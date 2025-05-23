import logging
import requests
import asyncio
import urllib.parse
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest, FloodWait
from libgen_api_enhanced import LibgenSearch

# Initialize LibgenSearch instance
lg = LibgenSearch()
logger = logging.getLogger(__name__)

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
        progress_msg = await message.reply("🔍 Searching Library Genesis...")
        
        try:
            results = lg.search_title_filtered(query, filters={}, exact_match=True)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            results = lg.search_title_filtered(query, filters={}, exact_match=True)
        
        if not results:
            return await progress_msg.edit("❌ No results found for your query.")

        # Store encoded query and index in callback data
        encoded_query = urllib.parse.quote(query)
        response = [f"📚 Found {len(results)} results for '{query}':"]
        buttons = []
        for idx, result in enumerate(results[:10], 1):
            title = result['Title'][:35] + "..." if len(result['Title']) > 35 else result['Title']
            callback_data = f"lgdl_{encoded_query}_{idx-1}"  # Store query and index
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
    """Handle LibGen download callbacks using index-based selection"""
    try:
        # Extract query and index from callback data
        data_parts = callback_query.data.split("_", 2)
        encoded_query = data_parts[1]
        index = int(data_parts[2])
        original_query = urllib.parse.unquote(encoded_query)
        
        await callback_query.answer("📥 Fetching download links...")
        
        # Re-run the original search to get fresh results
        try:
            results = lg.search_title_filtered(original_query, filters={}, exact_match=True)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            results = lg.search_title_filtered(original_query, filters={}, exact_match=True)
        
        if not results or index >= len(results):
            return await callback_query.message.reply("❌ Book details not found.")

        book = results[index]
        details = format_book_details(book)
        
        # Create download buttons
        buttons = []
        if book.get('Direct_Download_Link'):
            buttons.append(
                [InlineKeyboardButton("⬇️ Direct Download", url=book['Direct_Download_Link'])]
            )
            
        mirror_buttons = []
        for i in range(1, 6):
            if mirror_url := book.get(f'Mirror_{i}'):
                mirror_buttons.append(InlineKeyboardButton(f"Mirror {i}", url=mirror_url))
        
        if mirror_buttons:
            buttons.append(mirror_buttons)
        
        if book.get('Cover') and book['Cover'].startswith('http'):
            buttons.append([InlineKeyboardButton("🖼 Cover Image", url=book['Cover'])])

        await callback_query.message.reply(
            details,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=not bool(book.get('Cover')),
            parse_mode=enums.ParseMode.MARKDOWN
        )

    except BadRequest as e:
        logger.error(f"BadRequest error: {e}")
        await callback_query.answer("⚠️ Error showing details. Try another book.")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Error processing request")

def download_book(url, filename):
    """Synchronous download helper"""
    try:
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            return True
        return False
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False