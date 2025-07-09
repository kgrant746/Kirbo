import os
from dotenv import load_dotenv

load_dotenv()

__version__ = "1.1.1"

TOKEN = os.getenv("KIRBO_TOKEN")        
GUILD_ID = int(os.getenv("GUILD_ID", 0))    
FFMPEG_PATH = os.getenv("FFMPEG_PATH")         

DISCONNECT_TIMEOUT = 1800

if not TOKEN or GUILD_ID == 0 or not FFMPEG_PATH:
    raise RuntimeError("Missing env vars in .env ─ see .env.example")
