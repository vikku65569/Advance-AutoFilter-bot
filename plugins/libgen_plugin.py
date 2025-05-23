import logging
from pyrogram import Client, filters
from libgen_api_enhanced import LibgenSearch
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# Initialize the Libgen search client once
lg = LibgenSearch()

@Client.on_message(filters.command('lgsearch') & filters.private)
async def search_libgen(client: Client, message):
    """
    Search Library Genesis for books.
    Usage: /lgsearch <query>
    """
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply('⚠️ Usage: /lgsearch <book title or author>', quote=True)

    query = parts[1]
    await message.reply(f'🔍 Searching LibGen for "{query}"...')

    try:
        results = lg.search_title(query)
        if not results:
            return await message.reply('❌ No results found.', quote=True)

        # Build a keyboard of top 5 results
        buttons = []
        for item in results[:5]:
            # Use the Libgen ID for download command
            item_id = item.get('ID')
            title = item.get('Title')[:40] + ('...' if len(item.get('Title')) > 40 else '')
            buttons.append([InlineKeyboardButton(title, callback_data=f'lgget:{item_id}')])

        await message.reply(
            '✅ Found results. Tap to get download link:',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f'LibGen search error: {e}')
        await message.reply(f'❌ Error searching LibGen: {e}', quote=True)

@Client.on_callback_query(filters.regex(r'^lgget:(\d+)$'))
async def callback_libgen_get(client: Client, callback_query):
    """
    Handle button presses to fetch download link for a given Libgen ID.
    """
    lib_id = callback_query.matches[0].group(1)
    await callback_query.answer('Generating download link...')

    try:
        # Get details for this item by ID search
        details_list = lg.search_title_filtered('', {'ID': lib_id})
        if not details_list:
            return await callback_query.message.reply('❌ Could not retrieve book details.')
        item = details_list[0]
        # Resolve download links (returns dict of mirrors)
        links = lg.resolve_download_links(item)
        # Pick first direct download link
        ddl = links.get('Direct_Download_Link') or list(links.values())[0]

        text = (
            f"📚 <b>{item.get('Title')}</b>\n"
            f"👤 <i>{item.get('Author')}</i>\n"
            f"📖 {item.get('Pages')} pages | {item.get('Extension')} | {item.get('Size')}\n"
            f"🔗 <a href=\"{ddl}\">Download here</a>"
        )
        await callback_query.message.edit_text(text, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f'LibGen get error: {e}')
        await callback_query.message.reply(f'❌ Error fetching download link: {e}')
