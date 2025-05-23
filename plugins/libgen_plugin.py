import logging
from pyrogram import Client, filters
from pyrogram.utils import escape_markdown
from libgen_api_enhanced import LibgenSearch
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import BadRequest
from pyrogram.helpers import escape_markdown

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
        results = lg.search_title(query)
        
        if not results:
            return await msg.edit_text("❌ No results found for your query.")

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
        await callback_query.answer("📖 Fetching book details...")
        
        results = lg.search_title_filtered(libgen_id, {"ID": libgen_id})
        if not results:
            return await callback_query.message.reply("❌ Book details not found.")

        book = results[0]
        
        details = (
            f"📚 **{escape_markdown(book['Title'])}**\n\n"
            f"👤 **Author:** {escape_markdown(book.get('Author', 'Unknown'))}\n"
            f"📅 **Year:** {escape_markdown(book.get('Year', 'N/A'))}\n"
            f"🌐 **Language:** {escape_markdown(book.get('Language', 'N/A'))}\n"
            f"📖 **Pages:** {escape_markdown(book.get('Pages', 'N/A'))}\n"
            f"📦 **Size:** {escape_markdown(book.get('Size', 'N/A'))}\n"
            f"📄 **Format:** {escape_markdown(book.get('Extension', 'N/A'))}\n"
            f"🏷️ **ISBN:** {escape_markdown(book.get('ISBN', 'N/A'))}\n"
            f"🖼️ **Cover:** {book.get('Cover', 'N/A')}\n\n"
        )

        links = []
        if book.get('Direct_Download_Link'):
            links.append(f"🔗 [Direct Download]({book['Direct_Download_Link']})")
            
        for i in range(1, 6):
            if mirror := book.get(f'Mirror_{i}'):
                links.append(f"🔗 [Mirror {i}]({mirror})")

        buttons = []
        if links:
            details += "**Download Links:**\n" + "\n".join(links)
            buttons.append([InlineKeyboardButton(
                "⬇️ Direct Download", 
                url=book.get('Direct_Download_Link', '')
            )])

        if book.get('Cover'):
            buttons.append([InlineKeyboardButton("🖼 Cover Image", url=book['Cover'])])

        await callback_query.message.edit_text(
            details,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
            disable_web_page_preview=not bool(book.get('Cover')),
            parse_mode="Markdown"
        )

    except BadRequest as e:
        logger.error(f"BadRequest error: {e}")
        await callback_query.answer("⚠️ Error showing details. Try another book.")
    except Exception as e:
        logger.error(f"Callback error: {e}")
        await callback_query.answer("❌ Error processing request")