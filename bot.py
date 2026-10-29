import logging
import os
import secrets
import asyncio
from datetime import datetime, timezone, timedelta
from math import radians, sin, cos, sqrt, atan2
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram.error import RetryAfter, TelegramError

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, select, func, Index

# ==================== Load Environment ====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

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
    telegram_id = Column(Integer, unique=True, index=True) # 增加索引提高查询效率
    username = Column(String(64))
    share_code = Column(String(32), unique=True, index=True) # 增加索引
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

# 为统计功能增加复合索引
Index('idx_visit_stats', Visit.target_id, Visit.visited_at)

# ==================== Async Database Setup ====================
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

@asynccontextmanager
async def get_session() -> AsyncSession:
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
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ==================== Logging ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Telegram embeds the bot token in request URLs. Keep application INFO logs,
# but suppress HTTP request traces, including when the root logger is DEBUG.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_rate_limit_store = {}

def rate_limit(action: str, interval_seconds: int = 5):
    def decorator(func):
        async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user = update.effective_user
            message = update.effective_message
            if user is None or message is None:
                return
            user_id = user.id
            key = (user_id, action)
            now = datetime.now(timezone.utc)
            last = _rate_limit_store.get(key)
            if last and (now - last).total_seconds() < interval_seconds:
                # 默默忽略或回复，这里选择提醒
                await message.reply_text(f"⏳ 速度太快了，请等待 {interval_seconds} 秒。")
                return
            _rate_limit_store[key] = now
            return await func(self, update, context, *args, **kwargs)
        return wrapper
    return decorator

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_location_expired(updated_at: datetime | None) -> bool:
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at > timedelta(hours=24)

