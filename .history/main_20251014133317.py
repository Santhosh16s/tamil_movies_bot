import logging
import asyncio
import nest_asyncio
import unicodedata
import re
import sys
import os
import time
import telegram
from rapidfuzz import process
from dotenv import load_dotenv
from functools import wraps
from supabase.client import create_client, Client
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ==================== CONFIGURATION ====================
class BotConfig:
    """கான்ஃபிகுரேஷன் செட்டிங்ஸ்"""
    def __init__(self):
        load_dotenv()
        nest_asyncio.apply()
        
        self.TOKEN = os.getenv("TOKEN")
        self.SUPABASE_URL = os.getenv("SUPABASE_URL")
        self.SUPABASE_KEY = os.getenv("SUPABASE_KEY")
        self.PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")
        
        self.ADMIN_IDS = self._parse_admin_ids()
        self.CHANNEL_IDS = self._parse_channel_ids()
        
        self.setup_logging()
    
    def _parse_admin_ids(self):
        """அட்மின் ஐடிகளை பார்க் செய்கிறது"""
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        return set(map(int, filter(None, admin_ids_str.split(","))))
    
    def _parse_channel_ids(self):
        """சேனல் ஐடிகளை பார்க் செய்கிறது"""
        return {
            'skmovies': int(os.getenv("SKMOVIES_GROUP_ID")),
            'discussion': int(os.getenv("SKMOVIESDISCUSSION_GROUP_ID")),
            'updates': int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))
        }
    
    def setup_logging(self):
        """லாக்கிங் செட்டப்"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

# ==================== DATABASE MANAGER ====================
class DatabaseManager:
    """டேட்டாபேஸ் மேனேஜ்மெண்ட்"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.client = self._initialize_client()
        self.movies_data = self.load_movies_data()
    
    def _initialize_client(self):
        """Supabase கிளையண்ட் இனிஷியலைஸ்"""
        try:
            client = create_client(self.config.SUPABASE_URL, self.config.SUPABASE_KEY)
            logging.info("✅ Supabase கிளையண்ட் தொடங்கப்பட்டது")
            return client
        except Exception as e:
            logging.error(f"❌ Supabase கிளையண்ட் பிழை: {e}")
            sys.exit(1)
    
    def load_movies_data(self):
        """திரைப்பட தரவை லோட் செய்கிறது"""
        try:
            response = self.client.table("movies").select("*").execute()
            movies = response.data or []
            
            movies_data = {}
            for movie in movies:
                cleaned_title = self.clean_title(movie['title'])
                movies_data[cleaned_title] = {
                    'poster_url': movie['poster_url'],
                    'files': {
                        '480p': movie['file_480p'],
                        '720p': movie['file_720p'],
                        '1080p': movie['file_1080p'],
                    }
                }
            
            logging.info(f"✅ {len(movies_data)} திரைப்படங்கள் லோட் செய்யப்பட்டன")
            return movies_data
        except Exception as e:
            logging.error(f"❌ திரைப்பட தரவு லோட் பிழை: {e}")
            return {}
    
    @staticmethod
    def clean_title(title: str) -> str:
        """தலைப்பை சுத்தம் செய்கிறது"""
        cleaned = title.lower()
        cleaned = ''.join(c for c in cleaned if unicodedata.category(c)[0] not in ['S', 'C'])
        cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    
    def extract_title(self, filename: str) -> str:
        """கோப்பு பெயரிலிருந்து தலைப்பை எக்ஸ்ட்ராக்ட் செய்கிறது"""
        # ஃபைல் பெயர் கிளீனிங் லாஜிக்
        filename = re.sub(r"@\S+", "", filename)
        filename = re.sub(r"\b(480p|720p|1080p|x264|x265|HEVC|HDRip|WEBRip|AAC)\b", "", filename, flags=re.IGNORECASE)
        filename = re.sub(r"[\[\]\(\)\{\}]", " ", filename)
        filename = re.sub(r"\s+", " ", filename).strip()

        match = re.search(r"([a-zA-Z\s]+)(?:\(?)(20\d{2})(?:\)?)", filename)
        if match:
            return f"{match.group(1).strip()} ({match.group(2)})"
        
        title = re.split(r"[-0-9]", filename)[0].strip()
        return title

