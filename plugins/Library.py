import logging
import asyncio
import urllib.parse
import json
from uuid import uuid4
from info import *
from Script import *
from datetime import datetime, timedelta
from collections import defaultdict
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest, FloodWait
from libgen_api_enhanced import LibgenSearch
from database.users_chats_db import db
import aiohttp

# Initialize LibgenSearch instance
lg = LibgenSearch()
logger = logging.getLogger(__name__)

# Concurrency control and cache
USER_LOCKS = defaultdict(asyncio.Lock)
search_cache = {}
RESULTS_PER_PAGE = 10

def escape_markdown(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

async def libgen_search(query: str):
    """Reusable search function with error handling"""
    try:
        try:
            default_results = lg.search_default(query)
            filtered_results = lg.search_title_filtered(query, filters={}, exact_match=True)
            search_default_filtered = lg.search_default_filtered(query, filters={}, exact_match=False)
            return filtered_results + default_results + search_default_filtered
        except json.JSONDecodeError:
            return lg.search_title(query)
    except Exception as e:
        logger.error(f"Libgen search error: {str(e)}")
        return None

async def process_libgen_url(url: str) -> str:
    """Fix Libgen URL encoding issues"""
    # Decode URL components first
    decoded_url = urllib.parse.unquote(url)
    # Re-encode special characters except /
    return urllib.parse.quote(decoded_url, safe="/:")

async def validate_download_url(url: str):
    """Validate if URL is accessible and contains valid content"""
    processed_url = await process_libgen_url(url)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Referer": "https://libgen.rs/",  # Add required Referer header
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            # Use GET instead of HEAD to better simulate browser behavior
            async with session.get(
                processed_url,
                headers=headers,
                timeout=15,
                allow_redirects=True,
                verify_ssl=False
            ) as response:
                if response.status != 200:
                    return False, processed_url
                
                # Check content type and content existence
                content_type = response.headers.get('Content-Type', '').lower()
                if not any(x in content_type for x in ['pdf', 'epub', 'octet-stream']):
                    return False, processed_url
                
                # Verify actual content is present
                content = await response.content.read(1024)
                if not content:
                    return False, processed_url
                
                return True, processed_url
        except Exception as e:
            logger.error(f"URL validation failed: {str(e)}")
            return False, processed_url

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

    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton("⌫ Back", callback_data=f"lgpage_{search_key}_{page-1}"))
    pagination.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="pages"))
    if page < total_pages:
        pagination.append(InlineKeyboardButton("Next ➪", callback_data=f"lgpage_{search_key}_{page+1}"))
    
    if pagination:
        buttons.append(pagination)

    return InlineKeyboardMarkup(buttons)

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

async def log_download(client, download_url: str, book: dict, callback_query):
    """Log download to channels with title-based duplicate prevention"""
    try:
        await client.send_document(
            int(LOG_CHANNEL),
            document=download_url,
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

        raw_title = str(book.get('Title', '')).strip()
        clean_title = raw_title.lower().strip()
        
        if not clean_title or clean_title == 'unknown':
            return

        if not await db.is_title_exists(clean_title):
            SINGle_FILE_STORE_CHANNEL = FILE_STORE_CHANNEL[0]
            await client.send_document(
                int(SINGle_FILE_STORE_CHANNEL),
                document=download_url
            )
            await db.add_file_title(clean_title)

    except Exception as log_error:
        logger.error(f"Failed to handle file logging: {log_error}", exc_info=True)

@Client.on_message(filters.command('search') & filters.private)
async def handle_search_command(client, message):
    """Handle /search command with pagination"""
    try:
        query = message.text.split(' ', 1)[1]
        if len(query) < 3:
            return await message.reply("❌ Search query too short (min 3 characters)")
            
        progress_msg = await message.reply("🔍 Searching in The Torrent Servers of Magical Library...")
        
        results = await libgen_search(query)
        if not results:
            return await progress_msg.edit("❌ Service unavailable. Please try again later.")

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
        await message.reply("❌ Search failed due to server issues. Please try again later.")

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
    """Handle download callback queries with improved validation"""
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

            # Use the URL processing and validation
            is_valid, processed_url = await validate_download_url(download_url)
            if not is_valid:
                await callback_query.answer("❌ Invalid download link")
                return

            await callback_query.answer("📥 Starting download...")
            progress_msg = await callback_query.message.reply("⏳ Verifying file availability...")

            try:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        sent_msg = await client.send_document(
                            chat_id=callback_query.message.chat.id,
                            document=processed_url,
                            caption=f"📚<b> {book.get('Title', 'Unknown')}</b>\n👤 <b> Author: </b> {book.get('Author', 'Unknown')}\n📦<b> Size:</b> {book.get('Size', 'N/A')}",
                            # Add browser-like headers and filename
                            file_name=f"{book.get('Title', 'file')[:40]}.{book.get('Extension', 'pdf')}",
                            headers={
                                "Referer": "https://libgen.rs/",
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                            }
                        )
                        break
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except TimeoutError:
                        if attempt == max_retries - 1:
                            raise
                        await asyncio.sleep(2 ** attempt)
                
                await handle_auto_delete(client, sent_msg, callback_query.message.chat.id)
                await log_download(client, processed_url, book, callback_query)
                await progress_msg.delete()

            except Exception as e:
                error_msg = "Failed to send file: "
                if "WEBPAGE_CURL_FAILED" in str(e):
                    error_msg += "Libgen server blocked Telegram. Try another mirror."
                elif "WEBPAGE_MEDIA_EMPTY" in str(e):
                    error_msg += "File format not supported by Telegram."
                else:
                    error_msg += str(e)
                
                await progress_msg.edit(f"❌ {error_msg}")
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Callback error: {e}")
            await callback_query.answer("❌ Error processing request")