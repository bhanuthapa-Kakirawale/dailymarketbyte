"""Central settings for the Daily Byte pipeline. Override anything via environment variables."""
import os
import datetime as dt
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Local secrets (GEMINI_API_KEY, etc.) live in a git-ignored .env file next to this module -
# see .env.example for the template. `config.py` is the first project module every entry point
# imports (main.py, render_daily_market_byte.py, news.py all import it before touching any
# env var), so loading here - and nowhere else - guarantees the key is populated before the
# first `os.getenv("GEMINI_API_KEY")` call, wherever that happens to be.
# `override=False` (python-dotenv's default) means a real environment variable already set by
# the shell or by GitHub Actions secrets always wins over .env - .env only fills gaps locally.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass    # python-dotenv not installed: .env is skipped, real env vars still work

IST = ZoneInfo("Asia/Kolkata")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
OUT_DIR = os.getenv("DAILY_BYTE_OUT", os.path.join(BASE_DIR, "output"))

# Video
W, H, FPS = 1080, 1920, 30
DURATION = 75.0   # target length; scenes that can't get data are dropped and others stretch

# Background music volume (0.0-1.0), mixed low under the captions by default. Override via env.
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", "0.22"))

# Stock universe for gainers/losers: NIFTY50 | NIFTY100 | NIFTY200 | NIFTY500
UNIVERSE = os.getenv("DAILY_BYTE_UNIVERSE", "NIFTY100").upper()
UNIVERSE_LABEL = {"NIFTY50": "Nifty 50", "NIFTY100": "Nifty 100",
                  "NIFTY200": "Nifty 200", "NIFTY500": "Nifty 500"}
TOP_N = 5

# Free AI: Google Gemini API (free tier) with Google Search grounding.
# Get a key at https://aistudio.google.com/apikey . Without a key, Google News headlines are used.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

# Nifty F&O expiry weekday (0=Mon, 1=Tue). NSE moved Nifty expiry to Tuesday in Sep 2025.
EXPIRY_WEEKDAY = int(os.getenv("NIFTY_EXPIRY_WEEKDAY", "1"))

# YouTube upload
YT_PRIVACY = os.getenv("YT_PRIVACY", "public")      # public | unlisted | private
YT_CATEGORY = os.getenv("YT_CATEGORY", "27")        # 27 = Education, 25 = News & Politics

# Colours (RGB)
BG1, BG2 = (7, 11, 26), (16, 26, 56)
CARD = (22, 32, 64)
CARD_ACTIVE = (34, 50, 96)
TEXT = (240, 244, 255)
SUB = (150, 162, 196)
ACCENT = (76, 201, 240)
GREEN = (46, 229, 157)
RED = (255, 77, 109)
YELLOW = (255, 214, 10)


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def fmt_in(x: float, dec: int = 0) -> str:
    """Indian digit grouping: 1234567.8 -> 12,34,567.8"""
    neg = x < 0
    s = f"{abs(x):.{dec}f}"
    ip, _, fp = s.partition(".")
    if len(ip) > 3:
        head, tail = ip[:-3], ip[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        ip = ",".join(groups + [tail])
    return ("-" if neg else "") + ip + ("." + fp if fp else "")
