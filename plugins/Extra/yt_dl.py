from __future__ import unicode_literals
import os
import requests
import asyncio
import logging
from pyrogram import filters, Client
from pyrogram.types import Message
from youtube_search import YoutubeSearch
from youtubesearchpython import SearchVideos
from yt_dlp import YoutubeDL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
SEARCH_TIMEOUT = 30  # seconds
DOWNLOAD_TIMEOUT = 300  # seconds
MAX_RETRIES = 3

async def cleanup_files(*files):
    """Clean up temporary files"""
    for file in files:
        try:
            if file and os.path.exists(file):
                os.remove(file)
        except Exception as e:
            logger.error(f"Error cleaning up {file}: {e}")

@Client.on_message(filters.command(['song', 'mp3']) & filters.private)
async def song(client, message):
    try:
        user_id = message.from_user.id 
        user_name = message.from_user.first_name 
        query = ' '.join(message.command[1:]) if len(message.command) > 1 else None
        
        if not query:
            return await message.reply("Please provide a song name.\nExample: `/song vaa vaathi`")
            
        m = await message.reply(f"**Searching your song...**\n`{query}`")
        
        # Search with retries
        for attempt in range(MAX_RETRIES):
            try:
                results = YoutubeSearch(query, max_results=1).to_dict()
                if results:
                    break
            except Exception as search_error:
                if attempt == MAX_RETRIES-1:
                    logger.error(f"Search error: {search_error}")
                    return await m.edit("❌ Failed to find the song. Please try again later.")
                await asyncio.sleep(1)
        else:
            return await m.edit("❌ No results found. Try a different query.")

        result = results[0]
        link = f"https://youtube.com{result['url_suffix']}"
        title = result["title"][:40]
        thumbnail = result["thumbnails"][0]
        thumb_name = f'thumb_{user_id}.jpg'
        performer = "[NETWORKS™]"
        duration = result["duration"]

        # Download thumbnail
        try:
            response = requests.get(thumbnail, timeout=SEARCH_TIMEOUT)
            response.raise_for_status()
            with open(thumb_name, 'wb') as f:
                f.write(response.content)
        except Exception as e:
            logger.error(f"Thumbnail download error: {e}")
            thumb_name = None

        await m.edit("**Downloading your song...**")
        
        # Download audio
        ydl_opts = {
            "format": "bestaudio[ext=m4a]",
            "outtmpl": f"%(id)s_{user_id}.%(ext)s",
            "quiet": True,
            "socket_timeout": DOWNLOAD_TIMEOUT,
            "nocheckcertificate": True,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info_dict = await asyncio.to_thread(ydl.extract_info, link, download=True)
                audio_file = ydl.prepare_filename(info_dict)
        except Exception as download_error:
            logger.error(f"Download error: {download_error}")
            return await m.edit("❌ Download failed. Please try again later.")

        # Calculate duration
        dur = sum(int(x) * 60 ** i for i, x in enumerate(reversed(duration.split(':'))))

        try:
            await message.reply_audio(
                audio_file,
                caption=f"**BY›› [UPDATE]({CHNL_LNK})**",
                quote=False,
                title=title,
                duration=dur,
                performer=performer,
                thumb=thumb_name
            )
        except Exception as send_error:
            logger.error(f"Send audio error: {send_error}")
            return await m.edit("❌ Failed to send audio. Please try again.")
            
        await m.delete()

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await m.edit("❌ An unexpected error occurred. Please try again.")
    finally:
        await cleanup_files(audio_file, thumb_name)

@Client.on_message(filters.command(["video", "mp4"]))
async def vsong(client, message: Message):
    try:
        urlissed = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
        if not urlissed:
            return await message.reply("Example: /video Baby Shark Dance")
            
        pablo = await message.reply(f"**Finding your video**\n`{urlissed}`")
        
        # Search with retries and timeout
        for attempt in range(MAX_RETRIES):
            try:
                search = SearchVideos(
                    urlissed,
                    offset=1,
                    mode="dict",
                    max_results=1,
                    timeout=SEARCH_TIMEOUT
                )
                result = search.result()
                if result.get("search_result"):
                    break
                await asyncio.sleep(1)
            except Exception as search_error:
                if attempt == MAX_RETRIES-1:
                    logger.error(f"Video search error: {search_error}")
                    return await pablo.edit("❌ Video search failed. Please try again later.")
        else:
            return await pablo.edit("❌ No video found. Try a different query.")

        video_data = result["search_result"][0]
        mo = video_data["link"]
        thum = video_data["title"]
        video_id = video_data["id"]
        thumb_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

        # Download thumbnail
        try:
            sedlyf = f"thumb_{video_id}.jpg"
            response = requests.get(thumb_url, timeout=SEARCH_TIMEOUT)
            response.raise_for_status()
            with open(sedlyf, 'wb') as f:
                f.write(response.content)
        except Exception as thumb_error:
            logger.error(f"Thumbnail download error: {thumb_error}")
            sedlyf = None

        # Download video
        opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
            "outtmpl": f"{video_id}.mp4",
            "socket_timeout": DOWNLOAD_TIMEOUT,
            "nocheckcertificate": True,
            "quiet": True,
            "retries": 3,
            "geo_bypass": True,
        }

        try:
            with YoutubeDL(opts) as ytdl:
                info_dict = await asyncio.to_thread(ytdl.extract_info, mo, download=True)
                file_stark = f"{video_id}.mp4"
        except Exception as download_error:
            logger.error(f"Video download error: {download_error}")
            return await pablo.edit(f"❌ Download failed: {str(download_error)}")

        try:
            await client.send_video(
                message.chat.id,
                video=file_stark,
                duration=int(info_dict["duration"]),
                caption=f"**Title:** [{thum}]({mo})\n**Requested by:** {message.from_user.mention}",
                thumb=sedlyf,
                supports_streaming=True,
                reply_to_message_id=message.id
            )
        except Exception as send_error:
            logger.error(f"Send video error: {send_error}")
            return await pablo.edit("❌ Failed to send video. Please try again.")
            
        await pablo.delete()

    except Exception as e:
        logger.error(f"Unexpected video error: {e}")
        await pablo.edit("❌ An unexpected error occurred. Please try again.")
    finally:
        await cleanup_files(file_stark, sedlyf)