# ==================== Bot Core ====================
class LocationBot:
    def __init__(self):
        # 优化：在生成 Application 时增加基础配置
        if not BOT_TOKEN:
            raise ValueError("BOT_TOKEN environment variable is not set")
        self.app = (Application.builder().token(BOT_TOKEN)
                    .post_init(self.post_init).post_shutdown(self.post_shutdown).build())
        self.setup_handlers()

    @staticmethod
    def generate_share_code() -> str:
        """优化：使用完全随机的 token，不再包含 user_id 后缀，增强安全性"""
        return secrets.token_urlsafe(12)

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        lat1_rad, lon1_rad = radians(lat1), radians(lon1)
        lat2_rad, lon2_rad = radians(lat2), radians(lon2)
        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad
        a = sin(dlat/2)**2 + cos(lat1_rad)*cos(lat2_rad)*sin(dlon/2)**2
        return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

    @rate_limit("start", interval_seconds=3)
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        user = update.effective_user

        async with get_session() as session:
            result = await session.execute(select(User).filter_by(telegram_id=user.id))
            db_user = result.scalar_one_or_none()

            if not db_user:
                # 修复：生成不带 ID 的安全 Code
                share_code = self.generate_share_code()
                db_user = User(
                    telegram_id=user.id,
                    username=(user.username or "User")[:64],
                    share_code=share_code,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(db_user)
                await session.flush()

            if args:
                await self._handle_share_link(update, context, args[0], db_user)
                return

            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={db_user.share_code}"

        keyboard = [[KeyboardButton("📍 分享我的位置", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            f"你好！你的专属距离查询链接为：\n\n`{link}`\n\n"
            f"1. 点击下方按钮分享你的位置。\n"
            f"2. 将链接发给好友，他们分享位置后你即可看到距离。\n"
            f"3. 隐私设置：使用 /privacy 修改状态。",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def _handle_share_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, share_code: str, current_db_user: User):
        async with get_session() as session:
            result = await session.execute(select(User).filter_by(share_code=share_code))
            target_user = result.scalar_one_or_none()

            if not target_user:
                await update.message.reply_text("❌ 无效的链接。")
                return

            if target_user.telegram_id == update.effective_user.id:
                await update.message.reply_text("这是你自己的链接哦！")
                return

            if not target_user.is_public:
                await update.message.reply_text("🔒 该用户已开启隐私保护，暂时无法查询。")
                return

            context.user_data['target_info'] = {
                'id': target_user.id,
                'telegram_id': target_user.telegram_id,
                'username': target_user.username or "未知用户"
            }

        keyboard = [[KeyboardButton("📍 点击分享位置并计算距离", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            f"你想查看与 @{context.user_data['target_info']['username']} 的距离吗？\n请点击下方按钮分享你的实时位置。",
            reply_markup=reply_markup
        )

    @rate_limit("location_process", interval_seconds=5)
    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        message = update.effective_message
        if message is None or message.location is None:
            return
        loc = message.location
        lat, lon = loc.latitude, loc.longitude

        async with get_session() as session:
            # 优化：无论何种情况，先更新自己的位置信息（双向更新）
            result = await session.execute(select(User).filter_by(telegram_id=user.id))
            db_user = result.scalar_one_or_none()
            
            if db_user:
                db_user.latitude = lat
                db_user.longitude = lon
                db_user.last_updated = datetime.now(timezone.utc)
            
            # 情况：正在查询他人距离
            if 'target_info' in context.user_data:
                target_info = context.user_data.pop('target_info')
                
                result_target = await session.execute(select(User).filter_by(telegram_id=target_info['telegram_id']))
                target_user = result_target.scalar_one_or_none()

                if not target_user or not target_user.is_public or target_user.latitude is None:
                    await message.reply_text("对方尚未分享位置，无法计算。")
                    return

                # 检查对方位置是否过期（24小时）
                if is_location_expired(target_user.last_updated):
                    await message.reply_text("⏰ 对方的位置信息已过期（超过24小时未更新）。")
                    return

                distance = self.calculate_distance(lat, lon, target_user.latitude, target_user.longitude)

                # 记录访问
                session.add(Visit(visitor_id=user.id, target_id=target_user.telegram_id, distance_km=distance))

                dist_str = f"{distance*1000:.0f} m" if distance < 1 else f"{distance:.2f} km"
                
                privacy_note = "" if db_user and db_user.is_public else "\n\n⚠️ *提示：你当前处于私密模式，对方无法通过你的链接反向查询。*"
                await message.reply_text(f"📏 直线距离: *{dist_str}*{privacy_note}", parse_mode="Markdown")
                return

            await message.reply_text("✅ 你的位置已更新，链接现在生效了！")

    async def privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        args = context.args

        async with get_session() as session:
            result = await session.execute(select(User).filter_by(telegram_id=user.id))
            db_user = result.scalar_one_or_none()

            if not db_user:
                await update.message.reply_text("请先发送 /start 注册。")
                return

            if not args:
                status = "公开 (Public)" if db_user.is_public else "私密 (Private)"
                await update.message.reply_text(
                    f"当前状态：*{status}*\n\n"
                    f"开启公开：`/privacy on` (他人可查询)\n"
                    f"开启私密：`/privacy off` (他人无法查询)",
                    parse_mode="Markdown"
                )
                return

            cmd = args[0].lower()
            if cmd in ("on", "public", "true"):
                db_user.is_public = True
                msg = "✅ 设置成功：现在持有链接的人可以查看与你的距离。"
            else:
                db_user.is_public = False
                msg = "🔐 设置成功：你的位置现在已隐藏，他人无法通过链接查询。"
            
            await update.message.reply_text(msg)

    # ==================== Admin Commands ====================
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id): return

        async with get_session() as session:
            total_users = await session.scalar(select(func.count()).select_from(User))
            active_locs = await session.scalar(select(func.count()).filter(User.last_updated > datetime.now(timezone.utc) - timedelta(hours=24)))
            total_vists = await session.scalar(select(func.count()).select_from(Visit))

        await update.message.reply_text(
            f"📊 **数据统计**\n"
            f"• 总用户数: {total_users}\n"
            f"• 24h内活跃位置: {active_locs}\n"
            f"• 总查询次数: {total_vists}",
            parse_mode="Markdown"
        )

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """优化：增加频率控制和异常捕获，防止被 Telegram 封禁"""
        if not is_admin(update.effective_user.id): return
        if not context.args:
            await update.message.reply_text("用法: /broadcast [消息内容]")
            return

        text = " ".join(context.args)
        async with get_session() as session:
            result = await session.execute(select(User.telegram_id))
            uids = [row[0] for row in result.all()]

        sent, failed = 0, 0
        status_msg = await update.message.reply_text(f"🚀 正在发送给 {len(uids)} 位用户...")

        for uid in uids:
            # Retry the same recipient once, and count every final outcome.
            for attempt in range(2):
                try:
                    await context.bot.send_message(chat_id=uid, text=text)
                    sent += 1
                    if sent % 20 == 0:
                        await asyncio.sleep(1)
                    break
                except RetryAfter as error:
                    if attempt:
                        failed += 1
                        break
                    delay = error.retry_after
                    if isinstance(delay, timedelta):
                        delay = delay.total_seconds()
                    await asyncio.sleep(delay)
                except TelegramError:
                    failed += 1
                    break

        await status_msg.edit_text(f"📢 广播完成！\n✅ 成功: {sent}\n❌ 失败: {failed}")

    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("privacy", self.privacy))
        self.app.add_handler(CommandHandler("stats", self.stats))
        self.app.add_handler(CommandHandler("broadcast", self.broadcast))
        self.app.add_handler(MessageHandler(filters.LOCATION, self.handle_location))

    async def post_init(self, application: Application):
        await init_db()
        logger.info("Database & Tables initialized.")

    async def post_shutdown(self, application: Application):
        await engine.dispose()

    def run(self):
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    LocationBot().run()
