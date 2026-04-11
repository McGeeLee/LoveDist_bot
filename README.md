# Location Bot — Telegram Distance Checker Bot

A Telegram bot built with Python-Telegram-Bot and SQLAlchemy async database. Users can share their real-time location, generate a shareable link, and allow others to calculate the straight-line distance between them. Supports privacy mode, admin statistics, and broadcast functionality.

## Features

- 📍 Share real-time location and auto-update
- 🔗 Generate a unique shareable link (send directly to friends)
- 📏 Calculate straight-line distance between users (Haversine formula)
- 🔒 Privacy toggle: Public / Private mode
- 👑 Admin statistics: user count, location count, visit count
- 📢 Admin broadcast messages
- ⏱️ Rate limiting (prevents abuse)
- 🗄️ Async SQLite database

## Tech Stack

- Python 3.10+
- python-telegram-bot v20.x
- SQLAlchemy 2.x (async)
- aiosqlite
- python-dotenv

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/your-repo/location-bot.git
cd location-bot
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
# or
venv\Scripts\activate      # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in the real values:

```bash
cp .env.example .env
```

Edit `.env`:

```
BOT_TOKEN=your_bot_token_from_BotFather
ADMIN_IDS=123456789,987654321   # optional, comma-separated
```

### 5. Initialize the database

The `locations.db` SQLite file will be created automatically on first run.

### 6. Run the bot

```bash
python bot.py
```

The bot will start polling for updates.

## Usage Guide

### User Commands

| Command | Description |
|---------|-------------|
| `/start` | Register, get your shareable link, and prompt to share location |
| `/privacy` | Check current privacy status |
| `/privacy on` | Make your location public (others can query distance via your link) |
| `/privacy off` | Make your location private (only you can see your own location) |

### Admin Commands

Requires `ADMIN_IDS` set in `.env`.

| Command | Description |
|---------|-------------|
| `/stats` | View statistics (total users, users with location, total visits, today's visits) |
| `/broadcast <message>` | Broadcast a message to all registered users |

### Querying Distance to Another User

1. The other person gets their shareable link via `/start` (format `https://t.me/bot_username?start=xxxx`)
2. You forward the link to a friend or click it yourself
3. After clicking Start, the bot asks if you want to check the distance
4. Click the "Share Location" button and send your current location
5. The bot returns the straight-line distance between you and the other person

> The other person's shareable link is valid for 24 hours after their last location update.

## Project Structure

```
location-bot/
├── bot.py                # Main program
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variable template
├── .env                  # Actual configuration (do not commit to git)
├── locations.db          # SQLite database (auto-generated)
└── README.md
```

## Deployment Recommendations

- **Production**: Use webhook mode (requires public HTTPS URL) and replace SQLite with PostgreSQL (async version).
- **Process management**: Use systemd, supervisor, or Docker.
- **Logging**: Configure `logging` to output to a file.

## FAQ

**Q: Why can't I query someone's distance?**  
A: Possible reasons: the other person hasn't shared their location, has set privacy to private, or their location hasn't been updated in over 24 hours.

**Q: What are the rate limits?**  
A: Own location update: 30 seconds/request; distance query: 5 seconds/request; start command: 3 seconds/request.

**Q: How to add more admins?**  
A: Edit `ADMIN_IDS` in `.env` with comma-separated Telegram numeric IDs, then restart the bot.

## License

MIT License