from telethon import events
from core.permissions import authorized_only
from core.utils import respond

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]id$'))
    @authorized_only()
    async def id_handler(event):
        chat_id = event.chat_id
        response = f"**Current Chat ID:** `{chat_id}`"
        
        # Check if replying to a message to fetch IDs
        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                if reply_msg.sender_id:
                    response += f"\n**Replied User ID:** `{reply_msg.sender_id}`"
                if reply_msg.forward:
                    if reply_msg.forward.chat_id:
                        response += f"\n**Replied Forwarded Chat ID:** `{reply_msg.forward.chat_id}`"
                    elif reply_msg.forward.sender_id:
                        response += f"\n**Replied Forwarded User ID:** `{reply_msg.forward.sender_id}`"
                        
        await respond(event, response)
