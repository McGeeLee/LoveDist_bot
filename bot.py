import logging
import os
import secrets
from datetime import datetime, timedelta
from contextlib import contextmanager
from math import radians, sin, cos, sqrt, atan2

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, func
from sqlalchemy.orm import declarative_base, sessionmaker

# ==================== 配置 ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("请设置环境变量 BOT_TOKEN")

DATABASE_URL = "sqlite:///locations.db"

# 管理员ID列表（与 BOT_TOKEN 一样的导入方式）
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS")
if ADMIN_IDS_RAW is None:
    ADMIN_IDS = []
else:
    ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

# ==================== 数据库模型 ====================
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
    created_at = Column(DateTime, default=datetime.utcnow)

class Visit(Base):
    __tablename__ = 'visits'
    id = Column(Integer, primary_key=True)
    visitor_id = Column(Integer)
    target_id = Column(Integer)
    distance_km = Column(Float)
    visited_at = Column(DateTime, default=datetime.utcnow)

# ==================== 数据库会话 ====================
engine = create_engine(DATABASE_URL, echo=False)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(engine)

@contextmanager
def get_session():
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ==================== 日志 ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== 辅助函数 ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ==================== Bot 主逻辑 ====================
class LocationBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()

    @staticmethod
    def generate_share_code(user_id: int) -> str:
        """生成唯一分享码（内部使用）"""
        return secrets.token_hex(4).upper() + str(user_id)[-4:]

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371.0
        lat1_rad, lon1_rad = radians(lat1), radians(lon1)
        lat2_rad, lon2_rad = radians(lat2), radians(lon2)
        dlon = lon2_rad - lon1_rad
        dlat = lat2_rad - lat1_rad
        a = sin(dlat/2)**2 + cos(lat1_rad)*cos(lat2_rad)*sin(dlon/2)**2
        return R * (2 * atan2(sqrt(a), sqrt(1 - a)))

    # ---------- 普通用户命令 ----------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if args:
            await self._handle_share_link(update, context, args[0])
            return

        user = update.effective_user
        with get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=user.id).first()
            if not db_user:
                share_code = self.generate_share_code(user.id)
                db_user = User(
                    telegram_id=user.id,
                    username=(user.username or "")[:64],
                    share_code=share_code,
                    created_at=datetime.utcnow()
                )
                session.add(db_user)
            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={db_user.share_code}"

        keyboard = [[KeyboardButton("📍 分享位置", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            f"🎉 欢迎使用本Bot！\n\n"
            f"🔗 你的专属链接（分享给朋友, 24h有效期）：\n{link}\n\n"
            f"📌 点击下方按钮分享你的位置，朋友点击你的链接后发送位置，即可知道与你相距多远。",
            reply_markup=reply_markup
        )

    async def _handle_share_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE, share_code: str):
        visitor = update.effective_user
        with get_session() as session:
            target_user = session.query(User).filter_by(share_code=share_code).first()
            if not target_user:
                await update.message.reply_text("❌ 无效的分享链接！")
                return
            if not target_user.is_public and target_user.telegram_id != visitor.id:
                await update.message.reply_text("🔒 该用户的位置已设置为私密")
                return
            # 可选：提前检查位置是否过期（但为了统一，在 handle_location 中检查）
            context.user_data['target_user'] = {
                'id': target_user.id,
                'telegram_id': target_user.telegram_id,
                'username': target_user.username or "用户"
            }

        keyboard = [[KeyboardButton("📍 分享位置", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            f"📍 你想知道离 @{context.user_data['target_user']['username']} 有多远吗？\n请点击下方按钮分享你的位置。",
            reply_markup=reply_markup
        )

    async def handle_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        loc = update.message.location
        lat, lon = loc.latitude, loc.longitude

        # 情况1：查询距离模式（用户先点了分享链接）
        if 'target_user' in context.user_data:
            target_info = context.user_data.pop('target_user')
            with get_session() as session:
                target_user = session.query(User).filter_by(telegram_id=target_info['telegram_id']).first()
                if not target_user or target_user.latitude is None:
                    await update.message.reply_text("❌ 对方尚未设置位置，无法计算距离")
                    return

                # ========== 新增：检查对方位置是否超过24小时 ==========
                if target_user.last_updated is None:
                    await update.message.reply_text("❌ 对方链接已失效（对方从未设置位置）")
                    return
                time_diff = datetime.utcnow() - target_user.last_updated
                if time_diff > timedelta(hours=24):
                    await update.message.reply_text("❌ 对方链接已失效（位置超过24小时未更新）")
                    return
                # ==================================================

                distance = self.calculate_distance(lat, lon, target_user.latitude, target_user.longitude)
                visit = Visit(
                    visitor_id=user.id,
                    target_id=target_user.telegram_id,
                    distance_km=distance
                )
                session.add(visit)

                if distance < 1:
                    dist_str = f"{distance*1000:.0f} 米"
                elif distance < 10:
                    dist_str = f"{distance:.1f} 公里"
                else:
                    dist_str = f"{distance:.0f} 公里"

                await update.message.reply_text(
                    f"📏 你离 @{target_user.username} 的距离是：{dist_str}\n"
#                   f"📍 你的位置：({lat:.4f}, {lon:.4f})\n"
#                   f"🎯 TA的位置：({target_user.latitude:.4f}, {target_user.longitude:.4f})"
                )
            return

        # 情况2：普通更新自己的位置
        with get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=user.id).first()
            if not db_user:
                await update.message.reply_text("请先使用 /start 注册")
                return
            db_user.latitude = lat
            db_user.longitude = lon
            db_user.last_updated = datetime.utcnow()
            await update.message.reply_text(
                f"✅ 位置已更新！\n"
#               f"经纬度：({lat:.4f}, {lon:.4f})\n"
#               f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

    async def set_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[KeyboardButton("📍 分享位置", request_location=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("请点击下方按钮分享你的位置：", reply_markup=reply_markup)

    async def my_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        with get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=user.id).first()
            if not db_user:
                await update.message.reply_text("请先 /start 注册")
                return
            bot_username = context.bot.username
            link = f"https://t.me/{bot_username}?start={db_user.share_code}"
            await update.message.reply_text(
                f"🔗 你的专属分享链接：\n{link}\n\n"
                f"发送这个链接给朋友，他们点击后发送位置就能看到与你之间的距离。\n"
                f"⚠️ 注意：你的位置有效期为24小时，超时后链接将失效，请及时更新位置。"
            )

    async def privacy_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        with get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=user.id).first()
            if not db_user:
                await update.message.reply_text("请先 /start 注册")
                return
            keyboard = [
                [
                    InlineKeyboardButton("🔓 公开" if not db_user.is_public else "✅ 公开", callback_data="privacy_public"),
                    InlineKeyboardButton("🔒 私密" if db_user.is_public else "✅ 私密", callback_data="privacy_private")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            status = "公开" if db_user.is_public else "私密"
            await update.message.reply_text(
                f"当前状态：{status}\n"
                f"公开：任何人都可通过你的链接查询距离\n"
                f"私密：只有你自己可以查询",
                reply_markup=reply_markup
            )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        action = query.data

        with get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=user_id).first()
            if not db_user:
                await query.edit_message_text("请先 /start 注册")
                return
            if action == "privacy_public":
                db_user.is_public = True
                await query.edit_message_text("✅ 你的位置已设置为「公开」")
            elif action == "privacy_private":
                db_user.is_public = False
                await query.edit_message_text("✅ 你的位置已设置为「私密」")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
🤖 位置距离Bot - 使用指南

📌 基本命令：
/start      - 注册并获取专属链接
/setlocation - 设置/更新你的位置
/mylink     - 获取你的专属分享链接
/privacy    - 切换公开/私密模式
/help       - 显示本帮助

📍 如何让别人知道距离？
1. 使用 /mylink 获取你的专属链接
2. 把链接发给朋友
3. 朋友点击链接后发送自己的位置 → 自动算出距离

⚠️ 位置有效期：
- 你的位置有效期为24小时，超时后链接将失效
- 请定期使用 /setlocation 更新位置

🔒 隐私说明：
- 公开：任何人都可通过你的链接查询距离
- 私密：只有你自己可以查询（其他人会收到“私密”提示）
"""
        await update.message.reply_text(help_text)

    # ---------- 管理员命令 ----------
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ 你没有权限使用此命令。")
            return
        with get_session() as session:
            total_users = session.query(User).count()
            users_with_location = session.query(User).filter(User.latitude.isnot(None)).count()
            total_visits = session.query(Visit).count()
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            visits_today = session.query(Visit).filter(Visit.visited_at >= today).count()
        msg = (
            f"📊 Bot 统计信息\n\n"
            f"👤 总用户数：{total_users}\n"
            f"📍 已设置位置：{users_with_location}\n"
            f"🔍 总查询次数：{total_visits}\n"
            f"📅 今日查询：{visits_today}"
        )
        await update.message.reply_text(msg)

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ 你没有权限使用此命令。")
            return
        if not context.args:
            await update.message.reply_text("用法：/broadcast 消息内容")
            return
        text = " ".join(context.args)
        with get_session() as session:
            users = session.query(User).all()
        success = 0
        fail = 0
        for user in users:
            try:
                await context.bot.send_message(chat_id=user.telegram_id, text=f"📢 广播消息：\n{text}")
                success += 1
            except Exception as e:
                logger.error(f"向用户 {user.telegram_id} 发送广播失败: {e}")
                fail += 1
        await update.message.reply_text(f"广播完成：成功 {success}，失败 {fail}")

    async def list_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ 你没有权限使用此命令。")
            return
        with get_session() as session:
            users = session.query(User).order_by(User.created_at.desc()).limit(20).all()
        if not users:
            await update.message.reply_text("暂无用户。")
            return
        msg = "📋 最近20位用户：\n\n"
        for idx, u in enumerate(users, 1):
            username = u.username or f"ID:{u.telegram_id}"
            has_loc = "📍" if u.latitude else "❌"
            msg += f"{idx}. {username} {has_loc} 注册:{u.created_at.strftime('%Y-%m-%d')}\n"
        await update.message.reply_text(msg)

    # ---------- 注册处理器 ----------
    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("setlocation", self.set_location))
        self.app.add_handler(CommandHandler("mylink", self.my_link))
        self.app.add_handler(CommandHandler("privacy", self.privacy_settings))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(MessageHandler(filters.LOCATION, self.handle_location))
        self.app.add_handler(CallbackQueryHandler(self.button_callback))

        self.app.add_handler(CommandHandler("stats", self.stats))
        self.app.add_handler(CommandHandler("broadcast", self.broadcast))
        self.app.add_handler(CommandHandler("users", self.list_users))

    def run(self):
        print("🤖 Bot 正在运行...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

# ==================== 启动 ====================
if __name__ == "__main__":
    if not ADMIN_IDS:
        print("⚠️ 未设置 ADMIN_IDS 环境变量或未配置有效管理员，管理员功能不可用。")
    bot = LocationBot()
    bot.run()