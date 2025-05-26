import logging
import asyncio
import urllib.parse
import os
import aiohttp
import aiofiles
from uuid import uuid4
from info import *
from Script import *
from datetime import datetime, timedelta
from collections import defaultdict
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import BadRequest, FloodWait
from libgen_api_enhanced import LibgenSearch
from database.users_chats_db import db  # Assuming you have a database module for logging
import shutil

# Initialize LibgenSearch instance
lg = LibgenSearch()
logger = logging.getLogger(__name__)

# Concurrency control and cache
USER_LOCKS = defaultdict(asyncio.Lock)
LAST_PROGRESS_UPDATE = defaultdict(lambda: (0, datetime.min))
search_cache = {}

ACTIVE_DOWNLOADS = {}
RESULTS_PER_PAGE = 10

def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

async def libgen_search(query: str):
    """Reusable search function"""
    try:
        default_results = lg.search_default(query)
        filtered_results = lg.search_title_filtered(query, filters={}, exact_match=True)
        search_default_filtered = lg.search_default_filtered(query, filters={},exact_match=False)
        return filtered_results + default_results + search_default_filtered # Combine both result lists
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        return lg.search_title(query)

async def create_search_buttons(results: list, search_key: str, page: int):
    """Create paginated inline keyboard markup"""
    total = len(results)
    total_pages = (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE
    
    start_idx = (page - 1) * RESULTS_PER_PAGE
    end_idx = start_idx + RESULTS_PER_PAGE
    page_results = results[start_idx:end_idx]

    buttons = []
    for idx, result in enumerate(page_results, start=1):
        global_idx = start_idx + idx - 1
        title = result['Title'][:35] + "..." if len(result['Title']) > 35 else result['Title']
        callback_data = f"lgdl_{search_key}_{global_idx}"
        buttons.append([
            InlineKeyboardButton(
                f"{result['Extension'].upper()} ~{result['Size']} - {title}",
                callback_data=callback_data
            )
        ])

    # Pagination controls
    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton("⌫ Back", callback_data=f"lgpage_{search_key}_{page-1}"))
    pagination.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="pages"))
    if page < total_pages:
        pagination.append(InlineKeyboardButton("Next ➪", callback_data=f"lgpage_{search_key}_{page+1}"))
    
    if pagination:
        buttons.append(pagination)

    return InlineKeyboardMarkup(buttons)

async def download_libgen_file(url: str, temp_path: str, progress_msg, user_id: int):
    """Reusable file downloader with progress, retries, and cancellation"""
    last_percent = -1
    max_retries = 3
    retry_delay = 5
    message_valid = True  # Track message validity
    
    try:
        cancel_button = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel Download", callback_data=f"cancel_{user_id}")]])
        try:
            await progress_msg.edit("⬇️ Downloading file... (0%)", reply_markup=cancel_button)
        except Exception as e:
            logger.warning(f"Initial progress update failed: {e}")
            message_valid = False

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3600)) as session:
            for attempt in range(max_retries):
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise Exception(f"Download failed with status {response.status}")

                        total_size = int(response.headers.get('content-length', 0)) or None
                        downloaded = 0
                        
                        ACTIVE_DOWNLOADS[user_id] = {
                            'cancelled': False,
                            'path': temp_path,
                            'message': progress_msg
                        }
                        
                        async with aiofiles.open(temp_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(1024*1024*2):
                                if ACTIVE_DOWNLOADS.get(user_id, {}).get('cancelled', False):
                                    raise Exception("Download cancelled by user")
                                    
                                if not chunk:
                                    continue

                                # Write chunk
                                await f.write(chunk)
                                downloaded += len(chunk)
                                
                                if not message_valid:
                                    continue  # Skip updates if message is invalid
                                
                                # Progress updates
                                if total_size:
                                    current_time = datetime.now()
                                    percent = round((downloaded / total_size) * 100)
                                    
                                    # Update logic with message validity check
                                    try:
                                        if (percent != last_percent and percent - last_percent >= 1) or \
                                           (current_time - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2)):
                                            await progress_msg.edit(
                                                f"⬇️ Downloading file... ({percent}%)",
                                                reply_markup=cancel_button
                                            )
                                            last_percent = percent
                                            LAST_PROGRESS_UPDATE[user_id] = (percent, current_time)
                                    except BadRequest as e:
                                        if "MESSAGE_ID_INVALID" in str(e):
                                            message_valid = False
                                            logger.warning("Progress message became invalid, stopping updates")
                                        elif "MESSAGE_NOT_MODIFIED" not in str(e):
                                            logger.warning(f"Progress update failed: {e}")
                                    except Exception as e:
                                        logger.warning(f"Progress update failed: {e}")
                                    except BadRequest as e:
                                        if "MESSAGE_ID_INVALID" in str(e):
                                            message_valid = False
                                            logger.warning("Progress message became invalid, stopping updates")
                                        elif "MESSAGE_NOT_MODIFIED" not in str(e):
                                            logger.warning(f"Progress update failed: {e}")
                                    except Exception as e:
                                        logger.warning(f"Progress update failed: {e}")

                        return

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"Download attempt {attempt+1} failed: {str(e)}, retrying...")
                        await asyncio.sleep(retry_delay)
                        continue
                    raise
                finally:
                    ACTIVE_DOWNLOADS.pop(user_id, None)
                break
    except Exception as e:
        if message_valid:
            try:
                await progress_msg.edit(f"❌ Download failed: {str(e)[:100]}")
            except Exception as edit_error:
                logger.error(f"Failed to update progress message: {edit_error}")
        raise

