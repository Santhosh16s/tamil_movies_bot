"""
தமிழ் திரைப்பட பாட் - தொழில்முறை பதிப்பு
Professional Tamil Movie Bot with Enterprise-Grade Architecture

இந்த பாட் Telegram மூலம் தமிழ் திரைப்படங்களை வழங்குகிறது.
Supabase database மற்றும் advanced fuzzy search-ஐ பயன்படுத்துகிறது.
"""

import logging
import asyncio
import nest_asyncio
import unicodedata
import re
import sys
import os
importco
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone
from functools import wraps
from enum import Enum

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
from rapidfuzz import process
from dotenv import load_dotenv
from supabase.client import create_client, Client

# ============================================================================
# உலகளாவிய மாறிலிகள் மற்றும் கட்டமைப்பு
# Global Constants and Configuration
# ============================================================================

class ConfigurationManager:
    """கட்டமைப்பு மேலாண்மை வகுப்பு - Configuration management class"""
    
    def __init__(self):
        """சூழல் மாறிகளை ஏற்றுகிறது - Loads environment variables"""
        load_dotenv()
        self._validate_and_load()
    
    def _validate_and_load(self) -> None:
        """அனைத்து தேவையான சூழல் மாறிகளையும் சரிபார்த்து ஏற்றுகிறது"""
        required_vars = [
            "TOKEN", "SUPABASE_URL", "SUPABASE_KEY", 
            "PRIVATE_CHANNEL_LINK", "SKMOVIES_GROUP_ID",
            "SKMOVIESDISCUSSION_GROUP_ID", "MOVIE_UPDATE_CHANNEL_ID"
        ]
        
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise EnvironmentError(
                f"❌ தேவையான சூழல் மாறிகள் காணவில்லை: {', '.join(missing_vars)}"
            )
        
        self.TOKEN = os.getenv("TOKEN")
        self.SUPABASE_URL = os.getenv("SUPABASE_URL")
        self.SUPABASE_KEY = os.getenv("SUPABASE_KEY")
        self.PRIVATE_CHANNEL_LINK = os.getenv("PRIVATE_CHANNEL_LINK")
        self.MOVIE_UPDATE_CHANNEL_URL = self.PRIVATE_CHANNEL_LINK
        
        # Integer மதிப்புகளை மாற்றுதல்
        try:
            self.SKMOVIES_GROUP_ID = int(os.getenv("SKMOVIES_GROUP_ID"))
            self.SKMOVIESDISCUSSION_GROUP_ID = int(os.getenv("SKMOVIESDISCUSSION_GROUP_ID"))
            self.MOVIE_UPDATE_CHANNEL_ID = int(os.getenv("MOVIE_UPDATE_CHANNEL_ID"))
        except (ValueError, TypeError) as e:
            raise ValueError(f"❌ Group ID மதிப்புகள் தவறானவை: {e}")
        
        # Admin IDs-ஐ பாகுபடுத்துதல்
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        self.admin_ids = set(map(int, filter(None, admin_ids_str.split(","))))
        
        if not self.admin_ids:
            logging.warning("⚠️ Admin IDs வழங்கப்படவில்லை. சில features செயல்படாது.")


class ResolutionType(Enum):
    """வீடியோ தெளிவுத்திறன் வகைகள் - Video resolution types"""
    LOW = "480p"
    MEDIUM = "720p"
    HIGH = "1080p"


# ============================================================================
# Logging அமைப்பு - Logging Setup
# ============================================================================

