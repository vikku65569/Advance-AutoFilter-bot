from __future__ import unicode_literals

import os, requests, asyncio, math, time, wget
from pyrogram import filters, Client
from pyrogram.types import Message
from info import *
from youtube_search import YoutubeSearch
from youtubesearchpython import SearchVideos
from yt_dlp import YoutubeDL

COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.txt')

def check_cookies():
    if not os.path.exists(COOKIES_FILE):
        raise FileNotFoundError(
            "Cookies file not found. Create a cookies.txt file with YouTube login cookies.\n"
            "See: https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
        )
    return True

@Client.on_message(filters.command(['song', 'mp3']) & filters.private)
async def song(client, message):
    try:
        check_cookies()
        user_id = message.from_user.id 
        query = ' '.join(message.command[1:]) or None
        
        if not query:
            return await message.reply("Example: /song vaa vaathi song")
            
        m = await message.reply(f"**Searching your song...**\n`{query}`")
        
        # Get YouTube results
        results = YoutubeSearch(query, max_results=1).to_dict()
        if not results:
            return await m.edit("❌ No results found")
            
        video = results[0]
        link = f"https://youtube.com{video['url_suffix']}"
        title = video["title"][:40]
        thumbnail = video["thumbnails"][0]
        duration = video["duration"]

        # Download thumbnail
        thumb_name = f'thumb_{user_id}.jpg'
        try:
            response = requests.get(thumbnail, timeout=10)
            response.raise_for_status()
            with open(thumb_name, 'wb') as f:
                f.write(response.content)
        except Exception as e:
            thumb_name = None

        # Configure yt-dlp options
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'%(id)s_{user_id}.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'nocheckcertificate': True,
            'quiet': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

        await m.edit("**Downloading your song...**")
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=True)
            audio_file = ydl.prepare_filename(info).replace('.webm', '.mp3')

        # Send audio
        await message.reply_audio(
            audio_file,
            caption=f"**BY›› [UPDATE]({CHNL_LNK})**",
            title=title,
            duration=int(sum(int(x) * 60 ** i for i, x in enumerate(reversed(duration.split(':'))))),
            thumb=thumb_name
        )
        await m.delete()

    except Exception as e:
        await message.reply(f"**Error:** `{str(e)}`")
    finally:
        for f in [audio_file, thumb_name]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except:
                pass

@Client.on_message(filters.command(["video", "mp4"]))
async def vsong(client, message: Message):
    try:
        check_cookies()
        query = ' '.join(message.command[1:]) or None
        if not query:
            return await message.reply("Example: /video Baby Shark Dance")
            
        pablo = await message.reply(f"**Finding video:** `{query}`")
        
        # Search for video
        search = SearchVideos(query, offset=1, mode="dict", max_results=1)
        result = search.result()
        if not result.get("search_result"):
            return await pablo.edit("❌ No video found")
            
        video = result["search_result"][0]
        url = video["link"]
        title = video["title"]
        video_id = video["id"]
        thumb_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

        # Download thumbnail
        thumb_file = f"thumb_{video_id}.jpg"
        try:
            response = requests.get(thumb_url, timeout=10)
            response.raise_for_status()
            with open(thumb_file, 'wb') as f:
                f.write(response.content)
        except Exception as e:
            thumb_file = None

        # Configure yt-dlp options
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': f'%(id)s.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'nocheckcertificate': True,
            'quiet': True,
            'retries': 3
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_file = ydl.prepare_filename(info)

        # Send video
        await message.reply_video(
            video_file,
            caption=f"**Title:** [{title}]({url})\n**Requested by:** {message.from_user.mention}",
            thumb=thumb_file,
            supports_streaming=True
        )
        await pablo.delete()

    except Exception as e:
        await message.reply(f"**Error:** `{str(e)}`")
    finally:
        for f in [video_file, thumb_file]:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
            except:
                pass