# In your upload_to_telegram function, modify the progress callback:
async def upload_to_telegram(client, temp_path: str, book: dict, progress_msg, chat_id: int, user_id: int):
    """Reusable Telegram uploader with progress for large files"""
    last_percent = -1
    last_message = ""
    chunk_size = 1024*1024*2  # 2MB chunks
    
    async def progress(current, total):
        nonlocal last_percent, last_message
        percent = round(current * 100 / total)
        message = f"📤 Uploading... ({percent}%)"
        now = datetime.now()
        
        # Different update logic for large files
        if total > 30*1024*1024:  # For files >30MB
            mb_current = current//1024//1024
            mb_total = total//1024//1024
            message = f"📤 Uploading {mb_total}MB ({mb_current}MB sent)..."
            update_threshold = 5  # Update every 5MB or 15 seconds
            last_mb = last_percent * total // 100 // 1024 // 1024
            
            if (mb_current - last_mb >= update_threshold) or \
               (now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=15)):
                try:
                    await progress_msg.edit(message)
                    last_percent = percent
                    last_message = message
                    LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                except Exception as e:
                    if "MESSAGE_NOT_MODIFIED" not in str(e):
                        logger.warning(f"Upload progress failed: {e}")
        else:
            # Original logic for smaller files
            if (percent != last_percent and percent - last_percent >= 1) or \
               (now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2)):
                try:
                    await progress_msg.edit(message)
                    last_percent = percent
                    last_message = message
                    LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                except Exception as e:
                    if "MESSAGE_NOT_MODIFIED" not in str(e):
                        logger.warning(f"Upload progress failed: {e}")

    try:
        return await client.send_document(
            chat_id=chat_id,
            document=temp_path,
            caption=f"📚<b> {book.get('Title', 'Unknown')}</b>\n👤 <b> Author: </b> {book.get('Author', 'Unknown')}\n📦<b> Size:</b> {book.get('Size', 'N/A')}",
            progress=progress
        )
    except FloodWait as e:
        await progress_msg.edit(f"⚠️ Flood wait: Please wait {e.value} seconds")
        await asyncio.sleep(e.value)
        return await upload_to_telegram(client, temp_path, book, progress_msg, chat_id, user_id)

async def handle_auto_delete(client, sent_msg, chat_id: int):
    """Handle auto-delete functionality"""
    if AUTO_DELETE_TIME > 0:
        deleter_msg = await client.send_message(
            chat_id=chat_id,
            text=script.AUTO_DELETE_MSG.format(AUTO_DELETE_MIN),
            reply_to_message_id=sent_msg.id
        )
        
        async def auto_delete_task():
            await asyncio.sleep(AUTO_DELETE_TIME)
            try:
                await sent_msg.delete()
                await deleter_msg.edit(script.FILE_DELETED_MSG)
            except Exception as e:
                logger.error(f"Auto-delete failed: {e}")
        
        asyncio.create_task(auto_delete_task())


