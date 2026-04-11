import logging
import os
import secrets
from datetime import datetime, timezone, timedelta
from math import radians, sin, cos, sqrt, atan2
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, select, func

# ==================== Load Environment ====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")

DATABASE_URL = "sqlite+aiosqlite:///locations.db"

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS")
ADMIN_IDS = []
if ADMIN_IDS_RAW:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# ==================== Database Models ====================
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String(64))
    share_code = Column(String(16), unique=True)
    latitude = Column(Float)
    longitude = Column(Float)
    is_public = Column(Boolean, default=True)
    last_updated = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Visit(Base):
    __tablename__ = 'visits'
    id = Column(Integer, primary_key=True)
    visitor_id = Column(Integer)
    target_id = Column(Integer)
    distance_km = Column(Float)
    visited_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# ==================== Async Database Setup ====================
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

@asynccontextmanager
async def get_session() -> AsyncSession:
    """Async context manager for database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db():
    """Create tables asynchronously."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ==================== Logging ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== Rate Limiting ====================
# Simple in-memory rate limiter: { (user_id, action): last_timestamp }
_rate_limit_store = {}

def rate_limit(action: str, interval_seconds: int = 5):
    """Decorator factory for rate limiting per user+action."""
    def decorator(func):
        async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            key = (user_id, action)
            now = datetime.now(timezone.utc)
            last = _rate_limit_store.get(key)
            if last and (now - last).total_seconds() < interval_seconds:
                await update.message.reply_text(f"⏳ Please wait {interval_seconds} seconds before using this again.")
                return
            _rate_limit_store[key] = now
            return await func(self, update, context, *args, **kwargs)
        return wrapper
    return decorator

