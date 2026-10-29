"""Keep the offline suite independent of local production configuration."""
import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ.pop("BOT_TOKEN", None)
os.environ.pop("ADMIN_IDS", None)