async def log_download(client, temp_path: str, book: dict, callback_query):
    """Log download to channels with title-based duplicate prevention"""
    try:
        # First send to regular log channel (original behavior)
        await client.send_document(
            int(LOG_CHANNEL),  # Ensure integer format
            document=temp_path,
            caption=(
                f"📥 User {callback_query.from_user.mention} downloaded:\n"
                f"📖 Title: {escape_markdown(book.get('Title', 'Unknown'))}\n"
                f"👤 Author: {escape_markdown(book.get('Author', 'Unknown'))}\n"
                f"📦 Size: {escape_markdown(book.get('Size', 'N/A'))}\n"
                f"👤 User ID: {callback_query.from_user.id}\n"
                f"🤖 Via: {client.me.first_name}"
            ),
            parse_mode=enums.ParseMode.HTML
        )

        # Get and clean title
        raw_title = str(book.get('Title', '')).strip()  # Convert to string
        clean_title = raw_title.lower().strip()
        
        logger.debug(f"Processing title: {clean_title}")  # Add debug logging
        
        if not clean_title or clean_title == 'unknown':
            logger.warning("Skipping invalid title for file store")
            return

        # Check if title exists in database
        if not await db.is_title_exists(clean_title):
            logger.info(f"New title detected: {clean_title}")
            SINGle_FILE_STORE_CHANNEL = FILE_STORE_CHANNEL[0]
            # Send to file store channel with explicit chat ID conversion
            await client.send_document(
                int(SINGle_FILE_STORE_CHANNEL),  # Convert to integer
                document=temp_path
            )
            
            # Store title in database
            await db.add_file_title(clean_title)
            logger.info(f"Title stored: {clean_title}")
        else:
            logger.debug(f"Duplicate title skipped: {clean_title}")

    except Exception as log_error:
        logger.error(f"Failed to handle file logging: {log_error}", exc_info=True)

