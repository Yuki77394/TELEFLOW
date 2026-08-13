from telethon import events
from core.permissions import authorized_only
from core.utils import respond

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]me$'))
    @authorized_only()
    async def me_handler(event):
        from core.client import client as userbot_client
        me = await userbot_client.get_me()
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        username = f"@{me.username}" if me.username else "None"
        phone = me.phone or "Private"
        
        response = (
            "👤 **UserBot Account Info**\n\n"
            f"• **Name:** {first_name} {last_name}\n"
            f"• **Username:** {username}\n"
            f"• **User ID:** `{me.id}`\n"
            f"• **Phone:** `{phone}`"
        )
        await respond(event, response)
