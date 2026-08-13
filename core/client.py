import sys
from telethon import TelegramClient
from telethon.sessions import StringSession, MemorySession
import config
from core.logger import logger

# Validate API configuration
if not config.API_ID or not config.API_HASH:
    logger.critical("API_ID and API_HASH must be configured in .env file!")
    sys.exit(1)

# Initialize TelegramClient
if config.SESSION_STRING:
    logger.info("Initializing TelegramClient with StringSession...")
    client = TelegramClient(
        StringSession(config.SESSION_STRING),
        config.API_ID,
        config.API_HASH
    )
else:
    logger.info("Initializing TelegramClient with file-based session (data/teleflow)...")
    # Creates/uses data/teleflow.session file
    client = TelegramClient(
        "data/teleflow",
        config.API_ID,
        config.API_HASH
    )

# Initialize bot client if BOT_TOKEN is present
# Use MemorySession — bot tokens re-authenticate every restart,
# so no persistent session file is needed. This also prevents
# "database is locked" errors from SQLite session file contention.
bot_client = None
if config.BOT_TOKEN:
    logger.info("Initializing Assistant Bot client...")
    bot_client = TelegramClient(
        MemorySession(),
        config.API_ID,
        config.API_HASH
    )
