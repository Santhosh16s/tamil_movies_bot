"""
தமிழ் திரைப்பட பாட் - தொழில்முறை பதிப்பு
Professional Tamil Movie Bot with Enterprise-Grade Architecture

இது Telegram மூலம் 2025 HD தமிழ் திரைப்படங்களை வழங்கும் பாட்டாகும்.
Supabase Database மற்றும் fuzzy search-ஐ பயன்படுத்துகிறது.
"""

import os
import sys
import re
import time
import asyncio
import logging
import unicodedata
from functools import wraps
from datetime import datetime, timezone

import nest_asyncio
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from dotenv import load_dotenv
from rapidfuzz import process
from supabase.client import create_client, Client

# --- Apply nest_asyncio for running in nested loops (e.g., Jupyter, certain servers) ---
nest_asyncio.apply()

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(funcName)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
load_dotenv()

TOKEN: str = os.getenv("TOKEN")
if not TOKEN:
    logger.critical("❌ Telegram TOKEN not set in environment.")
    sys.exit(1)

ADMIN_IDS_STR: str = os.getenv("ADMIN_IDS", "")
admin_ids: set[int] = set(map(int, filter(None, ADMIN_IDS_STR.split(","))))

PRIVATE_CHANNEL_LINK: str = os.getenv("PRIVATE_CHANNEL_LINK", "")
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

# Group / Channel IDs
try:
    SKMOVIES_GROUP_ID: int = int(os.getenv("SKMOVIES_GROUP_ID"))
    SKMOVIESDISCUSSION_GROUP_ID: int = int(os.getenv("SKMOVIESDISCUSSION_GROUP_ID"))
    MOVIE_UPDATE_CHANNEL_ID: int = int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))
except Exception as e:
    logger.critical(f"❌ Invalid group/channel IDs: {e}")
    sys.exit(1)

MOVIE_UPDATE_CHANNEL_URL: str = PRIVATE_CHANNEL_LINK  # Reuse private channel link

# --- Supabase Client Initialization ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info(f"✅ Supabase client initialized: URL={SUPABASE_URL}, KEY={SUPABASE_KEY[:5]}***")
except Exception as e:
    logger.critical(f"❌ Supabase client initialization failed: {e}")
    sys.exit(1)

# --- Global State ---
user_files: dict[int, dict] = {}           # Temporary storage for /addmovie workflow
pending_file_requests: dict[int, dict] = {}  # Pending file requests per user
pending_post: dict[int, dict] = {}          # Pending /post messages per user
movies_data: dict[str, dict] = {}           # Cached movies from Supabase

logger.info("🚀 Initial configuration and environment loaded successfully.")
