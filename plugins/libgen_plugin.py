import logging
import asyncio
import urllib.parse
import os
import aiohttp
import aiofiles
from info import * 
from Script import *
from datetime import datetime, timedelta
from collections import defaultdict
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest, FloodWait
from libgen_api_enhanced import LibgenSearch

# Initialize LibgenSearch instance
lg = LibgenSearch()
logger = logging.getLogger(__name__)

# Concurrency control
USER_LOCKS = defaultdict(asyncio.Lock)
LAST_PROGRESS_UPDATE = defaultdict(lambda: (0, datetime.min))

def escape_markdown(text: str) -> str:
    """Custom markdown escaper for Pyrogram"""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in escape_chars else char for char in text)

def format_book_details(book):
    """Format book details in Markdown using LibGen's structure"""
    return (
        f"📚 **{escape_markdown(book['Title'])}**\n\n"
        f"👤 **Author:** {escape_markdown(book.get('Author', 'Unknown'))}\n"
        f"📅 **Year:** {escape_markdown(book.get('Year', 'N/A'))}\n"
        f"🌐 **Language:** {escape_markdown(book.get('Language', 'N/A'))}\n"
        f"📖 **Pages:** {escape_markdown(book.get('Pages', 'N/A'))}\n"
        f"📦 **Size:** {escape_markdown(book.get('Size', 'N/A'))}\n"
        f"📄 **Format:** {escape_markdown(book.get('Extension', 'N/A'))}\n"
        f"🖼️ **Cover:** {book.get('Cover', 'N/A')}\n\n"
        "**Download Links:**\n" + "\n".join(
            [f"🔗 [Mirror {i}]({book[f'Mirror_{i}']})" 
             for i in range(1, 6) if book.get(f'Mirror_{i}')]
        ) + 
        f"\n\n**Direct Download:** {book.get('Direct_Download_Link', 'N/A')}"
    )

@Client.on_message(filters.command('lgsearch') & filters.private)
async def handle_libgen_search(client, message):
    """Handle LibGen search requests"""
    try:
        query = message.text.split(' ', 1)[1]
        progress_msg = await message.reply("🔍 Searching in The Torrent Servers of Magical Library...")
        
        try:
            results = lg.search_title_filtered(query, filters={}, exact_match=True)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            results = lg.search_title_filtered(query, filters={}, exact_match=True)
        
        if not results:
            return await progress_msg.edit("❌ No results found for your query.")

        encoded_query = urllib.parse.quote(query)
        response = [f"📚 Found {len(results)} results for '{query}':"]
        buttons = []
        for idx, result in enumerate(results[:10], 1):
            title = result['Title'][:35] + "..." if len(result['Title']) > 35 else result['Title']
            callback_data = f"lgdl_{encoded_query}_{idx-1}"
            buttons.append(
                [InlineKeyboardButton(
                    f"{idx}. {title}",
                    callback_data=callback_data
                )]
            )

        await progress_msg.edit(
            "\n".join(response),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.MARKDOWN
        )

    except IndexError:
        await message.reply("⚠️ Please provide a search query!\nExample: `/lgsearch The Great Gatsby`", 
                          parse_mode=enums.ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"LibGen search error: {e}")
        await message.reply(f"❌ Error searching LibGen: {str(e)}")

@Client.on_callback_query(filters.regex(r"^lgdl_"))
async def handle_libgen_download(client, callback_query):
    """Handle LibGen download with concurrency control and progress fixes"""
    user_id = callback_query.from_user.id
    async with USER_LOCKS[user_id]:
        try:
            data_parts = callback_query.data.split("_", 2)
            encoded_query = data_parts[1]
            index = int(data_parts[2])
            original_query = urllib.parse.unquote(encoded_query)
            
            await callback_query.answer("📥 Starting download...")
            progress_msg = await callback_query.message.reply("⏳ Downloading book from server...")
            
            try:
                results = lg.search_title_filtered(original_query, filters={}, exact_match=True)
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
                results = lg.search_title_filtered(original_query, filters={}, exact_match=True)
            
            if not results or index >= len(results):
                await progress_msg.edit("❌ Book details not found.")
                return await asyncio.sleep(5).then(lambda _: progress_msg.delete())

            book = results[index]
            download_url = book.get('Direct_Download_Link')
            
            if not download_url:
                await progress_msg.edit("❌ No direct download available for this book.")
                return await asyncio.sleep(5).then(lambda _: progress_msg.delete())

            clean_title = "".join(c if c.isalnum() else "_" for c in book['Title'])
            file_ext = book.get('Extension', 'pdf')
            filename = f"{clean_title[:50]}.{file_ext}"
            temp_path = f"downloads/{filename}"
            os.makedirs("downloads", exist_ok=True)

            try:
                last_percent = -1
                await progress_msg.edit("⬇️ Downloading file... (0%)")
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(download_url) as response:
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
                                            await progress_msg.edit(
                                                f"⬇️ Downloading file... ({percent}%)"
                                            )
                                            last_percent = percent
                                            LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                                        except Exception as e:
                                            logger.warning(f"Progress update failed: {e}")

                await progress_msg.edit("📤 Uploading to Telegram...")
                
                async def progress(current, total):
                    percent = round(current * 100 / total)
                    now = datetime.now()
                    if percent != last_percent or now - LAST_PROGRESS_UPDATE[user_id][1] > timedelta(seconds=2):
                        try:
                            await progress_msg.edit(f"📤 Uploading... ({percent}%)")
                            LAST_PROGRESS_UPDATE[user_id] = (percent, now)
                        except Exception as e:
                            logger.warning(f"Upload progress update failed: {e}")

                # Send document and store message reference
                sent_msg = await client.send_document(
                    chat_id=callback_query.message.chat.id,
                    document=temp_path,
                    caption=f"📚 {book.get('Title', 'Unknown')}\n👤 Author: {book.get('Author', 'Unknown')}\n📦 Size: {book.get('Size', 'N/A')}",
                    progress=progress
                )

                # Send log to channel
                try:
                    await client.send_document(
                        LOG_CHANNEL,
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
                    
                except Exception as log_error:
                    logger.error(f"Failed to send log: {log_error}")

                # Auto-delete logic (modified)
                if AUTO_DELETE_TIME > 0:
                    deleter_msg = await callback_query.message.reply(
                        script.AUTO_DELETE_MSG.format(AUTO_DELETE_MIN)
                    )
                    
                    async def auto_delete_task():
                        await asyncio.sleep(AUTO_DELETE_TIME)
                        try:
                            await sent_msg.delete()
                            await deleter_msg.edit(script.FILE_DELETED_MSG)
                        except Exception as e:
                            logger.error(f"Auto-delete failed: {e}")
                    
                    asyncio.create_task(auto_delete_task())  # Start without waiting

                # Cleanup
                await progress_msg.delete()
                if os.path.exists(temp_path):
                    os.remove(temp_path)

            except Exception as e:
                logger.error(f"Download error: {e}")
                await progress_msg.edit(f"❌ Download failed: {str(e)}")
                await asyncio.sleep(5)
                await progress_msg.delete()
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except:
                        pass

        except Exception as e:
            logger.error(f"Callback error: {e}")
            await callback_query.answer("❌ Error processing request")