# ==================== Helper Functions ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ==================== Bot Core ====================
class LocationBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()

    @staticmethod
    def generate_share_code(user_id: int) -> str:
        """Generate unique share code."""
        return secrets.token_hex(4).upper() + str(user_id)[-4:]

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Haversine distance calculation in km."""
        R = 6371.0
        lat1_rad, lon1_rad = radians(lat1), radians(lon1)
        lat2_rad, lon2_rad = radians(lat2), radians(lon2)
        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad
        a = sin(dlat/2)**2 + cos(lat1_rad)*cos(lat2_rad)*sin(dlon/2)**2
        return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

    # ==================== User Commands ====================
    @rate_limit("start", interval_seconds=3)
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if args:
            await self._handle_share_link(update, context, args[0])
            return

        user = update.effective_user
        async with get_session() as session:
            result = await session.execute(select(User).filter_by(telegram_id=user.id))
            db_user = result.scalar_one_or_none()

            if not db_user:
                share_code = self.generate_share_code(user.id)
                db_user = User(
                    telegram_id=user.id,
                    username=(user.username or "")[:64],
                    share_code=share_code,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(db_user)
                await session.flush()

            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={db_user.share_code}"

        keyboard = [[KeyboardButton("Share Location", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            f"Welcome!\n\n"
            f"Your personal link:\n{link}\n\n"
            f"Share your location using the button below.\n\n"
            f"Privacy: you can change your visibility with /privacy",
            reply_markup=reply_markup
        )

    async def _handle_share_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, share_code: str):
        visitor = update.effective_user

        async with get_session() as session:
            result = await session.execute(select(User).filter_by(share_code=share_code))
            target_user = result.scalar_one_or_none()

            if not target_user:
                await update.message.reply_text("Invalid link.")
                return

            if not target_user.is_public and target_user.telegram_id != visitor.id:
                await update.message.reply_text("This user has set their location to private.")
                return

            context.user_data['target_user'] = {
                'id': target_user.id,
                'telegram_id': target_user.telegram_id,
                'username': target_user.username or "User"
            }

        keyboard = [[KeyboardButton("Share Location", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            f"Do you want to check distance to @{context.user_data['target_user']['username']}?",
            reply_markup=reply_markup
        )

    @rate_limit("location_update", interval_seconds=30)  # own location update
    @rate_limit("location_query", interval_seconds=5)    # distance query (separate limit)
    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        loc = update.message.location
        lat, lon = loc.latitude, loc.longitude

        # Case 1: distance query mode
        if 'target_user' in context.user_data:
            target_info = context.user_data.pop('target_user')

            async with get_session() as session:
                result = await session.execute(select(User).filter_by(telegram_id=target_info['telegram_id']))
                target_user = result.scalar_one_or_none()

                if not target_user or target_user.latitude is None:
                    await update.message.reply_text("Target location not available.")
                    return

                if target_user.last_updated is None:
                    await update.message.reply_text("Link expired (no location shared).")
                    return

                if datetime.now(timezone.utc) - target_user.last_updated > timedelta(hours=24):
                    await update.message.reply_text("Link expired (location older than 24h).")
                    return

                distance = self.calculate_distance(
                    lat, lon,
                    target_user.latitude,
                    target_user.longitude
                )

                visit = Visit(
                    visitor_id=user.id,
                    target_id=target_user.telegram_id,
                    distance_km=distance
                )
                session.add(visit)

                if distance < 1:
                    dist_str = f"{distance*1000:.0f} m"
                elif distance < 10:
                    dist_str = f"{distance:.1f} km"
                else:
                    dist_str = f"{distance:.0f} km"

                await update.message.reply_text(f"📏 Distance: {dist_str}")
            return

        # Case 2: update own location
        async with get_session() as session:
            result = await session.execute(select(User).filter_by(telegram_id=user.id))
            db_user = result.scalar_one_or_none()

            if not db_user:
                await update.message.reply_text("Please register first using /start")
                return

            db_user.latitude = lat
            db_user.longitude = lon
            db_user.last_updated = datetime.now(timezone.utc)

            await update.message.reply_text("📍 Location updated successfully.")

    async def privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set or show location privacy status."""
        user = update.effective_user
        args = context.args

        async with get_session() as session:
            result = await session.execute(select(User).filter_by(telegram_id=user.id))
            db_user = result.scalar_one_or_none()

            if not db_user:
                await update.message.reply_text("Please use /start first.")
                return

            if not args:
                status = "public" if db_user.is_public else "private"
                await update.message.reply_text(
                    f"🔒 Your location is currently **{status}**.\n"
                    f"To change: `/privacy on` (public) or `/privacy off` (private)",
                    parse_mode="Markdown"
                )
                return

            cmd = args[0].lower()
            if cmd in ("on", "public", "true", "yes"):
                db_user.is_public = True
                await update.message.reply_text("✅ Your location is now **public**. Anyone with your link can see distance.")
            elif cmd in ("off", "private", "false", "no"):
                db_user.is_public = False
                await update.message.reply_text("🔐 Your location is now **private**. Only you can see your own location.")
            else:
                await update.message.reply_text("Usage: `/privacy [on|off]` or just `/privacy` to check status", parse_mode="Markdown")

    # ==================== Admin Commands ====================
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Access denied.")
            return

        async with get_session() as session:
            total_users = await session.scalar(select(func.count()).select_from(User))
            users_with_location = await session.scalar(select(func.count()).filter(User.latitude.isnot(None)).select_from(User))
            total_visits = await session.scalar(select(func.count()).select_from(Visit))
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            visits_today = await session.scalar(select(func.count()).filter(Visit.visited_at >= today).select_from(Visit))

        await update.message.reply_text(
            f"📊 **Statistics**\n"
            f"👥 Total users: {total_users}\n"
            f"📍 With location: {users_with_location}\n"
            f"👣 Total visits: {total_visits}\n"
            f"📅 Today visits: {visits_today}",
            parse_mode="Markdown"
        )

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Access denied.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /broadcast message")
            return

        text = " ".join(context.args)

        async with get_session() as session:
            result = await session.execute(select(User.telegram_id))
            user_ids = [row[0] for row in result.all()]

        success = 0
        fail = 0

        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                success += 1
            except Exception as e:
                logger.warning(f"Broadcast failed to {uid}: {e}")
                fail += 1

        await update.message.reply_text(f"📢 Broadcast done. Success: {success}, Failed: {fail}")

    # ==================== Handlers ====================
    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("privacy", self.privacy))
        self.app.add_handler(CommandHandler("stats", self.stats))
        self.app.add_handler(CommandHandler("broadcast", self.broadcast))
        self.app.add_handler(MessageHandler(filters.LOCATION, self.handle_location))

    async def post_init(self):
        """Initialize database tables after bot starts."""
        await init_db()
        logger.info("Database initialized")

    def run(self):
        print("Bot is starting...")
        # Register post_init callback
        self.app.post_init = self.post_init
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== Entry Point ====================
if __name__ == "__main__":
    bot = LocationBot()
    bot.run()