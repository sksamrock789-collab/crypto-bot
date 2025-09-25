<<<<<<< HEAD
import os, time, pickle, logging, requests, random
from io import BufferedReader
from pathlib import Path
from dotenv import load_dotenv
from moviepy.editor import VideoFileClip, AudioFileClip
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from PIL import Image, ImageDraw, ImageFont
import praw

# ---------------- CONFIG ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
load_dotenv("config.env")

# Reddit API
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

# Pixabay / Pexels API
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
PEXELS_KEY = os.getenv("PEXELS_KEY")

# Freesound API
FREESOUND_KEY = os.getenv("FREESOUND_KEY")

# Telegram API
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# YouTube API
CLIENT_SECRETS_FILE = "client_secret.json"
TOKEN_PICKLE = "token.pickle"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

TMP = Path("tmp")
TMP.mkdir(exist_ok=True)


# ---------------- HELPERS ----------------
def notify(msg: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
        except Exception as e:
            logging.warning(f"Telegram notify failed: {e}")


def get_youtube_service():
    creds = None
    if Path(TOKEN_PICKLE).exists():
        f: BufferedReader
        with open(TOKEN_PICKLE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PICKLE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


# ---------------- CONTENT FETCH ----------------
def fetch_reddit_video(subs="funny+memes+videos", limit=30):
    reddit = praw.Reddit(client_id=REDDIT_CLIENT_ID,
                         client_secret=REDDIT_SECRET,
                         user_agent=REDDIT_USER_AGENT)
    for post in reddit.subreddit(subs).hot(limit=limit):
        if post.is_video and not post.stickied:
            return post.title, post.media["reddit_video"]["fallback_url"]
    return None, None


def fetch_pixabay_video():
    if not PIXABAY_KEY:
        return None
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q=trending&per_page=10"
    try:
        r = requests.get(url, timeout=15).json()
        hits = r.get("hits", [])
        if hits:
            v = random.choice(hits)
            return "Pixabay Viral Clip", v["videos"]["medium"]["url"]
    except Exception as e:
        logging.warning(f"Pixabay fetch failed: {e}")
    return None


def fetch_pexels_video():
    if not PEXELS_KEY:
        return None
    url = "https://api.pexels.com/videos/search?query=trending&per_page=10"
    try:
        headers = {"Authorization": PEXELS_KEY}
        r = requests.get(url, headers=headers, timeout=15).json()
        vids = r.get("videos", [])
        if vids:
            pick = random.choice(vids)
            return "Pexels Viral Clip", pick["video_files"][0]["link"]
    except Exception as e:
        logging.warning(f"Pexels fetch failed: {e}")
    return None


def fetch_freesound_audio():
    if not FREESOUND_KEY:
        return None
    url = "https://freesound.org/apiv2/search/text/"
    try:
        r = requests.get(url, params={"query": "beat", "page_size": 10},
                         headers={"Authorization": f"Token {FREESOUND_KEY}"}, timeout=15).json()
        if r.get("results"):
            pick = random.choice(r["results"])
            audio_url = pick["previews"]["preview-hq-mp3"]
            path = TMP / "music.mp3"
            res = requests.get(audio_url, timeout=15)
            path.write_bytes(res.content)
            return str(path)
    except Exception as e:
        logging.warning(f"Freesound fetch failed: {e}")
    return None


# ---------------- VIDEO ----------------
def download_video(url: str, out_path: Path):
    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
        return True
    except Exception as e:
        logging.error(f"Download failed: {e}")
        return False


def mix_video_music(video_path: Path, audio_path: Path, duration=15):
    clip = VideoFileClip(str(video_path)).subclip(0, duration).resize((1080, 1920))
    if audio_path and Path(audio_path).exists():
        audio = AudioFileClip(str(audio_path)).subclip(0, duration)
        clip = clip.set_audio(audio)
    out = TMP / f"final_{int(time.time())}.mp4"
    clip.write_videofile(str(out), fps=24, codec="libx264", audio_codec="aac", threads=2)
    return str(out)


def make_thumbnail(video_path, title, out_path="thumb.jpg"):
    try:
        clip = VideoFileClip(video_path)
        frame = clip.get_frame(clip.duration / 2)
        img = Image.fromarray(frame)

        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 70)
        except:
            font = ImageFont.load_default()

        text = title[:40] + ("\n" + title[40:80] if len(title) > 40 else "")
        w, h = draw.textsize(text, font=font)
        x = (img.width - w) / 2
        y = img.height - h - 50

        draw.rectangle([x-20, y-20, x+w+20, y+h+20], fill=(0, 0, 0, 180))
        draw.text((x, y), text, font=font, fill="white")

        img.save(out_path, "JPEG")
        return out_path
    except Exception as e:
        logging.error(f"Thumbnail error: {e}")
        return None


# ---------------- UPLOAD ----------------
def upload_video_to_youtube(service, video_path, title, description, tags=None, thumb_path=None):
    body = {
        "snippet": {"title": title, "description": description, "tags": tags or [], "categoryId": "23"},
        "status": {"privacyStatus": "public"}
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()

    vid_id = response.get("id")
    link = f"https://youtu.be/{vid_id}"

    if thumb_path and Path(thumb_path).exists():
        try:
            service.thumbnails().set(videoId=vid_id, media_body=MediaFileUpload(thumb_path)).execute()
        except Exception as e:
            logging.warning(f"Thumbnail upload failed: {e}")

    logging.info(f"✅ Uploaded: {link}")
    notify(f"✅ Uploaded: {title}\n{link}")
    return link


# ---------------- MAIN ----------------
def main():
    global final_video
    service = get_youtube_service()
    notify("🚀 Bot started: uploading viral video...")

    # 1. Try Reddit
    title, url = fetch_reddit_video()
    if not url:
        # 2. Try Pixabay
        alt = fetch_pixabay_video()
        if alt:
            title, url = alt
        else:
            # 3. Try Pexels
            alt = fetch_pexels_video()
            if alt:
                title, url = alt
            else:
                notify("❌ No video found (Reddit/Pixabay/Pexels)")
                return

    # Download video
    video_path = TMP / f"video_{int(time.time())}.mp4"
    if not download_video(url, video_path):
        notify("❌ Failed to download video")
        return

    # Add music
    audio = fetch_freesound_audio()

    # Final mix
    final_video = mix_video_music(video_path, audio, duration=15)
        
    # Hindi + English Title
    final_title = f"{title[:60]} | {title[:40]} 🔥 #shorts"
    description = f"{title}\n\n{title} (हिंदी)\n\n#shorts #viral"

    # Thumbnail
    thumb_path = make_thumbnail(final_video, title)

    # Upload
    upload_video_to_youtube(service, final_video, final_title, description, thumb_path=thumb_path)


if __name__ == "__main__":
    while True:
        main()
        time.sleep(3 * 3600)  # every 3 hours
=======
import os, time, pickle, logging, requests, random
from pathlib import Path
from dotenv import load_dotenv
from moviepy.editor import VideoFileClip, AudioFileClip
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from PIL import Image, ImageDraw, ImageFont
import praw

# ---------------- CONFIG ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
load_dotenv("config.env")

# Reddit API
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_SECRET = os.getenv("REDDIT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")

# Pixabay / Pexels API
PIXABAY_KEY = os.getenv("PIXABAY_KEY")
PEXELS_KEY = os.getenv("PEXELS_KEY")

# Freesound API
FREESOUND_KEY = os.getenv("FREESOUND_KEY")

# Telegram API
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# YouTube API
CLIENT_SECRETS_FILE = "client_secret.json"
TOKEN_PICKLE = "token.pickle"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

TMP = Path("tmp")
TMP.mkdir(exist_ok=True)


# ---------------- HELPERS ----------------
def notify(msg: str):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=5)
        except Exception as e:
            logging.warning(f"Telegram notify failed: {e}")


def get_youtube_service():
    creds = None
    if Path(TOKEN_PICKLE).exists():
        with open(TOKEN_PICKLE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PICKLE, "wb") as f:
            pickle.dump(creds, f)
    return build("youtube", "v3", credentials=creds)


# ---------------- CONTENT FETCH ----------------
def fetch_reddit_video(subs="funny+memes+videos", limit=30):
    reddit = praw.Reddit(client_id=REDDIT_CLIENT_ID,
                         client_secret=REDDIT_SECRET,
                         user_agent=REDDIT_USER_AGENT)
    for post in reddit.subreddit(subs).hot(limit=limit):
        if post.is_video and not post.stickied:
            return post.title, post.media["reddit_video"]["fallback_url"]
    return None, None


def fetch_pixabay_video():
    if not PIXABAY_KEY:
        return None
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q=trending&per_page=10"
    try:
        r = requests.get(url, timeout=15).json()
        hits = r.get("hits", [])
        if hits:
            v = random.choice(hits)
            return "Pixabay Viral Clip", v["videos"]["medium"]["url"]
    except Exception as e:
        logging.warning(f"Pixabay fetch failed: {e}")
    return None


def fetch_pexels_video():
    if not PEXELS_KEY:
        return None
    url = "https://api.pexels.com/videos/search?query=trending&per_page=10"
    try:
        headers = {"Authorization": PEXELS_KEY}
        r = requests.get(url, headers=headers, timeout=15).json()
        vids = r.get("videos", [])
        if vids:
            pick = random.choice(vids)
            return "Pexels Viral Clip", pick["video_files"][0]["link"]
    except Exception as e:
        logging.warning(f"Pexels fetch failed: {e}")
    return None


def fetch_freesound_audio():
    if not FREESOUND_KEY:
        return None
    url = "https://freesound.org/apiv2/search/text/"
    try:
        r = requests.get(url, params={"query": "beat", "page_size": 10},
                         headers={"Authorization": f"Token {FREESOUND_KEY}"}, timeout=15).json()
        if r.get("results"):
            pick = random.choice(r["results"])
            audio_url = pick["previews"]["preview-hq-mp3"]
            path = TMP / "music.mp3"
            res = requests.get(audio_url, timeout=15)
            path.write_bytes(res.content)
            return str(path)
    except Exception as e:
        logging.warning(f"Freesound fetch failed: {e}")
    return None


# ---------------- VIDEO ----------------
def download_video(url: str, out_path: Path):
    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
        return True
    except Exception as e:
        logging.error(f"Download failed: {e}")
        return False


def mix_video_music(video_path: Path, audio_path: Path, duration=15):
    clip = VideoFileClip(str(video_path)).subclip(0, duration).resize((1080, 1920))
    if audio_path and Path(audio_path).exists():
        audio = AudioFileClip(str(audio_path)).subclip(0, duration)
        clip = clip.set_audio(audio)
    out = TMP / f"final_{int(time.time())}.mp4"
    clip.write_videofile(str(out), fps=24, codec="libx264", audio_codec="aac", threads=2)
    return str(out)


def make_thumbnail(video_path, title, out_path="thumb.jpg"):
    try:
        clip = VideoFileClip(video_path)
        frame = clip.get_frame(clip.duration / 2)
        img = Image.fromarray(frame)

        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 70)
        except:
            font = ImageFont.load_default()

        text = title[:40] + ("\n" + title[40:80] if len(title) > 40 else "")
        w, h = draw.textsize(text, font=font)
        x = (img.width - w) / 2
        y = img.height - h - 50

        draw.rectangle([x-20, y-20, x+w+20, y+h+20], fill=(0, 0, 0, 180))
        draw.text((x, y), text, font=font, fill="white")

        img.save(out_path, "JPEG")
        return out_path
    except Exception as e:
        logging.error(f"Thumbnail error: {e}")
        return None


