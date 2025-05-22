import logging
import re
from pyrogram import Client, filters
from pyrogram.errors import RPCError, PeerIdInvalid
from info import ADMINS, LOG_CHANNEL
from pyrogram.types import Message

logger = logging.getLogger(__name__)

# --------------------- ADMIN TO USER ---------------------
@Client.on_message(filters.command('message') & filters.user(ADMINS))
async def admin_send_message(client: Client, message: Message):
    """
    Admin command:
      /message <user_id|@username> <text>
    """
    parts = message.text.split(None, 2)
    if len(parts) < 3:
        return await message.reply("⚠️ Usage: `/message <user_id|@username> <text>`", quote=True)

    target, text = parts[1], parts[2]

    # Resolve ID or username
    try:
        user_id = int(target)
    except ValueError:
        username = target.lstrip('@')
        try:
            user = await client.get_users(username)
            user_id = user.id
        except PeerIdInvalid:
            return await message.reply(f"❌ Couldn’t find a user @{username}.", quote=True)
        except RPCError as e:
            logger.error(f"Error resolving @{username}: {e}")
            return await message.reply(f"❌ Error looking up @{username}: `{e}`", quote=True)

    try:
        await client.send_message(chat_id=user_id, text=text)
        # Tag message so replies can be routed
        await message.reply(f"✅ Message sent to `{user_id}`! Use this message to reply back.", quote=True)
    except RPCError as e:
        logger.error(f"Failed to send message to {user_id}: {e}")
        await message.reply(f"❌ Could not deliver message to `{user_id}`. Error: `{e}`", quote=True)


# --------------------- USER TO ADMIN ---------------------
@Client.on_message(filters.command('message') & filters.private & ~filters.user(ADMINS))
async def user_send_message(client: Client, message: Message):
    """
    User command:
      /message <text>
    """
    parts = message.text.split(None, 1)
    if len(parts) < 2:
        return await message.reply("⚠️ Usage: `/message <text>`", quote=True)

    text = parts[1]
    user = message.from_user
    # Build metadata
    header = (
        f"📩 #message <b>From User</b>\n"
        f"👤 Name: {user.first_name or ''} {user.last_name or ''}\n"
        f"🆔 User ID: `{user.id}` #UID{user.id}#\n"
        f"📱 Username: @{user.username or 'N/A'}\n"
        f"⏰ Time: {message.date.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "➖➖➖➖➖➖➖\n"
    )
    try:
        # send header and message to LOG_CHANNEL
        await client.send_message(LOG_CHANNEL, header + text)
        await message.reply("✅ Your message has been sent to the admins.", quote=True)
    except Exception as e:
        logger.error(f"Failed to forward user message: {e}")
        await message.reply("❌ Could not send your message. Please try again later.", quote=True)


# --------------------- ADMIN REPLY TO USER ---------------------
def extract_user_id(text: str) -> int:
    # catch #UID12345# pattern
    match = re.search(r"#UID(\d+)#", text)
    return int(match.group(1)) if match else None

@Client.on_message(filters.chat(LOG_CHANNEL) & filters.reply)
async def reply_to_user(client: Client, message: Message):
    """
    Routes admin replies back to the original user.
    """
    replied = message.reply_to_message
    user_id = extract_user_id(replied.text or '')
    if not user_id:
        return await message.reply_text("❌ Couldn't find user ID in the original message.", quote=True)

    # Prepare reply text
    resp = message.text or ''
    try:
        # send admin reply to user
        await client.send_message(chat_id=user_id, text=f"📬 <b>Admin Reply:</b>\n{resp}")
        await message.reply_text(f"✅ Reply sent to user `{user_id}`.", quote=True)
    except RPCError as e:
        logger.error(f"Failed to reply to user {user_id}: {e}")
        await message.reply_text(f"❌ Delivery failed: {e}", quote=True)
