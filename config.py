import os
from dotenv import load_dotenv

load_dotenv()

__version__ = "1.3.1"

TOKEN = os.getenv("KIRBO_TOKEN")        
GUILD_ID = int(os.getenv("GUILD_ID", 0))    
FFMPEG_PATH = os.getenv("FFMPEG_PATH")    
LLM_CHANNEL_ID = int(os.getenv("LLM_CHANNEL_ID", 0))    
GENERAL_CHAT_CHANNEL_ID = os.getenv("GENERAL_CHAT_CHANNEL_ID")   
COD_VOICE_CHANNEL_ID = os.getenv("COD_VOICE_CHANNEL_ID")      

DISCONNECT_TIMEOUT = 1800

if not TOKEN or GUILD_ID == 0 or not FFMPEG_PATH:
    raise RuntimeError("Missing env vars in .env ─ see .env.example")


SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    raise RuntimeError("Missing Spotify credentials")
