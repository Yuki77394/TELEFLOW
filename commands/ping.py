import time
from telethon import events
from core.permissions import authorized_only
from core.utils import respond, edit_or_reply

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]ping$'))
    @authorized_only()
    async def ping_handler(event):
        start = time.time()
        msg = await respond(event, "Calculating latency...")
        end = time.time()
        latency = round((end - start) * 1000)
        await edit_or_reply(msg, f"**Pong!** ⚡\nLatency: `{latency}ms`", event)
