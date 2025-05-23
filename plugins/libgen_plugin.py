import logging
from pyrogram import Client, filters
from libgen_api_enhanced import LibgenSearch
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest

logger = logging.getLogger(__name__)
lg = LibgenSearch()

@Client.on_message(filters.command('lgsearch') & filters.private)
async def search_libgen(client: Client, message):
    """Search Library Genesis and display results with inline buttons"""
    try:
        query = message.text.split(' ', 1)[1]
    except IndexError:
        return await message.reply("⚠️ Please provide a search query.\nExample: `/lgsearch The Great Gatsby`", parse_mode="markdown")

    try:
        msg = await message.reply("🔍 Searching Library Genesis...")
        results = lg.search_title(query)  # You can also use search_author
        
        if not results:
            return await msg.edit_text("❌ No results found for your query.")

        # Prepare buttons for first 5 results
        buttons = []
        for result in results[:5]:
            title = f"{result['Title'][:30]}..." if len(result['Title']) > 30 else result['Title']
            author = result['Author'][:15] if result['Author'] else "Unknown"
            btn_text = f"{title} - {author}"
            buttons.append(
                [InlineKeyboardButton(btn_text, callback_data=f"lgdl_{result['ID']}")]
            )

        await msg.edit_text(
            f"📚 Found {len(results)} results for '{query}':",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"Search error: {e}")
        await message.reply(f"❌ Error searching LibGen: {str(e)}")

@Client.on_callback_query(filters.regex(r"^lgdl_"))
async def handle_download_request(client, callback_query):
    """Handle download requests from inline buttons"""
    try:
        libgen_id = callback_query.data.split("_", 1)[1]
        await callback_query.answer("Fetching download links...")
        
        # Get exact match using ID
        results = lg.search_title_filtered(libgen_id, {"ID": libgen_id})
        if not results:
            return await callback_query.message.reply("❌ Book details not found.")

        book = results[0]
        download_links = [
            book.get("Direct_Download_Link"),
            *[book[f"Mirror_{i}"] for i in range(1, 6) if book.get(f"Mirror_{i}")]
        ]

        # Create message with all valid links
        links_text = "\n".join(
            [f"🔗 [Download Mirror {i+1}]({link})" 
             for i, link in enumerate(download_links) if link]
        )
        
        response_text = (
            f"📖 **{book['Title']}**\n"
            f"👤 Author: {book.get('Author', 'Unknown')}\n"
            f"📅 Year: {book.get('Year', 'N/A')}\n"
            f"📄 Format: {book.get('Extension', 'N/A')}\n\n"
            f"{links_text}"
        )

        # Send as a new message to preserve original results
        await callback_query.message.reply(
            response_text,
            disable_web_page_preview=True,
            parse_mode="markdown"
        )
    except BadRequest as e:
        logger.error(f"BadRequest error: {e}")
        await callback_query.answer("Error generating links. Try another book.")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Error processing request")
