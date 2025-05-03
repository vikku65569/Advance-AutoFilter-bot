from __future__ import unicode_literals

import os, requests, asyncio, math, time, wget
from pyrogram import filters, Client
from pyrogram.types import Message
from info import *
from youtube_search import YoutubeSearch
from youtubesearchpython import SearchVideos
from yt_dlp import YoutubeDL



# Server configuration
COOKIES_FILE = os.path.join(os.path.dirname(__file__), 'cookies.txt')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

def validate_cookies():
    """Check if cookies file exists and is valid"""
    if not os.path.exists(COOKIES_FILE):
        raise FileNotFoundError(
            "❌ cookies.txt not found! Generate it using:\n"
            "1. Install 'Get cookies.txt LOCALLY' Chrome extension\n"
            "2. Login to YouTube in browser\n"
            "3. Export cookies for youtube.com\n"
            "4. Upload cookies.txt to server"
        )
    
    with open(COOKIES_FILE, 'r') as f:
        if not f.readline().startswith('# HTTP'):
            raise ValueError("Invalid cookies.txt format! First line must start with '# HTTP'")

@Client.on_message(filters.command(['song', 'mp3']) & filters.private)
async def song(client, message):
    audio_file = None
    try:
        validate_cookies()
        user_id = message.from_user.id 
        query = ' '.join(message.command[1:]) or None
        
        if not query:
            return await message.reply("❌ Example: /song vaa vaathi song")
            
        m = await message.reply(f"🔍 Searching...\n<code>{query}</code>")
        
        results = YoutubeSearch(query, max_results=1).to_dict()
        if not results:
            return await m.edit("❌ No results found")
            
        video = results[0]
        link = f"https://youtube.com{video['url_suffix']}"
        
        # yt-dlp configuration with cookie support
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'downloads/%(id)s_{user_id}.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'user_agent': USER_AGENT,
            'quiet': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'http_headers': {
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Fetch-User': '?1',
            }
        }

        await m.edit("⬇️ Downloading...")
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=True)
            audio_file = ydl.prepare_filename(info).replace('.webm', '.mp3')

        await message.reply_audio(
            audio_file,
            caption=f"🎵 {info['title']}\nvia @{client.me.username}",
            duration=info['duration']
        )
        await m.delete()

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}\n\n⚠️ Update cookies.txt if this persists")
    finally:
        if audio_file and os.path.exists(audio_file):
            os.remove(audio_file)

@Client.on_message(filters.command(["video", "mp4"]))
async def vsong(client, message: Message):
    video_file = None
    try:
        validate_cookies()
        query = ' '.join(message.command[1:]) or None
        if not query:
            return await message.reply("❌ Example: /video Baby Shark Dance")
            
        pablo = await message.reply(f"🔍 Searching video...\n<code>{query}</code>")
        
        search = SearchVideos(query, offset=1, mode="dict", max_results=1)
        result = search.result()
        if not result.get("search_result"):
            return await pablo.edit("❌ No video found")
            
        video = result["search_result"][0]
        url = video["link"]

        # yt-dlp configuration with cookie support
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]',
            'outtmpl': 'downloads/%(id)s.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'user_agent': USER_AGENT,
            'http_headers': {
                'Accept-Language': 'en-US,en;q=0.9',
                'Sec-Fetch-User': '?1',
            }
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_file = ydl.prepare_filename(info)

        await message.reply_video(
            video_file,
            caption=f"🎥 {info['title']}\nvia @{client.me.username}",
            supports_streaming=True
        )
        await pablo.delete()

    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}\n\n⚠️ Update cookies.txt if this persists")
    finally:
        if video_file and os.path.exists(video_file):
            os.remove(video_file)

def check_ffmpeg():
    if os.system("ffmpeg -version > /dev/null 2>&1") != 0:
        raise EnvironmentError(
            "❌ FFmpeg not installed! On server run:\n"
            "sudo apt update && sudo apt install ffmpeg -y"
        )