import logging
import requests
from pyrogram import Client, filters
from libgen_api_enhanced import LibgenSearch
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest

logger = logging.getLogger(__name__)
lg = LibgenSearch()

def format_book_details(result):
    """Format book details in a readable format"""
    return (
        f"📚 **Title:** {result['Title']}\n"
        f"👤 **Author:** {result.get('Author', 'N/A')}\n"
        f"📅 **Year:** {result.get('Year', 'N/A')}\n"
        f"🌐 **Language:** {result.get('Language', 'N/A')}\n"
        f"📄 **Format:** {result.get('Extension', 'N/A')}\n"
        f"📦 **Size:** {result.get('Size', 'N/A')}\n"
        f"🔗 **Direct Download:** {result.get('Direct_Download_Link', 'N/A')}\n"
        f"🖼 **Cover:** {result.get('Cover', 'N/A')}\n"
        "\n**Mirrors:**\n" + "\n".join(
            [f"Mirror {i}: {result[f'Mirror_{i}']}" 
             for i in range(1, 6) if result.get(f'Mirror_{i}')]
        )
    )

@Client.on_message(filters.command('lgsearch') & filters.private)
async def search_libgen(client: Client, message):
    """Handle search command without filters"""
    try:
        query = message.text.split(' ', 1)[1]
    except IndexError:
        return await message.reply("⚠️ Please provide a search query.\nExample: `/lgsearch The Great Gatsby`", parse_mode="markdown")

    try:
        msg = await message.reply("🔍 Searching Library Genesis...")
        
        # Search with empty filters and exact match
        results = lg.search_title_filtered(query, filters={}, exact_match=True)
        
        if not results:
            return await msg.edit_text("❌ No results found for your query.")

        # Format results listing
        response = [f"📚 Found {len(results)} results:"]
        for idx, result in enumerate(results[:10], 1):
            entry = (
                f"\n{idx}. **{result['Title'][:50]}**\n"
                f"   👤 {result.get('Author', 'Unknown')} | "
                f"📅 {result.get('Year', 'N/A')} | "
                f"📄 {result.get('Extension', 'N/A')}"
            )
            response.append(entry)

        # Add download buttons for first 10 results
        buttons = []
        for idx, result in enumerate(results[:10], 1):
            buttons.append(
                [InlineKeyboardButton(
                    f"📖 {idx}. {result['Title'][:30]}", 
                    callback_data=f"lgdl_{result['ID']}"
                )]
            )

        await msg.edit_text(
            "\n".join(response),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="markdown"
        )

    except Exception as e:
        logger.error(f"Search error: {e}")
        await message.reply(f"❌ Error searching LibGen: {str(e)}")

@Client.on_callback_query(filters.regex(r"^lgdl_"))
async def handle_download_request(client, callback_query):
    """Handle download requests"""
    try:
        libgen_id = callback_query.data.split("_", 1)[1]
        await callback_query.answer("📥 Fetching download links...")
        
        results = lg.search_title_filtered(libgen_id, {"ID": libgen_id})
        if not results:
            return await callback_query.message.reply("❌ Book details not found.")

        book = results[0]
        details = format_book_details(book)
        
        # Create download buttons
        buttons = []
        if book.get('Direct_Download_Link'):
            buttons.append(
                [InlineKeyboardButton("⬇️ Direct Download", url=book['Direct_Download_Link'])]
            )
        
        # Add mirror buttons
        mirror_buttons = []
        for i in range(1, 6):
            if mirror := book.get(f'Mirror_{i}'):
                mirror_buttons.append(
                    InlineKeyboardButton(f"Mirror {i}", url=mirror)
                )
        if mirror_buttons:
            buttons.append(mirror_buttons)

        await callback_query.message.reply(
            details,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
            parse_mode="markdown"
        )

    except BadRequest as e:
        logger.error(f"BadRequest error: {e}")
        await callback_query.answer("⚠️ Error showing details. Try another book.")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Error processing request")

def download_book(url, filename):
    """Utility function for direct downloads"""
    try:
        response = requests.get(url, stream=True)
        if response.ok:
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            return True
        return False
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False