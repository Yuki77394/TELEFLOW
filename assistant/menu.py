from telethon import events, Button
import config
from core.logger import logger
from core.permissions import is_authorized

async def is_bot_owner(event):
    """Ensures that only authorized users (owner + super users) interact with the bot."""
    return await is_authorized(event)

MAIN_MENU_TEXT = (
    "👋 **Welcome to TeleFlow Control Dashboard!**\n\n"
    "Manage your Telegram Channel Automation Platform dynamically. "
    "All changes apply in real-time.\n\n"
    "💬 **Choose an action:**"
)

MAIN_MENU_BUTTONS = [
    [
        Button.inline("📢 Manage Chats",     b"menu:chats_src"),
        Button.inline("🚪 Join Chat",        b"menu:join"),
    ],
    [
        Button.inline("📊 System Status",    b"menu:status"),
        Button.inline("👑 Super Users",      b"menu:super_users"),
    ],
    [
        Button.inline("🗄️ DB Dashboard",    b"menu:db"),
    ],
]


def register(bot_client):
    @bot_client.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        if not await is_bot_owner(event):
            logger.warning(f"Unauthorized /start from ID: {event.sender_id}")
            return
        await event.respond(MAIN_MENU_TEXT, buttons=MAIN_MENU_BUTTONS)
