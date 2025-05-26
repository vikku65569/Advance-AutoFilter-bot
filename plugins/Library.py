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
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest, FloodWait
from libgen_api_enhanced import LibgenSearch
from database.users_chats_db import db  # Assuming you have a database module for logging

# Initialize LibgenSearch instance
lg = LibgenSearch()
logger = logging.getLogger(__name__)

# Concurrency control and cache
USER_LOCKS = defaultdict(asyncio.Lock)
LAST_PROGRESS_UPDATE = defaultdict(lambda: (0, datetime.min))
search_cache = {}
RESULTS_PER_PAGE = 10

def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
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
    """Reusable file downloader with progress"""
    last_percent = -1
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"Download failed with status {response.status}")

            total_size = int(response.headers.get('content-length', 0)) or None
            downloaded = 0
            
            async with aiofiles.open(temp_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(1024*1024):
                    if not chunk:
                        continue
                    await f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size:
                        percent = round((downloaded / total_size) * 100)
                        now = datetime.now()
                        
                        if percent != last_percent and (
                            percent - last_percent >= 1 or 
                            now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2)
                        ):
                            try:
                                await progress_msg.edit(f"⬇️ Downloading file... ({percent}%)")
                                last_percent = percent
                                LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                            except Exception as e:
                                logger.warning(f"Progress update failed: {e}")

async def upload_to_telegram(client, temp_path: str, book: dict, progress_msg, chat_id: int, user_id: int):
    """Reusable Telegram uploader with progress"""
    last_percent = -1
    
    async def progress(current, total):
        nonlocal last_percent
        percent = round(current * 100 / total)
        now = datetime.now()
        if percent != last_percent or now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2):
            try:
                await progress_msg.edit(f"📤 Uploading... ({percent}%)")
                LAST_PROGRESS_UPDATE[user_id] = (percent, now)
            except Exception as e:
                logger.warning(f"Upload progress update failed: {e}")

    return await client.send_document(
        chat_id=chat_id,
        document=temp_path,
        caption=f"📚<b> {book.get('Title', 'Unknown')}</b>\n👤 <b> Author: </b> {book.get('Author', 'Unknown')}\n📦<b> Size:</b> {book.get('Size', 'N/A')}",
        progress=progress
    )

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
                # await sent_msg.delete()
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
        await message.reply("⚠️ Please provide a search query!\nExample: `/search The Great Gatsby`", 
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

@Client.on_callback_query(filters.regex(r"^lgdl_"))
async def handle_download_callback(client, callback_query):
    """Handle download callback queries"""
    user_id = callback_query.from_user.id
    async with USER_LOCKS[user_id]:
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
            progress_msg = await callback_query.message.reply("⏳ Downloading book from server...")

            # File handling
            clean_title = "".join(c if c.isalnum() else "_" for c in book['Title'])
            file_ext = book.get('Extension', 'pdf')
            filename = f"{clean_title[:50]}.{file_ext}"
            temp_path = f"downloads/{filename}"
            os.makedirs("downloads", exist_ok=True)

            try:
                await progress_msg.edit("⬇️ Downloading file... (0%)")
                await download_libgen_file(
                    url=download_url,
                    temp_path=temp_path,
                    progress_msg=progress_msg,
                    user_id=user_id
                )

                await progress_msg.edit("📤 Uploading to Telegram...")
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
                # await progress_msg.delete()

            except Exception as e:
                logger.error(f"Download error: {e}")
                await progress_msg.edit(f"❌ Download failed: {str(e)}")
                await asyncio.sleep(5)
            
            finally:
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass

        except Exception as e:
            logger.error(f"Callback error: {e}")
            await callback_query.answer("❌ Error processing request")