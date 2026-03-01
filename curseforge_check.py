import aiohttp
from bs4 import BeautifulSoup
from pathlib import Path
import json
from discord.ext import tasks
import config

_bot = None

# CFWidget public JSON endpoint (no official CF API key required)
# You can use either:
#   - project path: https://api.cfwidget.com/minecraft/modpacks/team-rocket
#   - project id:   https://api.cfwidget.com/1460748
CFWIDGET_URL = getattr(
    config,
    "CFWIDGET_URL",
    "https://api.cfwidget.com/minecraft/modpacks/team-rocket",
)

# Fallback HTML page (what you already have)
FILES_URL = getattr(config, "FILES_URL", "https://www.curseforge.com/minecraft/modpacks/team-rocket/files")

# Optional: browser-like headers for the fallback HTML scrape (may still 403)
_CF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://www.curseforge.com/",
}

CF_STATE_FILE = Path("cf_scrape_state.json")

def start(bot):
    global _bot
    _bot = bot
    if not cf_poll.is_running():
        print("[CurseForge] Poll task starting (every 10 minutes).")
        cf_poll.start()

@tasks.loop(minutes=10)
async def cf_poll():
    await check_curseforge(bot=_bot, announce_channel_id=config.CURSEFORGE_CHANNEL_ID)

def _load_cf_state():
    if CF_STATE_FILE.exists():
        return json.loads(CF_STATE_FILE.read_text(encoding="utf-8"))
    return {}

def _save_cf_state(data):
    CF_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

async def _announce(bot, channel_id: int, message: str):
    if not bot:
        print("[CurseForge] Bot not set, cannot announce.")
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        print("[CurseForge] Could not resolve announce channel.")
        return
    await channel.send(message)

async def _fetch_cfwidget_latest_file():
    """
    Returns (file_id, url, display_name) or None if it cannot determine.
    CFWidget returns JSON with a 'files' list including latest files.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(CFWIDGET_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    print(f"[CurseForge] CFWidget HTTP error: {resp.status}")
                    return None
                data = await resp.json()
    except Exception as e:
        print(f"[CurseForge] CFWidget request failed: {type(e).__name__}: {e}")
        return None

    files = data.get("files") or []
    if not files:
        print("[CurseForge] CFWidget returned no files.")
        return None

    latest = files[0]
    file_id = str(latest.get("id") or "")
    # urls section shape can vary a bit, handle common ones
    url = (
        (latest.get("urls") or {}).get("curseforge")
        or (latest.get("url"))
        or ""
    )
    # name fields can vary too
    name = latest.get("display") or latest.get("name") or latest.get("title") or "New file"
    if isinstance(name, str) and name.lower().endswith(".zip"):
        name = name[:-4]

    if not file_id:
        print("[CurseForge] CFWidget latest file missing id.")
        return None

    # If url is relative, prefix it
    if url.startswith("/"):
        url = f"https://www.curseforge.com{url}"
    elif url.startswith("www."):
        url = f"https://{url}"

    # If CFWidget doesn't give a URL, build one from file id + known path
    if not url:
        # This will still be a valid file page URL:
        url = f"https://www.curseforge.com/minecraft/modpacks/team-rocket/files/{file_id}"

    return (file_id, url, name)

async def _fetch_html_latest_file():
    """
    Fallback: scrape HTML. Returns (file_id, url, display_name) or None.
    This may still 403 depending on CF.
    """
    print(f"[CurseForge] HTML fallback scrape... url={FILES_URL}")
    try:
        async with aiohttp.ClientSession(headers=_CF_HEADERS) as session:
            async with session.get(FILES_URL, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    print(f"[CurseForge] HTML scrape HTTP error: {resp.status}")
                    return None
                html = await resp.text()
    except Exception as e:
        print(f"[CurseForge] HTML scrape failed: {type(e).__name__}: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    link = soup.find("a", href=lambda h: h and "/files/" in h)
    if not link:
        print("[CurseForge] HTML scrape: could not find file link on page.")
        return None

    href = link["href"]
    file_id = href.rstrip("/").split("/")[-1]
    url = f"https://www.curseforge.com{href}"
    name = link.get_text(strip=True) or "New file"
    return (file_id, url, name)

async def check_curseforge(bot, announce_channel_id: int):
    print(f"[CurseForge] Starting check... cfwidget={CFWIDGET_URL}")

    latest = await _fetch_cfwidget_latest_file()
    if latest is None:
        print("[CurseForge] CFWidget failed, trying HTML fallback.")
        latest = await _fetch_html_latest_file()

    if latest is None:
        print("[CurseForge] Could not determine latest file by any method.")
        return

    file_id, url, name = latest
    print(f"[CurseForge] Latest file: id={file_id} name={name} url={url}")

    state = _load_cf_state()
    last_seen = state.get("last_file_id")

    # First run: you asked to trigger an announcement
    if last_seen is None:
        state["last_file_id"] = file_id
        _save_cf_state(state)
        print(f"[CurseForge] First run. Stored file_id={file_id}. Triggering announcement.")
        await _announce(
            bot,
            announce_channel_id,
            f"Team Rocket Modpack Update Released!\n{name}\n{url}"
        )
        return

    if str(last_seen) == str(file_id):
        print(f"[CurseForge] No change. file_id still {file_id}.")
        return

    state["last_file_id"] = file_id
    _save_cf_state(state)
    print(f"[CurseForge] Update detected. old={last_seen} new={file_id}")

    await _announce(
        bot,
        announce_channel_id,
        f"Team Rocket Modpack Update Released!\n{name}\n{url}"
    )