# ---------------- UPLOAD ----------------
def upload_video_to_youtube(service, video_path, title, description, tags=None, thumb_path=None):
    body = {
        "snippet": {"title": title, "description": description, "tags": tags or [], "categoryId": "23"},
        "status": {"privacyStatus": "public"}
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()

    vid_id = response.get("id")
    link = f"https://youtu.be/{vid_id}"

    if thumb_path and Path(thumb_path).exists():
        try:
            service.thumbnails().set(videoId=vid_id, media_body=MediaFileUpload(thumb_path)).execute()
        except Exception as e:
            logging.warning(f"Thumbnail upload failed: {e}")

    logging.info(f"✅ Uploaded: {link}")
    notify(f"✅ Uploaded: {title}\n{link}")
    return link


# ---------------- MAIN ----------------
def main():
    service = get_youtube_service()
    notify("🚀 Bot started: uploading viral video...")

    # 1. Try Reddit
    title, url = fetch_reddit_video()
    if not url:
        # 2. Try Pixabay
        alt = fetch_pixabay_video()
        if alt:
            title, url = alt
        else:
            # 3. Try Pexels
            alt = fetch_pexels_video()
            if alt:
                title, url = alt
            else:
                notify("❌ No video found (Reddit/Pixabay/Pexels)")
                return

    # Download video
    video_path = TMP / f"video_{int(time.time())}.mp4"
    if not download_video(url, video_path):
        notify("❌ Failed to download video")
        return

    # Add music
    audio = fetch_freesound_audio()

    # Final mix
    final_video = mix_video_music(video_path, audio, duration=15)

    # Hindi + English Title
    final_title = f"{title[:60]} | {title[:40]} 🔥 #shorts"
    description = f"{title}\n\n{title} (हिंदी)\n\n#shorts #viral"

    # Thumbnail
    thumb_path = make_thumbnail(final_video, title)

    # Upload
    upload_video_to_youtube(service, final_video, final_title, description, thumb_path=thumb_path)


if __name__ == "__main__":
    while True:
        main()
        time.sleep(3 * 3600)  # every 3 hours