@Client.on_message(filters.command('search') & filters.private)
async def handle_search_command(client, message):
    """Handle /search command with pagination"""
    try:
        query = message.text.split(' ', 1)[1]
        progress_msg = await message.reply("🔍 Searching in The Torrent Servers of Magical Library...")
        
        results = await libgen_search(query)
        if not results:
            return await progress_msg.edit("❌ No results found for your query.")

        # Store results in cache
        search_key = str(uuid4())
        search_cache[search_key] = {
            'results': results,
            'query': query,
            'time': datetime.now()
        }

        total = len(results)
        buttons = await create_search_buttons(results, search_key, 1)
        
        response = [
            f"📚<b> Found </b> {total} results for <b>{query}</b>:",
            f"Rᴇǫᴜᴇsᴛᴇᴅ Bʏ : {message.from_user.mention if message.from_user else 'Unknown User'}",
            f"Sʜᴏᴡɪɴɢ ʀᴇsᴜʟᴛs ғʀᴏᴍ ᴛʜᴇ Mᴀɢɪᴄᴀʟ Lɪʙʀᴀʀʏ ᴏғ Lɪʙʀᴀʀʏ Gᴇɴᴇsɪs"
        ]

        await progress_msg.edit(
            "\n".join(response),
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )

    except IndexError:
        await message.reply("⚠️ Please provide a search query!\nExample: /search The Great Gatsby", 
                          parse_mode=enums.ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Search error: {e}")
        await message.reply(f"❌ Search failed: {str(e)}")

@Client.on_callback_query(filters.regex(r"^lgpage_"))
async def handle_pagination(client, callback_query):
    """Handle pagination callbacks"""
    try:
        data = callback_query.data.split('_')
        search_key = data[1]
        page = int(data[2])
        
        cached = search_cache.get(search_key)
        if not cached:
            await callback_query.answer("Search session expired!")
            return

        results = cached['results']
        query = cached['query']
        total = len(results)
        total_pages = (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE

        if page < 1 or page > total_pages:
            await callback_query.answer("Invalid page!")
            return

        buttons = await create_search_buttons(results, search_key, page)
        
        response = [
            f"📚 Found {total} results for <b>{query}</b>:",
            f"Rᴇǫᴜᴇsᴛᴇᴅ Bʏ ☞ {callback_query.from_user.mention}",
            f"Sʜᴏᴡɪɴɢ ʀᴇsᴜʟᴛs ғʀᴏᴍ ᴛʜᴇ Mᴀɢɪᴄᴀʟ Lɪʙʀᴀʀʏ"
        ]

        await callback_query.message.edit(
            "\n".join(response),
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )
        await callback_query.answer()
    except Exception as e:
        logger.error(f"Pagination error: {e}")
        await callback_query.answer("Error handling pagination!")

@Client.on_callback_query(filters.regex(r"^cancel_"))
async def handle_cancel_download(client, callback_query: CallbackQuery):
    user_id = int(callback_query.data.split('_')[1])
    
    if user_id in ACTIVE_DOWNLOADS:
        ACTIVE_DOWNLOADS[user_id]['cancelled'] = True
        temp_path = ACTIVE_DOWNLOADS[user_id]['path']
        
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                logger.error(f"Failed to delete cancelled file: {e}")
        
        try:
            await callback_query.answer("Download cancelled!", show_alert=False)
        except Exception as e:
            logger.error(f"Cancel answer error: {e}")
    else:
        try:
            await callback_query.answer("No active download to cancel", show_alert=False)
        except Exception as e:
            logger.error(f"Cancel answer error: {e}")


@Client.on_callback_query(filters.regex(r"^lgdl_"))
async def handle_download_callback(client, callback_query):
    """Handle download callback queries"""
    user_id = callback_query.from_user.id
    async with USER_LOCKS[user_id]:
        progress_msg = None
        try:
            data = callback_query.data.split('_')
            search_key = data[1]
            index = int(data[2])
            
            cached = search_cache.get(search_key)
            if not cached:
                await callback_query.answer("Session expired! Please search again")
                return

            results = cached['results']
            if index >= len(results):
                await callback_query.answer("Invalid selection!")
                return

            book = results[index]
            if not (download_url := book.get('Direct_Download_Link')):
                await callback_query.answer("❌ No direct download available")
                return

            await callback_query.answer("📥 Starting download...")
            try:
                progress_msg = await callback_query.message.reply("⏳ Downloading book from server...")
            except Exception as e:
                logger.error(f"Failed to create progress message: {e}")
                return

            # File handling
            clean_title = "".join(c if c.isalnum() else "_" for c in book['Title'])
            file_ext = book.get('Extension', 'pdf')
            filename = f"{clean_title[:50]}.{file_ext}"
            temp_path = f"downloads/{filename}"
            os.makedirs("downloads", exist_ok=True)

            try:
                await download_libgen_file(
                    url=download_url,
                    temp_path=temp_path,
                    progress_msg=progress_msg,
                    user_id=user_id
                )

                if ACTIVE_DOWNLOADS.get(user_id, {}).get('cancelled', False):
                    raise Exception("Download cancelled by user")

                try:
                    await progress_msg.edit("📤 Uploading to Telegram...")
                except BadRequest as e:
                    if "MESSAGE_ID_INVALID" in str(e):
                        logger.warning("Progress message invalid, creating new one")
                        progress_msg = await callback_query.message.reply("📤 Uploading to Telegram...")

                sent_msg = await upload_to_telegram(
                    client=client,
                    temp_path=temp_path,
                    book=book,
                    progress_msg=progress_msg,
                    chat_id=callback_query.message.chat.id,
                    user_id=user_id
                )

                await handle_auto_delete(client, sent_msg, callback_query.message.chat.id)
                await log_download(client, temp_path, book, callback_query)
                try:
                    await progress_msg.delete()
                except Exception as e:
                    logger.warning(f"Failed to delete progress message: {e}")

            except Exception as e:
                error_msg = f"❌ {'Download cancelled' if 'cancelled' in str(e) else 'Error'}: {str(e)[:100]}"
                try:
                    if progress_msg:
                        await progress_msg.edit(error_msg)
                    else:
                        await callback_query.message.reply(error_msg)
                except BadRequest as e:
                    if "MESSAGE_ID_INVALID" in str(e):
                        try:
                            await callback_query.message.reply(error_msg)
                        except Exception as e:
                            logger.error(f"Failed to send error message: {e}")
                except Exception as e:
                    logger.error(f"Failed to send error message: {e}")
                
                logger.error(f"Download error: {str(e)}", exc_info=True)
            
            finally:
                ACTIVE_DOWNLOADS.pop(user_id, None)
                if os.path.exists(temp_path):
                    try: 
                        os.remove(temp_path)
                    except Exception as e:
                        logger.error(f"Failed to remove temp file: {e}")

        except Exception as e:
            logger.error(f"Callback error: {e}")
            try:
                await callback_query.answer("❌ Error processing request", show_alert=False)
            except Exception as e:
                logger.error(f"Failed to answer callback: {e}")
            if progress_msg:
                try:
                    await progress_msg.edit("❌ Processing failed due to unexpected error")
                except Exception as e:
                    logger.error(f"Failed to update progress message: {e}")