class LoggerSetup:
    """மேம்படுத்தப்பட்ட logging அமைப்பு - Enhanced logging configuration"""
    
    @staticmethod
    def configure():
        """தொழில்முறை logging-ஐ அமைக்கிறது"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # மூன்றாம் தரப்பு நூலக logs-ஐ குறைக்கிறது
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('telegram').setLevel(logging.WARNING)


# ============================================================================
# Database மேலாளர் - Database Manager
# ============================================================================

class DatabaseManager:
    """Supabase database செயல்பாடுகளை நிர்வகிக்கிறது"""
    
    def __init__(self, url: str, key: str):
        """Database இணைப்பை துவக்குகிறது"""
        try:
            self.client: Client = create_client(url, key)
            logging.info(f"✅ Supabase இணைப்பு வெற்றிகரமாக நிறுவப்பட்டது")
            self._test_connection()
        except Exception as e:
            logging.error(f"❌ Supabase client உருவாக்க முடியவில்லை: {e}")
            raise
    
    def _test_connection(self) -> None:
        """Database இணைப்பை சோதிக்கிறது"""
        try:
            self.client.table("movies").select("id").limit(1).execute()
            logging.info("✅ Database இணைப்பு சோதனை வெற்றி")
        except Exception as e:
            logging.error(f"❌ Database இணைப்பு சோதனை தோல்வி: {e}")
            raise
    
    def படங்களை_ஏற்று(self) -> Dict[str, Dict[str, Any]]:
        """அனைத்து திரைப்படங்களையும் database-இலிருந்து ஏற்றுகிறது"""
        try:
            response = self.client.table("movies").select("*").execute()
            movies = response.data or []
            
            movies_data = {}
            for movie in movies:
                cleaned_title = self._தலைப்பை_சுத்தம்_செய்(movie['title'])
                movies_data[cleaned_title] = {
                    'poster_url': movie['poster_url'],
                    'files': {
                        ResolutionType.LOW.value: movie['file_480p'],
                        ResolutionType.MEDIUM.value: movie['file_720p'],
                        ResolutionType.HIGH.value: movie['file_1080p'],
                    }
                }
            
            logging.info(f"✅ {len(movies_data)} திரைப்படங்கள் வெற்றிகரமாக ஏற்றப்பட்டன")
            return movies_data
            
        except Exception as e:
            logging.error(f"❌ திரைப்படங்களை ஏற்ற பிழை: {e}")
            return {}
    
    def படத்தை_சேமி(self, title: str, poster_id: str, file_ids: List[str]) -> bool:
        """புதிய திரைப்படத்தை database-இல் சேமிக்கிறது"""
        try:
            cleaned_title = self._தலைப்பை_சுத்தம்_செய்(title)
            logging.info(f"படத்தை சேமிக்கிறது: '{cleaned_title}'")
            
            data = {
                "title": cleaned_title,
                "poster_url": poster_id,
                "file_480p": file_ids[0] if len(file_ids) > 0 else None,
                "file_720p": file_ids[1] if len(file_ids) > 1 else None,
                "file_1080p": file_ids[2] if len(file_ids) > 2 else None,
                "uploaded_at": datetime.utcnow().isoformat()
            }
            
            response = self.client.table("movies").insert(data).execute()
            
            if response.data:
                logging.info(f"✅ திரைப்படம் '{cleaned_title}' வெற்றிகரமாக சேமிக்கப்பட்டது")
                return True
            else:
                error_details = self._பிழையை_பெறு(response)
                logging.error(f"❌ சேமிப்பு தோல்வி: {error_details}")
                return False
                
        except Exception as e:
            logging.error(f"❌ Database insert பிழை: {e}")
            return False
    
    def பயனரை_பதிவு_செய்(self, user: telegram.User) -> bool:
        """புதிய பயனரை database-இல் பதிவு செய்கிறது"""
        try:
            user_id = user.id
            
            # ஏற்கனவே உள்ளதா என சரிபார்
            response = self.client.table("users").select("user_id, message_count").eq("user_id", user_id).limit(1).execute()
            
            if not response.data:
                # புதிய பயனரை சேர்
                user_data = {
                    "user_id": user_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "joined_at": datetime.utcnow().isoformat(),
                    "message_count": 1
                }
                
                insert_response = self.client.table("users").insert(user_data).execute()
                
                if insert_response.data:
                    logging.info(f"✅ புதிய பயனர் பதிவு: {user_id}")
                    return True
                else:
                    logging.error(f"❌ பயனர் பதிவு தோல்வி: {user_id}")
                    return False
            else:
                # message_count-ஐ புதுப்பி
                current_count = response.data[0].get("message_count", 0)
                new_count = current_count + 1
                
                update_response = self.client.table("users").update(
                    {"message_count": new_count}
                ).eq("user_id", user_id).execute()
                
                if update_response.data:
                    logging.debug(f"பயனர் {user_id} செய்தி எண்ணிக்கை: {new_count}")
                    return True
                
        except Exception as e:
            logging.error(f"❌ பயனர் பதிவு பிழை: {e}")
            return False
    
    def மொத்த_பயனர்கள்(self) -> int:
        """மொத்த பதிவு செய்யப்பட்ட பயனர்களின் எண்ணிக்கை"""
        try:
            response = self.client.table("users").select("user_id", count="exact").execute()
            return response.count or 0
        except Exception as e:
            logging.error(f"❌ பயனர் எண்ணிக்கை பெற பிழை: {e}")
            return 0
    
    def மொத்த_படங்கள்(self) -> int:
        """மொத்த திரைப்படங்களின் எண்ணிக்கை"""
        try:
            response = self.client.table("movies").select("id", count="exact").execute()
            return response.count or 0
        except Exception as e:
            logging.error(f"❌ திரைப்பட எண்ணிக்கை பெற பிழை: {e}")
            return 0
    
    def படங்கள்_பக்கம்(self, limit: int = 30, offset: int = 0) -> List[str]:
        """குறிப்பிட்ட பக்கத்தில் உள்ள திரைப்படங்களை திருப்பி அனுப்புகிறது"""
        try:
            response = self.client.table("movies").select("title").order(
                "title", desc=False
            ).range(offset, offset + limit - 1).execute()
            
            movies = response.data or []
            return [m['title'] for m in movies]
        except Exception as e:
            logging.error(f"❌ பக்க தரவு பெற பிழை: {e}")
            return []
    
    def தலைப்பை_புதுப்பி(self, old_title: str, new_title: str) -> bool:
        """திரைப்பட தலைப்பை புதுப்பிக்கிறது"""
        try:
            cleaned_old = self._தலைப்பை_சுத்தம்_செய்(old_title)
            cleaned_new = self._தலைப்பை_சுத்தம்_செய்(new_title)
            
            response = self.client.table("movies").update(
                {"title": cleaned_new}
            ).eq("title", cleaned_old).execute()
            
            if response.data:
                logging.info(f"✅ தலைப்பு புதுப்பிப்பு வெற்றி: {cleaned_old} → {cleaned_new}")
                return True
            else:
                logging.warning(f"⚠️ தலைப்பு புதுப்பிப்பு தோல்வி அல்லது படம் இல்லை")
                return False
                
        except Exception as e:
            logging.error(f"❌ தலைப்பு புதுப்பிப்பு பிழை: {e}")
            return False
    
    def படத்தை_நீக்கு(self, title: str) -> bool:
        """திரைப்படத்தை database-இலிருந்து நீக்குகிறது"""
        try:
            cleaned_title = self._தலைப்பை_சுத்தம்_செய்(title)
            
            response = self.client.table("movies").delete().eq(
                "title", cleaned_title
            ).execute()
            
            deleted_count = len(response.data) if response.data else 0
            
            if deleted_count > 0:
                logging.info(f"✅ படம் நீக்கப்பட்டது: {cleaned_title}")
                return True
            else:
                logging.warning(f"⚠️ படம் கிடைக்கவில்லை: {cleaned_title}")
                return False
                
        except Exception as e:
            logging.error(f"❌ படம் நீக்க பிழை: {e}")
            return False
    
    def கடைசி_பதிவேற்றம்(self) -> Optional[Tuple[str, datetime]]:
        """கடைசியாக பதிவேற்றப்பட்ட படத்தின் தகவல்"""
        try:
            response = self.client.table("movies").select(
                "title", "uploaded_at"
            ).order("uploaded_at", desc=True).limit(1).execute()
            
            if response.data:
                movie = response.data[0]
                title = movie['title']
                uploaded_at = datetime.fromisoformat(movie['uploaded_at'])
                return (title, uploaded_at)
            
            return None
            
        except Exception as e:
            logging.error(f"❌ கடைசி பதிவேற்றம் பெற பிழை: {e}")
            return None
    
    @staticmethod
    def _தலைப்பை_சுத்தம்_செய்(title: str) -> str:
        """தலைப்பை சுத்தம் செய்து standardize செய்கிறது"""
        cleaned = title.lower()
        cleaned = ''.join(
            c for c in cleaned 
            if unicodedata.category(c)[0] not in ['S', 'C']
        )
        cleaned = re.sub(r'[^\w\s\(\)]', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    
    @staticmethod
    def _பிழையை_பெறு(response: Any) -> str:
        """Response-இலிருந்து பிழை செய்தியை பெறுகிறது"""
        if hasattr(response, 'postgrest_error') and response.postgrest_error:
            return str(response.postgrest_error)
        elif hasattr(response, 'error') and response.error:
            return str(response.error)
        return "தெரியாத பிழை"


# ============================================================================
# உதவி செயல்பாடுகள் - Utility Functions
# ============================================================================

class MovieUtils:
    """திரைப்பட தொடர்பான உதவி செயல்பாடுகள்"""
    
    @staticmethod
    def தலைப்பை_பிரித்தெடு(filename: str) -> str:
        """கோப்பு பெயரிலிருந்து திரைப்பட தலைப்பை பிரித்தெடுக்கிறது"""
        # Telegram handles மற்றும் குழு பெயர்களை நீக்கு
        filename = re.sub(r"@\S+", "", filename)
        
        # தரம் மற்றும் format தகவல்களை நீக்கு
        quality_patterns = [
            r'\b(480p|720p|1080p|x264|x265|HEVC|HDRip|WEBRip|AAC|10bit|DS4K|UNTOUCHED)\b',
            r'\b(mkv|mp4|HD|HQ|Tamil|Telugu|Hindi|English|Dubbed|Org|Original|Proper)\b'
        ]
        
        for pattern in quality_patterns:
            filename = re.sub(pattern, "", filename, flags=re.IGNORECASE)
        
        # சிறப்பு எழுத்துக்களை இடைவெளியாக மாற்று
        filename = re.sub(r"[\[\]\(\)\{\}]", " ", filename)
        filename = re.sub(r"\s+", " ", filename).strip()
        
        # ஆண்டுடன் தலைப்பை தேடு
        match = re.search(r"([a-zA-Z\s]+)(?:\(?)(20\d{2})(?:\)?)", filename)
        if match:
            return f"{match.group(1).strip()} ({match.group(2)})"
        
        # ஆண்டு இல்லாத தலைப்பை தேடு
        title = re.split(r"[-0-9]", filename)[0].strip()
        return title
    
    @staticmethod
    def நேர_வித்தியாசம்(dt: datetime) -> str:
        """கொடுக்கப்பட்ட நேரத்திற்கும் தற்போதைய நேரத்திற்கும் உள்ள வித்தியாசம்"""
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


# ============================================================================
# Decorators - அணுகல் கட்டுப்பாடு
# ============================================================================

def admin_மட்டும்(func):
    """Admin மட்டும் அணுகக்கூடிய command-க்கான decorator"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        if user_id not in config.admin_ids:
            await update.message.reply_text(
                "❌ இந்த கட்டளை admins மட்டுமே பயன்படுத்த முடியும்"
            )
            logging.warning(f"அனுமதியற்ற அணுகல் முயற்சி: User {user_id}")
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper


