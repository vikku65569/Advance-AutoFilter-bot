from __future__ import unicode_literals

import os, requests, asyncio, math, time, wget
from pyrogram import filters, Client
from pyrogram.types import Message
from info import *
from youtube_search import YoutubeSearch
from youtubesearchpython import SearchVideos
from yt_dlp import YoutubeDL

# Adjust this to match your bot’s maximum file size (bytes)
TG_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# common yt-dlp options
YDL_OPTS_AUDIO = {
    "format": "bestaudio/best",
    "outtmpl": "%(id)s.%(ext)s",
    "quiet": True,
    "no_warnings": True,
    "postprocessors": [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
    ],
}
YDL_OPTS_VIDEO = {
    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best",
    "outtmpl": "%(id)s.%(ext)s",
    "quiet": True,
    "no_warnings": True,
    "merge_output_format": "mp4",
}

async def run_ydl(opts: dict, url: str) -> str:
    """Run yt_dlp with given opts on URL, return path to downloaded file."""
    loop = asyncio.get_event_loop()
    # run in executor to avoid blocking
    return await loop.run_in_executor(None, _download_sync, opts, url)

def _download_sync(opts: dict, url: str) -> str:
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # if postprocessor changed extension, fix filename
        if "requested_formats" in info and info.get("ext"):
            base, _ = os.path.splitext(filename)
            filename = f"{base}.{info['ext']}"
        return filename

@Client.on_message(filters.private & filters.command("song"))
async def song_handler(client: Client, message: Message):
    """Download audio from a YouTube URL and send it as mp3."""
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/song <YouTube URL>`", parse_mode="markdown")

    url = message.command[1]
    msg = await message.reply_text("🔎 Searching for best audio...", quote=True)

    try:
        # create a temp dir for this download
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            path = await run_ydl(YDL_OPTS_AUDIO, url)

            size = os.path.getsize(path)
            if size > TG_MAX_FILE_SIZE:
                return await msg.edit_text(f"❌ File is too large ({size/1024/1024:.1f} MB).")

            await client.send_audio(
                chat_id=message.chat.id,
                audio=path,
                title=os.path.basename(path),
                caption="Here’s your MP3 😉",
                timeout=120,
                quote=False
            )
    except Exception as e:
        await msg.edit_text(f"⚠ Failed to download audio:\n`{e}`")
    else:
        await msg.delete()

@Client.on_message(filters.private & filters.command("video"))
async def video_handler(client: Client, message: Message):
    """Download video from a YouTube URL and send it as mp4."""
    if len(message.command) < 2:
        return await message.reply_text("Usage: `/video <YouTube URL>`", parse_mode="markdown")

    url = message.command[1]
    msg = await message.reply_text("🔎 Fetching best video...", quote=True)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            path = await run_ydl(YDL_OPTS_VIDEO, url)

            size = os.path.getsize(path)
            if size > TG_MAX_FILE_SIZE:
                return await msg.edit_text(f"❌ File is too large ({size/1024/1024:.1f} MB).")

            await client.send_video(
                chat_id=message.chat.id,
                video=path,
                caption="Enjoy your video 🎬",
                timeout=120,
                supports_streaming=True,
                quote=False
            )
    except Exception as e:
        await msg.edit_text(f"⚠ Failed to download video:\n`{e}`")
    else:
        await msg.delete()