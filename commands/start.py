from telethon import events
from core.permissions import authorized_only
from core.utils import respond

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]start$'))
    @authorized_only()
    async def start_handler(event):
        from core.client import client as userbot_client
        me = await event.client.get_me()
        ub_me = await userbot_client.get_me()
        welcome_text = (
            f"👋 **Hello! I am TeleFlow Assistant Bot.**\n\n"
            f"🤖 **Assistant Bot:** @{me.username}\n"
            f"👤 **UserBot Account:** {ub_me.first_name} [ID: `{ub_me.id}`]\n\n"
            "Use `/help` or `.help` to see all available commands!"
        )
        await respond(event, welcome_text)