# ==================== UTILITY FUNCTIONS ====================
class BotUtils:
    """பயன்பாட்டு செயல்பாடுகள்"""
    
    @staticmethod
    def time_diff(dt):
        """நேர வித்தியாசத்தை கணக்கிடுகிறது"""
        now = datetime.now(timezone.utc)
        diff = now - dt.replace(tzinfo=timezone.utc)
        seconds = int(diff.total_seconds())

        if seconds < 60:
            return f"{seconds} வினாடிகள் முன்பு"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} நிமிடங்கள் முன்பு"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} மணிநேரம் முன்பு"
        days = hours // 24
        return f"{days} நாட்கள் முன்பு"
    
    @staticmethod
    async def delete_after_delay(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 600):
        """தாமதத்திற்குப் பிறகு செய்தியை நீக்குகிறது"""
        await asyncio.sleep(delay)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            logging.info(f"✅ செய்தி நீக்கப்பட்டது: {message_id}")
        except Exception as e:
            logging.warning(f"⚠️ செய்தி நீக்க பிழை: {e}")

# ==================== DECORATORS ====================
def restricted(func):
    """அட்மின் மட்டுமே அணுக அனுமதிக்கும் டெக்கரேட்டர்"""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in context.bot_data['config'].ADMIN_IDS:
            await update.message.reply_text("❌ இந்த கட்டளை அட்மின்களுக்கு மட்டுமே")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# ==================== MESSAGE HANDLERS ====================
