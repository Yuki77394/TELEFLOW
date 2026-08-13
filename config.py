import os
import time
from dotenv import load_dotenv

# Record start time for uptime tracking
START_TIME = time.time()

# Load variables from .env file
load_dotenv()

API_ID = os.getenv("API_ID")
if API_ID:
    try:
        API_ID = int(API_ID)
    except ValueError:
        pass

API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

# OWNER_ID can be integer user ID, or string username, or None
OWNER_ID = os.getenv("OWNER_ID")
if OWNER_ID:
    try:
        OWNER_ID = int(OWNER_ID)
    except ValueError:
        pass

# Bot Token for Assistant
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Auto-react to the UserBot's own copied/forwarded messages in target
# channels (3 reactions if the account has Telegram Premium, else 1).
# Set AUTO_REACT_ENABLED=false in .env to turn this off.
AUTO_REACT_ENABLED = os.getenv("AUTO_REACT_ENABLED", "true").strip().lower() not in ("false", "0", "no")
