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

async def upload_to_telegram(client, download_url: str, book: dict, progress_msg, chat_id: int, user_id: int):
    """Reusable Telegram uploader with direct URL streaming"""
    last_percent = -1
    
    async def progress(current, total):
        nonlocal last_percent
        percent = round(current * 100 / total)
        now = datetime.now()
        
        if total > 30*1024*1024:  # For large files
            mb_current = current//1024//1024
            mb_total = total//1024//1024
            message = f"📤 Streaming {mb_total}MB ({mb_current}MB)"
            
            if (mb_current - (last_percent * total // 100 // 1024 // 1024) >= 5) or \
               (now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=15)):
                try:
                    await progress_msg.edit(message)
                    last_percent = percent
                    LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                except Exception as e:
                    if "MESSAGE_NOT_MODIFIED" not in str(e):
                        logger.warning(f"Progress update failed: {e}")
        elif (percent != last_percent and percent - last_percent >= 2) or \
             (now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2)):
            try:
                await progress_msg.edit(f"📤 Uploading... ({percent}%)")
                last_percent = percent
                LAST_PROGRESS_UPDATE[user_id] = (percent, now)
            except Exception as e:
                logger.debug(f"Progress update skipped: {e}")

    try:
        return await client.send_document(
            chat_id=chat_id,
            document=download_url,
            caption=f"📚<b>{escape_markdown(book.get('Title', 'Unknown'))}</b>\n👤 Author: {escape_markdown(book.get('Author', 'Unknown'))}\n📦 Size: {escape_markdown(book.get('Size', 'N/A'))}",
            progress=progress
        )
    except FloodWait as e:
        await progress_msg.edit(f"⚠️ Flood wait: {e.value}s")
        await asyncio.sleep(e.value)
        return await upload_to_telegram(client, download_url, book, progress_msg, chat_id, user_id)


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


async def log_download(client, sent_msg, book, callback_query):
    """Log download using Telegram's file_id"""
    try:
        # Main log channel
        await client.send_document(
            int(LOG_CHANNEL),
            document=sent_msg.document.file_id,
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

        # File store channel
        raw_title = str(book.get('Title', '')).strip()
        clean_title = raw_title.lower().strip()
        
        if clean_title and clean_title != 'unknown' and not await db.is_title_exists(clean_title):
            await client.send_document(
                int(FILE_STORE_CHANNEL[0]),
                document=sent_msg.document.file_id
            )
            await db.add_file_title(clean_title)

    except Exception as log_error:
        logger.error(f"Logging failed: {log_error}", exc_info=True)


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
    """Handle download with direct URL streaming"""
    user_id = callback_query.from_user.id
    async with USER_LOCKS[user_id]:
        try:
            # Extract callback data
            data = callback_query.data.split('_')
            search_key = data[1]
            index = int(data[2])
            
            # Get cached results
            cached = search_cache.get(search_key)
            if not cached:
                await callback_query.answer("Session expired!")
                return

            # Validate index
            if index >= len(cached['results']):
                await callback_query.answer("Invalid selection!")
                return

            book = cached['results'][index]
            if not (download_url := book.get('Direct_Download_Link')):
                await callback_query.answer("❌ No direct download link")
                return

            # Start streaming process
            await callback_query.answer("🚀 Starting streaming...")
            progress_msg = await callback_query.message.reply("⚡ Streaming from Libgen servers...")

            try:
                # Stream directly to Telegram
                sent_msg = await upload_to_telegram(
                    client=client,
                    download_url=download_url,
                    book=book,
                    progress_msg=progress_msg,
                    chat_id=callback_query.message.chat.id,
                    user_id=user_id
                )

                # Post-processing
                await handle_auto_delete(client, sent_msg, callback_query.message.chat.id)
                await log_download(client, sent_msg, book, callback_query)
                await progress_msg.delete()

            except Exception as e:
                logger.error(f"Streaming error: {str(e)}", exc_info=True)
                await progress_msg.edit(f"❌ Streaming failed: {str(e)}")
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            await callback_query.answer("❌ Processing error")