# ============================================================================
# Message மேலாண்மை - Message Management
# ============================================================================

class MessageManager:
    """செய்திகளை நிர்வகிக்கும் வகுப்பு"""
    
    @staticmethod
    async def தாமதத்திற்கு_பிறகு_நீக்கு(
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        message_id: int,
        delay_seconds: int = 600
    ) -> None:
        """குறிப்பிட்ட நேரத்திற்கு பிறகு செய்தியை நீக்குகிறது"""
        await asyncio.sleep(delay_seconds)
        
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            logging.debug(f"செய்தி {message_id} நீக்கப்பட்டது (chat: {chat_id})")
        except Exception as e:
            logging.warning(f"செய்தி நீக்க பிழை {message_id}: {e}")
    
    @staticmethod
    def resolution_keyboard_உருவாக்கு(movie_name_key: str) -> InlineKeyboardMarkup:
        """Resolution தேர்வுக்கான keyboard உருவாக்குகிறது"""
        keyboard = [
            [
                InlineKeyboardButton(
                    ResolutionType.LOW.value,
                    callback_data=f"res|{movie_name_key}|{ResolutionType.LOW.value}"
                ),
                InlineKeyboardButton(
                    ResolutionType.MEDIUM.value,
                    callback_data=f"res|{movie_name_key}|{ResolutionType.MEDIUM.value}"
                ),
                InlineKeyboardButton(
                    ResolutionType.HIGH.value,
                    callback_data=f"res|{movie_name_key}|{ResolutionType.HIGH.value}"
                ),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def படப்_பரிந்துரை_keyboard(matches: List[Tuple[str, int]]) -> InlineKeyboardMarkup:
        """பரிந்துரைக்கப்பட்ட படங்களுக்கான keyboard"""
        keyboard = [
            [InlineKeyboardButton(
                m[0].title(),
                callback_data=f"movie|{m[0]}"
            )] for m in matches
        ]
        return InlineKeyboardMarkup(keyboard)


# ============================================================================
# சந்தா சரிபார்ப்பு - Subscription Verification
# ============================================================================

class SubscriptionManager:
    """பயனர் சந்தாவை சரிபார்க்கும் மேலாளர்"""
    
    @staticmethod
    async def சந்தாவை_சரிபார்(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """பயனர் தேவையான சேனலில் உள்ளாரா என சரிபார்க்கிறது"""
        try:
            user_status = await context.bot.get_chat_member(
                chat_id=config.MOVIE_UPDATE_CHANNEL_ID,
                user_id=user_id
            )
            
            is_subscribed = user_status.status in ['member', 'administrator', 'creator']
            
            if is_subscribed:
                logging.debug(f"பயனர் {user_id} சேனலில் உள்ளார்")
            else:
                logging.info(f"பயனர் {user_id} சேனலில் இல்லை")
            
            return is_subscribed
            
        except Exception as e:
            logging.error(f"❌ சந்தா சரிபார்ப்பு பிழை: {e}")
            # பிழை ஏற்பட்டால், பயனரை அனுமதி (graceful degradation)
            return True


# ============================================================================
# தேடல் இயந்திரம் - Search Engine
# ============================================================================

class SearchEngine:
    """Fuzzy search மூலம் திரைப்படங்களை தேடும் இயந்திரம்"""
    
    def __init__(self, movies_data: Dict[str, Any]):
        self.movies_data = movies_data
        self.movie_titles = list(movies_data.keys())
    
    def தேடு(self, query: str) -> Tuple[List[Tuple[str, int]], bool]:
        """
        திரைப்படங்களை தேடுகிறது
        Returns: (matches_list, is_exact_match)
        """
        cleaned_query = DatabaseManager._தலைப்பை_சுத்தம்_செய்(query)
        
        # உயர் தரமான பொருத்தங்கள் (score >= 80)
        good_matches = process.extract(
            cleaned_query,
            self.movie_titles,
            score_cutoff=80
        )
        
        if not good_matches:
            # குறைந்த தரமான பொருத்தங்கள் (score >= 60)
            broad_matches = process.extract(
                cleaned_query,
                self.movie_titles,
                limit=5,
                score_cutoff=60
            )
            return (broad_matches, False)
        
        # மிகச் சரியான பொருத்தம் (score >= 95)
        if len(good_matches) == 1 and good_matches[0][1] >= 95:
            return (good_matches, True)
        
        return (good_matches, False)
    
    def புதுப்பி(self, movies_data: Dict[str, Any]) -> None:
        """தேடல் தரவை புதுப்பிக்கிறது"""
        self.movies_data = movies_data
        self.movie_titles = list(movies_data.keys())


# ============================================================================
# பாட் செயல்பாடுகள் - Bot Operations
# ============================================================================

class BotOperations:
    """முக்கிய பாட் செயல்பாடுகள்"""
    
    def __init__(
        self,
        db: DatabaseManager,
        config: ConfigurationManager,
        search_engine: SearchEngine
    ):
        self.db = db
        self.config = config
        self.search = search_engine
        self.msg_mgr = MessageManager()
        self.sub_mgr = SubscriptionManager()
        
        # வரையறுக்கப்பட்ட நிலை தரவு
        self.user_files: Dict[int, Dict[str, Any]] = {}
        self.pending_post: Dict[int, Dict[str, Any]] = {}
    
    async def படத்தின்_போஸ்டரை_அனுப்பு(
        self,
        message: Message,
        movie_name_key: str,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """திரைப்படத்தின் போஸ்டர் மற்றும் resolution பட்டன்களை அனுப்புகிறது"""
        movie = self.search.movies_data.get(movie_name_key)
        
        if not movie:
            await message.reply_text("❌ படம் கிடைக்கவில்லை அல்லது போஸ்டர் இல்லை.")
            return
        
        caption = (
            f"🎬 *{movie_name_key.title()}*\n\n"
            f"👉 <a href='{self.config.PRIVATE_CHANNEL_LINK}'>"
            f"SK Movies Updates (News)🔔</a> - "
            f"புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும். Join பண்ணுங்க!"
        )
        
        try:
            sent = await message.reply_photo(
                movie["poster_url"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=self.msg_mgr.resolution_keyboard_உருவாக்கு(movie_name_key)
            )
            
            # 10 நிமிடங்களுக்கு பிறகு நீக்கு
            asyncio.create_task(
                self.msg_mgr.தாமதத்திற்கு_பிறகு_நீக்கு(
                    context, message.chat_id, sent.message_id
                )
            )
            
        except Exception as e:
            logging.error(f"❌ போஸ்டர் அனுப்ப பிழை: {e}")
            await message.reply_text("⚠️ போஸ்டர் அனுப்ப முடியவில்லை.")
    
    async def கோப்பை_அனுப்பு(
        self,
        chat_id: int,
        movie_name_key: str,
        resolution: str,
        context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """திரைப்பட கோப்பை குறிப்பிட்ட resolution-இல் அனுப்புகிறது"""
        movie = self.search.movies_data.get(movie_name_key)
        
        if not movie:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை"
            )
            return False
        
        file_id = movie['files'].get(resolution)
        
        if not file_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ இந்த resolution-க்கு file இல்லை."
            )
            return False
        
        try:
            caption = (
                f"🎬 *{movie_name_key.title()}* - {resolution}\n\n"
                f"👉 <a href='{self.config.PRIVATE_CHANNEL_LINK}'>"
                f"SK Movies Updates (News)🔔</a> - "
                f"புதிய படங்கள், அப்டேட்கள் அனைத்தும் இங்கே கிடைக்கும். "
                f"Join பண்ணுங்க!\n\n"
                f"⚠️ இந்த File 10 நிமிடங்களில் நீக்கப்படும். "
                f"தயவுசெய்து File ஐ உங்கள் Saved Messages-க்குப் "
                f"Forward பண்ணி வையுங்கள்."
            )
            
            sent_msg = await context.bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=caption,
                parse_mode="HTML"
            )
            
            # 10 நிமிடங்களுக்கு பிறகு நீக்கு
            asyncio.create_task(
                self.msg_mgr.தாமதத்திற்கு_பிறகு_நீக்கு(
                    context, sent_msg.chat.id, sent_msg.message_id
                )
            )
            
            return True
            
        except Exception as e:
            logging.error(f"❌ கோப்பு அனுப்ப பிழை: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ கோப்பை அனுப்ப முடியவில்லை. தயவுசெய்து மீண்டும் முயற்சிக்கவும்."
            )
            return False


# ============================================================================
# Command Handlers - கட்டளை கையாளுதல்
# ============================================================================

class CommandHandlers:
    """அனைத்து பாட் commands-க்கான handlers"""
    
    def __init__(self, bot_ops: BotOperations, db: DatabaseManager, config: ConfigurationManager):
        self.bot_ops = bot_ops
        self.db = db
        self.config = config
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start கட்டளை - வரவேற்பு செய்தி மற்றும் payload கையாளுதல்"""
        user = update.effective_user
        
        # பயனரை database-இல் பதிவு செய்
        await self.db.பயனரை_பதிவு_செய்(user)
        
        # Payload சரிபார்ப்பு (deep link support)
        payload = context.args[0] if context.args else None
        
        if payload and payload.startswith("sendfile_"):
            await self._payload_கையாளு(update, context, payload, user.id)
            return
        
        # சாதாரண வரவேற்பு செய்தி
        வரவேற்பு_செய்தி = (
            f"வணக்கம் {user.first_name}! 👋\n\n"
            f"🎬 லேட்டஸ்ட் 2025 HD தமிழ் படங்கள் வேண்டுமா? ✨\n"
            f"விளம்பரமில்லா உடனடி தேடலுடன், தரமான சினிமா அனுபவம் இங்கே! 🍿\n\n"
            f"🎬 தயவுசெய்து திரைப்படத்தின் பெயரை டைப் செய்து அனுப்புங்கள்!"
        )
        
        await update.message.reply_text(வரவேற்பு_செய்தி)
    
    async def _payload_கையாளு(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        payload: str,
        user_id: int
    ) -> None:
        """Deep link payload-ஐ கையாளுகிறது"""
        try:
            full_movie_res_string = payload[len("sendfile_"):]
            movie_name_key_parts = full_movie_res_string.rsplit('_', 1)
            
            if len(movie_name_key_parts) != 2:
                raise ValueError("தவறான payload format")
            
            movie_name_key, resolution = movie_name_key_parts
            
            logging.info(f"Payload சரிபார்ப்பு: User={user_id}, Movie={movie_name_key}, Res={resolution}")
            
            # கோப்பை அனுப்பு
            success = await self.bot_ops.கோப்பை_அனுப்பு(
                user_id, movie_name_key, resolution, context
            )
            
            if success:
                await update.message.reply_text("✅ உங்கள் கோப்பு இங்கே!")
            
        except Exception as e:
            logging.error(f"❌ Payload கையாளுதல் பிழை: {e}")
            await update.message.reply_text(
                "கோப்பைப் பெற முடியவில்லை. மீண்டும் முயற்சி செய்யுங்கள்."
            )
    
    @admin_மட்டும்
    async def totalusers_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/totalusers - மொத்த பயனர்கள் எண்ணிக்கை"""
        total = self.db.மொத்த_பயனர்கள்()
        await update.message.reply_text(f"📊 மொத்த பதிவு செய்யப்பட்ட பயனர்கள்: {total}")
    
    @admin_மட்டும்
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/status - பாட் நிலை தகவல்"""
        total_movies = self.db.மொத்த_படங்கள்()
        கடைசி_பதிவேற்றம் = self.db.கடைசி_பதிவேற்றம்()
        
        if கடைசி_பதிவேற்றம்:
            last_title, last_time = கடைசி_பதிவேற்றம்
            time_ago = MovieUtils.நேர_வித்தியாசம்(last_time)
            last_info = f'"{last_title.title()}" – _{time_ago}_'
        else:
            last_info = "இல்லை"
        
        status_text = (
            f"📊 *Bot Status:*\n"
            f"----------------------------------\n"
            f"• *மொத்த திரைப்படங்கள்:* `{total_movies}`\n"
            f"• *டேட்டாபேஸ் அளவு:* `N/A`\n"
            f"• *கடைசியாகப் பதிவேற்றம்:* {last_info}"
        )
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    
    @admin_மட்டும்
    async def addmovie_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/addmovie - புதிய திரைப்படம் சேர்க்கும் செயல்முறையை துவக்குகிறது"""
        user_id = update.message.from_user.id
        self.bot_ops.user_files[user_id] = {"poster": None, "movies": []}
        
        await update.message.reply_text(
            "📤 போஸ்டர் மற்றும் 3 movie files (480p, 720p, 1080p) "
            "வரிசையாக அனுப்பவும்."
        )
    
    @admin_மட்டும்
    async def edittitle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/edittitle - திரைப்பட தலைப்பை மாற்றுகிறது"""
        args = context.args
        
        if len(args) < 1 or "|" not in " ".join(args):
            await update.message.reply_text(
                "⚠️ Usage: `/edittitle <old title> | <new title>`",
                parse_mode="Markdown"
            )
            return
        
        full_args = " ".join(args)
        old_title_raw, new_title_raw = map(lambda x: x.strip(), full_args.split("|", 1))
        
        success = self.db.தலைப்பை_புதுப்பி(old_title_raw, new_title_raw)
        
        if success:
            # தேடல் தரவை புதுப்பி
            self.bot_ops.search.புதுப்பி(self.db.படங்களை_ஏற்று())
            
            await update.message.reply_text(
                f"✅ *{old_title_raw.title()}* இன் தலைப்பு, "
                f"*{new_title_raw.title()}* ஆக மாற்றப்பட்டது.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பழைய பெயர் கொடுக்கவும்."
            )
    
    @admin_மட்டும்
    async def deletemovie_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/deletemovie - திரைப்படத்தை நீக்குகிறது"""
        args = context.args
        
        if not args:
            await update.message.reply_text(
                "⚠️ Usage: `/deletemovie <movie name>`",
                parse_mode="Markdown"
            )
            return
        
        title_to_delete = " ".join(args).strip()
        success = self.db.படத்தை_நீக்கு(title_to_delete)
        
        if success:
            # தேடல் தரவை புதுப்பி
            self.bot_ops.search.புதுப்பி(self.db.படங்களை_ஏற்று())
            
            await update.message.reply_text(
                f"✅ *{title_to_delete.title()}* படத்தை நீக்கிவிட்டேன்.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "❌ அந்தப் படம் கிடைக்கவில்லை. சரியான பெயர் கொடுக்கவும்."
            )
    
    @admin_மட்டும்
    async def movielist_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/movielist - பட பட்டியலை காட்டுகிறது"""
        page = 1
        
        if context.args:
            try:
                page = max(1, int(context.args[0]))
            except ValueError:
                page = 1
        
        await self._movielist_பக்கம்_அனுப்பு(update.message, page)
    
    async def _movielist_பக்கம்_அனுப்பு(self, message: Message, page: int) -> None:
        """படப் பட்டியலின் குறிப்பிட்ட பக்கத்தை அனுப்புகிறது"""
        limit = 30
        offset = (page - 1) * limit
        
        movies = self.db.படங்கள்_பக்கம்(limit=limit, offset=offset)
        total_movies = self.db.மொத்த_படங்கள்()
        total_pages = (total_movies + limit - 1) // limit
        
        if not movies:
            await message.reply_text("❌ இந்த பக்கத்தில் படம் இல்லை.")
            return
        
        text = f"🎬 Movies List - பக்கம் {page}/{total_pages}\n\n"
        for i, title in enumerate(movies, start=offset + 1):
            text += f"{i}. {title.title()}\n"
        
        # Pagination பட்டன்கள்
        keyboard = []
        if page > 1:
            keyboard.append(
                InlineKeyboardButton("⬅️ Previous", callback_data=f"movielist_{page - 1}")
            )
        if page < total_pages:
            keyboard.append(
                InlineKeyboardButton("Next ➡️", callback_data=f"movielist_{page + 1}")
            )
        
        reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
        await message.reply_text(text, reply_markup=reply_markup)
    
    @admin_மட்டும்
    async def adminpanel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/adminpanel - Admin பட்டியல்"""
        admin_list = "\n".join([f"👤 {admin_id}" for admin_id in self.config.admin_ids])
        
        await update.message.reply_text(
            f"🛠️ *Admin Panel*\n\n📋 *Admin IDs:*\n{admin_list}",
            parse_mode='Markdown'
        )
    
    @admin_மட்டும்
    async def addadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/addadmin - புதிய admin சேர்க்கிறது"""
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /addadmin <user_id>")
            return
        
        try:
            new_admin_id = int(context.args[0])
            
            if new_admin_id in self.config.admin_ids:
                await update.message.reply_text("⚠️ இந்த user ஏற்கனவே ஒரு admin.")
            else:
                self.config.admin_ids.add(new_admin_id)
                await update.message.reply_text(f"✅ புதிய admin சேர்க்கப்பட்டது: {new_admin_id}")
                
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்."
            )
    
    @admin_மட்டும்
    async def removeadmin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/removeadmin - admin-ஐ நீக்குகிறது"""
        if not context.args:
            await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")
            return
        
        try:
            rem_admin_id = int(context.args[0])
            
            if rem_admin_id not in self.config.admin_ids:
                await update.message.reply_text("❌ User admin பட்டியலில் இல்லை.")
            elif len(self.config.admin_ids) == 1:
                await update.message.reply_text(
                    "⚠️ குறைந்தபட்சம் ஒரு admin இருக்க வேண்டும்."
                )
            else:
                self.config.admin_ids.remove(rem_admin_id)
                await update.message.reply_text(f"✅ Admin நீக்கப்பட்டது: {rem_admin_id}")
                
        except ValueError:
            await update.message.reply_text(
                "⚠️ Invalid user ID. தயவுசெய்து ஒரு எண்ணை வழங்கவும்."
            )
    
    @admin_மட்டும்
    async def post_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/post - குழுவிற்கு செய்தி அனுப்ப துவக்குகிறது"""
        user_id = update.effective_user.id
        
        await update.message.reply_text(
            "📤 நீங்கள் பதிவிட விரும்பும் செய்தியை "
            "(text/photo/video/document/audio) 30 வினாடிகளுக்குள் அனுப்பவும்."
        )
        
        async def expire():
            await asyncio.sleep(30)
            if self.bot_ops.pending_post.get(user_id):
                self.bot_ops.pending_post.pop(user_id, None)
                try:
                    await update.message.reply_text(
                        "⏰ நேரம் முடிந்துவிட்டது. செய்தி அனுப்ப /post ஐ மீண்டும் பயன்படுத்தவும்."
                    )
                except:
                    pass
        
        self.bot_ops.pending_post[user_id] = {}
        self.bot_ops.pending_post[user_id]['task'] = asyncio.create_task(expire())
    
    @admin_மட்டும்
    async def restart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/restart - பாட்டை மீண்டும் தொடங்குகிறது"""
        await update.message.reply_text("♻️ பாட்டை மீண்டும் தொடங்குகிறது...")
        logging.info("Admin-ஆல் restart கோரிக்கை செய்யப்பட்டது")
        sys.exit(0)


# ============================================================================
# Message Handlers - செய்தி கையாளுதல்
# ============================================================================

class MessageHandlers:
    """Text, photo, document messages-க்கான handlers"""
    
    def __init__(self, bot_ops: BotOperations, db: DatabaseManager):
        self.bot_ops = bot_ops
        self.db = db
    
    async def movie_தேடல்_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """பயனரின் படத் தேடலை கையாளுகிறது"""
        search_query = update.message.text.strip()
        
        # தேடல் தரவை புதுப்பி
        self.bot_ops.search.புதுப்பி(self.db.படங்களை_ஏற்று())
        
        if not self.bot_ops.search.movies_data:
            await update.message.reply_text(
                "டேட்டாபேஸ் காலியாக உள்ளது அல்லது ஏற்ற முடியவில்லை. பின்னர் முயற்சிக்கவும்."
            )
            return
        
        matches, is_exact = self.bot_ops.search.தேடு(search_query)
        
        if not matches:
            await update.message.reply_text(
                "❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை\n\n"
                "🎬 2025 இல் வெளியான தமிழ் HD திரைப்படங்கள் மட்டுமே இங்கு கிடைக்கும்✨.\n\n"
                "உங்களுக்கு எதுவும் சந்தேகங்கள் இருந்தால் இந்த குழுவில் கேட்கலாம் "
                "https://t.me/skmoviesdiscussion"
            )
        elif is_exact:
            # மிகச் சரியான பொருத்தம் - நேரடியாக போஸ்டர் அனுப்பு
            matched_title = matches[0][0]
            logging.info(f"சரியான பொருத்தம்: '{matched_title}'")
            await self.bot_ops.படத்தின்_போஸ்டரை_அனுப்பு(
                update.message, matched_title, context
            )
        else:
            # பல பொருத்தங்கள் - பரிந்துரைகள் காட்டு
            await update.message.reply_text(
                "⚠️ நீங்கள் இந்த படங்களில் ஏதாவது குறிப்பிடுகிறீர்களா?",
                reply_markup=MessageManager.படப்_பரிந்துரை_keyboard(matches)
            )
    
    async def file_சேமிப்பு_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin addmovie செயல்முறைக்கு வரும் files-ஐ கையாளுகிறது"""
        message = update.message
        user_id = message.from_user.id
        
        if user_id not in self.bot_ops.user_files:
            await message.reply_text("❗ முதலில் /addmovie அனுப்பவும்.")
            return
        
        # Poster கையாளுதல்
        if message.photo:
            file_id = message.photo[-1].file_id
            self.bot_ops.user_files[user_id]["poster"] = file_id
            await message.reply_text("🖼️ Poster received.")
            
            asyncio.create_task(
                MessageManager.தாமதத்திற்கு_பிறகு_நீக்கு(
                    context, message.chat_id, message.message_id
                )
            )
            return
        
        # Document/Movie file கையாளுதல்
        if message.document:
            if len(self.bot_ops.user_files[user_id]["movies"]) >= 3:
                await message.reply_text("❗ மூன்று movie files ஏற்கனவே பெற்றுவிட்டேன்.")
                return
            
            movie_file_id = message.document.file_id
            movie_file_name = message.document.file_name
            
            self.bot_ops.user_files[user_id]["movies"].append({
                "file_id": movie_file_id,
                "file_name": movie_file_name
            })
            
            await message.reply_text(
                f"🎥 Movie file {len(self.bot_ops.user_files[user_id]['movies'])} received.\n"
                f"📂 `{movie_file_name}`",
                parse_mode="Markdown"
            )
            
            asyncio.create_task(
                MessageManager.தாமதத்திற்கு_பிறகு_நீக்கு(
                    context, message.chat_id, message.message_id
                )
            )
        
        # அனைத்து files பெறப்பட்டதா என சரிபார்
        if (self.bot_ops.user_files[user_id]["poster"] and 
            len(self.bot_ops.user_files[user_id]["movies"]) == 3):
            await self._movies_சேமி(user_id, message)
    
    async def _movies_சேமி(self, user_id: int, message: Message) -> None:
        """சேகரிக்கப்பட்ட படத் தரவை database-இல் சேமிக்கிறது"""
        user_data = self.bot_ops.user_files[user_id]
        
        poster_id = user_data["poster"]
        movies_list = user_data["movies"]
        telegram_file_ids = [m["file_id"] for m in movies_list]
        
        # தலைப்பை பிரித்தெடு
        raw_title = MovieUtils.தலைப்பை_பிரித்தெடு(movies_list[0]["file_name"])
        
        # Database-இல் சேமி
        saved = self.db.படத்தை_சேமி(raw_title, poster_id, telegram_file_ids)
        
        if saved:
            # தேடல் தரவை புதுப்பி
            self.bot_ops.search.புதுப்பி(self.db.படங்களை_ஏற்று())
            
            await message.reply_text(
                f"✅ Movie saved as *{raw_title.title()}*.",
                parse_mode="Markdown"
            )
        else:
            await message.reply_text("❌ DB-ல் சேமிக்க முடியவில்லை.")
        
        # Cleanup
        self.bot_ops.user_files[user_id] = {"poster": None, "movies": []}
    
    async def post_forward_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin post செயல்முறைக்கு வரும் செய்திகளை கையாளுகிறது"""
        user_id = update.effective_user.id
        
        if user_id not in self.bot_ops.pending_post:
            return  # /post mode-இல் இல்லை
        
        msg = update.message
        
        keyboard = [
            [
                InlineKeyboardButton("SKmovies", callback_data="postgroup|SKmovies"),
                InlineKeyboardButton("SKmoviesdiscussion", callback_data="postgroup|SKmoviesdiscussion"),
                InlineKeyboardButton("Both", callback_data="postgroup|both"),
            ]
        ]
        
        await msg.reply_text(
            "📌 எந்த group-க்கு forward செய்ய விரும்புகிறீர்கள்?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        self.bot_ops.pending_post[user_id]['message'] = msg
    
    async def general_tracker(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """அனைத்து பயனர் செயல்பாடுகளையும் கண்காணிக்கிறது"""
        if update.effective_user:
            await self.db.பயனரை_பதிவு_செய்(update.effective_user)


# ============================================================================
# Callback Query Handlers - பட்டன் கிளிக் கையாளுதல்
# ============================================================================

class CallbackHandlers:
    """Inline keyboard பட்டன் கிளிக்குகளை கையாளுகிறது"""
    
    def __init__(
        self,
        bot_ops: BotOperations,
        config: ConfigurationManager,
        cmd_handlers: CommandHandlers
    ):
        self.bot_ops = bot_ops
        self.config = config
        self.cmd_handlers = cmd_handlers
    
    async def resolution_கிளிக்_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Resolution பட்டன் கிளிக்கை கையாளுகிறது"""
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        
        if not query.data or "|" not in query.data:
            await query.message.reply_text("தவறான கோரிக்கை.")
            return
        
        _, movie_name_key, resolution = query.data.split("|", 2)
        
        # சந்தா சரிபார்ப்பு
        is_subscribed = await SubscriptionManager.சந்தாவை_சரிபார்(user_id, context)
        
        if not is_subscribed:
            await query.message.reply_text(
                "⚠️ இந்த திரைப்படத்தைப் பெற, முதலில் நமது சேனலில் இணையவும்.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "சேனலில் இணைய இங்கே கிளிக் செய்யவும்",
                        url=self.config.PRIVATE_CHANNEL_LINK
                    )],
                    [InlineKeyboardButton(
                        "மீண்டும் முயற்சிக்கவும்",
                        callback_data=f"tryagain|{movie_name_key}|{resolution}"
                    )]
                ])
            )
            return
        
        # கோப்பை அனுப்பு
        await self.bot_ops.கோப்பை_அனுப்பு(
            update.effective_chat.id,
            movie_name_key,
            resolution,
            context
        )
    
    async def tryagain_கிளிக்_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """'மீண்டும் முயற்சிக்கவும்' பட்டனை கையாளுகிறது"""
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('|')
        if len(data) != 3:
            await query.message.reply_text("தவறான கோரிக்கை.")
            return
        
        movie_name_key = data[1]
        resolution = data[2]
        
        # மீண்டும் சந்தா சரிபார்ப்பு
        is_subscribed = await SubscriptionManager.சந்தாவை_சரிபார்(
            query.from_user.id,
            context
        )
        
        if is_subscribed:
            await query.message.edit_text(
                "✅ நீங்கள் இப்போது சேனலில் இணைந்துவிட்டீர்கள். "
                "உங்கள் திரைப்படம் அனுப்பப்படுகிறது...",
                parse_mode="Markdown"
            )
            
            # கோப்பை அனுப்பு
            await self.bot_ops.கோப்பை_அனுப்பு(
                query.message.chat_id,
                movie_name_key,
                resolution,
                context
            )
        else:
            await query.message.edit_text(
                "⚠️ நீங்கள் இன்னும் சேனலில் இணையவில்லை. "
                "முதலில் இணைந்த பிறகு மீண்டும் முயற்சிக்கவும்.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "சேனலில் இணைய இங்கே கிளிக் செய்யவும்",
                        url=self.config.PRIVATE_CHANNEL_LINK
                    )],
                    [InlineKeyboardButton(
                        "மீண்டும் முயற்சிக்கவும்",
                        callback_data=query.data
                    )]
                ])
            )
    
    async def movie_பட்டன்_கிளிக்_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """பரிந்துரை படப் பட்டன் கிளிக்கை கையாளுகிறது"""
        query = update.callback_query
        await query.answer()
        
        if not query.data or "|" not in query.data:
            await query.message.reply_text("தவறான கோரிக்கை.")
            return
        
        _, movie_name_key = query.data.split("|", 1)
        
        if movie_name_key in self.bot_ops.search.movies_data:
            await self.bot_ops.படத்தின்_போஸ்டரை_அனுப்பு(
                query.message,
                movie_name_key,
                context
            )
        else:
            await query.message.reply_text(
                "❌ மன்னிக்கவும், இந்தத் திரைப்படம் எங்கள் Database-இல் இல்லை"
            )
    
    async def movielist_pagination_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """படப் பட்டியல் pagination-ஐ கையாளுகிறது"""
        query = update.callback_query
        await query.answer()
        
        if not query.data.startswith("movielist_"):
            return
        
        page = int(query.data.split("_")[1])
        
        limit = 30
        offset = (page - 1) * limit
        movies = self.bot_ops.db.படங்கள்_பக்கம்(limit=limit, offset=offset)
        total_movies = self.bot_ops.db.மொத்த_படங்கள்()
        total_pages = (total_movies + limit - 1) // limit
        
        if not movies:
            await query.message.edit_text("❌ இந்த பக்கத்தில் படம் இல்லை.")
            return
        
        text = f"🎬 Movies List - பக்கம் {page}/{total_pages}\n\n"
        for i, title in enumerate(movies, start=offset + 1):
            text += f"{i}. {title.title()}\n"
        
        # Pagination பட்டன்கள்
        keyboard = []
        if page > 1:
            keyboard.append(
                InlineKeyboardButton("⬅️ Previous", callback_data=f"movielist_{page - 1}")
            )
        if page < total_pages:
            keyboard.append(
                InlineKeyboardButton("Next ➡️", callback_data=f"movielist_{page + 1}")
            )
        
        reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
        await query.message.edit_text(text, reply_markup=reply_markup)
    
    async def postgroup_கிளிக்_handler(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Post செய்யும் group தேர்வை கையாளுகிறது"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if user_id not in self.bot_ops.pending_post:
            await query.message.reply_text(
                "⏰ நேரம் முடிந்துவிட்டது. செய்தி அனுப்ப /post ஐ மீண்டும் பயன்படுத்தவும்."
            )
            return
        
        choice = query.data.split('|')[1]
        msg = self.bot_ops.pending_post[user_id]['message']
        
        # Group IDs தேர்வு
        group_ids = []
        if choice == "SKmovies":
            group_ids = [self.config.SKMOVIES_GROUP_ID]
        elif choice == "SKmoviesdiscussion":
            group_ids = [self.config.SKMOVIESDISCUSSION_GROUP_ID]
        elif choice == "both":
            group_ids = [
                self.config.SKMOVIES_GROUP_ID,
                self.config.SKMOVIESDISCUSSION_GROUP_ID
            ]
        
        # குழுக்களுக்கு அனுப்பு
        for gid in group_ids:
            try:
                if msg.text:
                    await context.bot.send_message(chat_id=gid, text=msg.text)
                elif msg.photo:
                    await context.bot.send_photo(
                        chat_id=gid,
                        photo=msg.photo[-1].file_id,
                        caption=msg.caption
                    )
                elif msg.video:
                    await context.bot.send_video(
                        chat_id=gid,
                        video=msg.video.file_id,
                        caption=msg.caption
                    )
                elif msg.document:
                    await context.bot.send_document(
                        chat_id=gid,
                        document=msg.document.file_id,
                        caption=msg.caption
                    )
                elif msg.audio:
                    await context.bot.send_audio(
                        chat_id=gid,
                        audio=msg.audio.file_id,
                        caption=msg.caption
                    )
                elif msg.voice:
                    await context.bot.send_voice(
                        chat_id=gid,
                        voice=msg.voice.file_id
                    )
                
                await query.message.reply_text(f"✅ Forwarded to {gid}")
                
            except Exception as e:
                logging.error(f"❌ Forward தோல்வி {gid}: {e}")
                await query.message.reply_text(f"❌ Forward failed to {gid}: {e}")
        
        # Cleanup
        self.bot_ops.pending_post.pop(user_id, None)


# ============================================================================
# முக்கிய பாட் அமைப்பு - Main Bot Setup
# ============================================================================

class TamilMovieBot:
    """முக்கிய பாட் வகுப்பு - எல்லா கூறுகளையும் ஒருங்கிணைக்கிறது"""
    
    def __init__(self):
        """பாட்டின் அனைத்து கூறுகளையும் துவக்குகிறது"""
        # Logging அமைப்பு
        LoggerSetup.configure()
        
        # கட்டமைப்பு ஏற்றுதல்
        self.config = ConfigurationManager()
        logging.info("✅ கட்டமைப்பு வெற்றிகரமாக ஏற்றப்பட்டது")
        
        # Database இணைப்பு
        self.db = DatabaseManager(self.config.SUPABASE_URL, self.config.SUPABASE_KEY)
        
        # திரைப்படங்களை ஏற்றுதல்
        movies_data = self.db.படங்களை_ஏற்று()
        
        # தேடல் இயந்திரம்
        self.search_engine = SearchEngine(movies_data)
        
        # பாட் செயல்பாடுகள்
        self.bot_ops = BotOperations(self.db, self.config, self.search_engine)
        
        # Handlers
        self.cmd_handlers = CommandHandlers(self.bot_ops, self.db, self.config)
        self.msg_handlers = MessageHandlers(self.bot_ops, self.db)
        self.callback_handlers = CallbackHandlers(
            self.bot_ops,
            self.config,
            self.cmd_handlers
        )
        
        logging.info("✅ பாட் கூறுகள் வெற்றிகரமாக துவக்கப்பட்டன")
    
    def _handlers_பதிவு_செய்(self, app) -> None:
        """அனைத்து handlers-ஐயும் application-இல் பதிவு செய்கிறது"""
        
        # Command handlers
        app.add_handler(CommandHandler("start", self.cmd_handlers.start_command))
        app.add_handler(CommandHandler("totalusers", self.cmd_handlers.totalusers_command))
        app.add_handler(CommandHandler("status", self.cmd_handlers.status_command))
        app.add_handler(CommandHandler("addmovie", self.cmd_handlers.addmovie_command))
        app.add_handler(CommandHandler("edittitle", self.cmd_handlers.edittitle_command))
        app.add_handler(CommandHandler("deletemovie", self.cmd_handlers.deletemovie_command))
        app.add_handler(CommandHandler("movielist", self.cmd_handlers.movielist_command))
        app.add_handler(CommandHandler("adminpanel", self.cmd_handlers.adminpanel_command))
        app.add_handler(CommandHandler("addadmin", self.cmd_handlers.addadmin_command))
        app.add_handler(CommandHandler("removeadmin", self.cmd_handlers.removeadmin_command))
        app.add_handler(CommandHandler("post", self.cmd_handlers.post_command))
        app.add_handler(CommandHandler("restart", self.cmd_handlers.restart_command))
        
        # Message handlers (முன்னுரிமை வரிசையில்)
        # Priority -1: Post forward செய்தி பிடிப்பு
        app.add_handler(
            MessageHandler(
                filters.ALL & ~filters.COMMAND,
                self.msg_handlers.post_forward_handler
            ),
            -1
        )
        
        # Priority 0: File சேமிப்பு (addmovie செயல்முறைக்கு)
        app.add_handler(
            MessageHandler(
                filters.PHOTO | filters.Document.ALL,
                self.msg_handlers.file_சேமிப்பு_handler
            )
        )
        
        # Priority 0: படத் தேடல்
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.msg_handlers.movie_தேடல்_handler
            )
        )
        
        # Callback query handlers
        app.add_handler(
            CallbackQueryHandler(
                self.callback_handlers.resolution_கிளிக்_handler,
                pattern=r"^res\|"
            )
        )
        
        app.add_handler(
            CallbackQueryHandler(
                self.callback_handlers.tryagain_கிளிக்_handler,
                pattern=r"^tryagain\|"
            )
        )
        
        app.add_handler(
            CallbackQueryHandler(
                self.callback_handlers.movie_பட்டன்_கிளிக்_handler,
                pattern=r"^movie\|"
            )
        )
        
        app.add_handler(
            CallbackQueryHandler(
                self.callback_handlers.movielist_pagination_handler,
                pattern=r"^movielist_"
            )
        )
        
        app.add_handler(
            CallbackQueryHandler(
                self.callback_handlers.postgroup_கிளிக்_handler,
                pattern=r"^postgroup\|"
            )
        )
        
        logging.info("✅ அனைத்து handlers வெற்றிகரமாக பதிவு செய்யப்பட்டன")
    
    async def இயக்கு(self) -> None:
        """பாட்டை இயக்குகிறது"""
        try:
            # Application உருவாக்குதல்
            app = ApplicationBuilder().token(self.config.TOKEN).build()
            
            # Handlers பதிவு செய்தல்
            self._handlers_பதிவு_செய்(app)
            
            logging.info("🚀 தமிழ் திரைப்பட பாட் தொடங்குகிறது...")
            logging.info(f"📊 {len(self.search_engine.movies_data)} திரைப்படங்கள் தயார் நிலையில்")
            logging.info(f"👥 {len(self.config.admin_ids)} admins கட்டமைக்கப்பட்டுள்ளனர்")
            
            # பாட்டை polling mode-இல் இயக்குதல்
            await app.run_polling(drop_pending_updates=False)
            
        except KeyboardInterrupt:
            logging.info("⏹️ பாட் நிறுத்தப்படுகிறது (KeyboardInterrupt)")
        except Exception as e:
            logging.error(f"❌ பாட் இயக்க பிழை: {e}", exc_info=True)
            raise
        finally:
            logging.info("👋 பாட் வெற்றிகரமாக நிறுத்தப்பட்டது")


# ============================================================================
# நிரல் நுழைவு புள்ளி - Program Entry Point
# ============================================================================

async def main():
    """முக்கிய நிரல் நுழைவு புள்ளி"""
    # Nest asyncio இயக்குதல் (Jupyter notebooks போன்ற சூழல்களுக்கு)
    nest_asyncio.apply()
    
    try:
        bot = TamilMovieBot()
        await bot.இயக்கு()
    except Exception as e:
        logging.critical(f"💥 பாட் துவக்கத்தில் மீளமுடியாத பிழை: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    """நிரல் நேரடியாக இயக்கப்படும்போது"""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⏹️ பயனரால் நிறுத்தப்பட்டது")
    except Exception as e:
        logging.critical(f"💥 நிரல் இயக்க பிழை: {e}", exc_info=True)
        sys.exit(1)