class MessageHandlers:
    """செய்தி ஹேண்ட்லர்கள்"""
    
    def __init__(self, config: BotConfig, db: DatabaseManager):
        self.config = config
        self.db = db
        self.user_files = {}
        self.pending_requests = {}
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/start கட்டளை"""
        user = update.effective_user
        await self._track_user(user)
        
        welcome_text = (
            f"வணக்கம் {user.first_name}! 👋\n\n"
            "🎬 லேட்டஸ்ட் 2025 HD தமிழ் படங்கள் வேண்டுமா? ✨\n"
            "விளம்பரமில்லா உடனடி தேடலுடன், தரமான சினிமா அனுபவம் இங்கே! 🍿\n\n"
            "🎬 தயவுசெய்து திரைப்படத்தின் பெயரை டைப் செய்து அனுப்புங்கள்!"
        )
        
        await update.message.reply_text(welcome_text)
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """உரை செய்திகளை கையாள்கிறது"""
        search_query = update.message.text.strip()
        await self._search_and_send_movies(update, search_query)
    
    async def _search_and_send_movies(self, update: Update, search_query: str):
        """திரைப்படங்களை தேடி அனுப்புகிறது"""
        cleaned_query = self.db.clean_title(search_query)
        movie_titles = list(self.db.movies_data.keys())
        
        matches = process.extract(cleaned_query, movie_titles, score_cutoff=80)
        
        if not matches:
            await self._show_suggestions(update, cleaned_query, movie_titles)
        elif len(matches) == 1 and matches[0][1] >= 95:
            await self._send_movie_poster(update.message, matches[0][0], update._context)
        else:
            await self._show_matches(update, matches)
    
    async def _send_movie_poster(self, message: Message, movie_key: str, context: ContextTypes.DEFAULT_TYPE):
        """திரைப்பட போஸ்டர் அனுப்புகிறது"""
        movie = self.db.movies_data.get(movie_key)
        if not movie:
            await message.reply_text("❌ திரைப்படம் கிடைக்கவில்லை")
            return
        
        caption = self._create_movie_caption(movie_key)
        keyboard = self._create_resolution_buttons(movie_key)
        
        try:
            sent_msg = await message.reply_photo(
                movie["poster_url"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            asyncio.create_task(
                BotUtils.delete_after_delay(context, message.chat_id, sent_msg.message_id)
            )
        except Exception as e:
            logging.error(f"❌ போஸ்டர் அனுப்ப பிழை: {e}")
            await message.reply_text("⚠️ போஸ்டர் அனுப்ப முடியவில்லை")
    
    def _create_movie_caption(self, movie_key: str) -> str:
        """திரைப்பட விவரம் உருவாக்குகிறது"""
        return (
            f"🎬 *{movie_key.title()}*\n\n"
            f"👉 <a href='{self.config.PRIVATE_CHANNEL_LINK}'>SK Movies Updates 🔔</a> - "
            "புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே!"
        )
    
    def _create_resolution_buttons(self, movie_key: str) -> list:
        """ரெசலூஷன் பட்டன்கள் உருவாக்குகிறது"""
        return [[
            InlineKeyboardButton("480p", callback_data=f"res|{movie_key}|480p"),
            InlineKeyboardButton("720p", callback_data=f"res|{movie_key}|720p"), 
            InlineKeyboardButton("1080p", callback_data=f"res|{movie_key}|1080p"),
        ]]
    
    async def _track_user(self, user: telegram.User):
        """பயனர் தகவலை டிராக் செய்கிறது"""
        try:
            # பயனர் டிராக்கிங் லாஜிக்
            user_data = {
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "joined_at": datetime.utcnow().isoformat()
            }
            # Supabase-ல் சேமிக்கவும்
            logging.info(f"✅ பயனர் டிராக் செய்யப்பட்டார்: {user.id}")
        except Exception as e:
            logging.error(f"❌ பயனர் டிராக்கிங் பிழை: {e}")

# ==================== MAIN BOT CLASS ====================
class MovieBot:
    """முக்கிய பாட் கிளாஸ்"""
    
    def __init__(self):
        self.config = BotConfig()
        self.db = DatabaseManager(self.config)
        self.handlers = MessageHandlers(self.config, self.db)
        self.app = None
    
    async def initialize(self):
        """பாட்டை இனிஷியலைஸ் செய்கிறது"""
        self.app = ApplicationBuilder().token(self.config.TOKEN).build()
        self._setup_handlers()
        
        # க்ளோபல் டேட்டா
        self.app.bot_data['config'] = self.config
        self.app.bot_data['db'] = self.db
    
    def _setup_handlers(self):
        """எல்லா ஹேண்ட்லர்களையும் செட்டப் செய்கிறது"""
        # கட்டளை ஹேண்ட்லர்கள்
        self.app.add_handler(CommandHandler("start", self.handlers.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        
        # செய்தி ஹேண்ட்லர்கள்
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handlers.handle_text_message
        ))
        
        # கால்பேக் ஹேண்ட்லர்கள்
        self.app.add_handler(CallbackQueryHandler(
            self.handle_resolution_click, pattern=r"^res\|"
        ))
    
    @restricted
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/status கட்டளை"""
        try:
            response = context.bot_data['db'].client.table("movies").select("id", count="exact").execute()
            total_movies = response.count or 0
            
            status_text = (
                f"📊 *பாட் நிலை:*\n"
                f"----------------------------------\n"
                f"• மொத்த திரைப்படங்கள்: `{total_movies}`\n"
                f"• திரைப்பட தரவு: `{len(context.bot_data['db'].movies_data)}`\n"
                f"• அட்மின்கள்: `{len(context.bot_data['config'].ADMIN_IDS)}`"
            )
            
            await update.message.reply_text(status_text, parse_mode='Markdown')
        except Exception as e:
            logging.error(f"❌ Status பிழை: {e}")
            await update.message.reply_text("❌ நிலை தகவலைப் பெற முடியவில்லை")
    
    async def handle_resolution_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """ரெசலூஷன் கிளிக் கையாளுதல்"""
        query = update.callback_query
        await query.answer()
        
        _, movie_key, resolution = query.data.split("|", 2)
        await self._send_movie_file(query, movie_key, resolution, context)
    
    async def _send_movie_file(self, query, movie_key: str, resolution: str, context: ContextTypes.DEFAULT_TYPE):
        """திரைப்பட கோப்பை அனுப்புகிறது"""
        movie = self.db.movies_data.get(movie_key)
        if not movie:
            await query.message.reply_text("❌ திரைப்படம் கிடைக்கவில்லை")
            return
        
        file_id = movie['files'].get(resolution)
        if not file_id:
            await query.message.reply_text("⚠️ இந்த ரெசலூஷனில் கோப்பு இல்லை")
            return
        
        try:
            caption = (
                f"🎬 *{movie_key.title()}* - {resolution}p\n\n"
                f"👉 <a href='{self.config.PRIVATE_CHANNEL_LINK}'>SK Movies Updates 🔔</a>\n\n"
                f"⚠️ இந்த கோப்பு 10 நிமிடங்களில் நீக்கப்படும்"
            )
            
            sent_msg = await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            
            asyncio.create_task(
                BotUtils.delete_after_delay(context, sent_msg.chat_id, sent_msg.message_id)
            )
        except Exception as e:
            logging.error(f"❌ கோப்பு அனுப்ப பிழை: {e}")
            await query.message.reply_text("⚠️ கோப்பை அனுப்ப முடியவில்லை")
    
    async def run(self):
        """பாட்டை இயக்குகிறது"""
        if not self.app:
            await self.initialize()
        
        logging.info("🚀 திரைப்பட பாட் தொடங்குகிறது...")
        await self.app.run_polling()

# ==================== MAIN EXECUTION ====================
async def main():
    """முக்கிய செயல்பாடு"""
    bot = MovieBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())