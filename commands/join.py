from telethon import events
from core.permissions import authorized_only
from core.utils import respond, edit_or_reply, perform_join

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]join\s+(\S+)'))
    @authorized_only()
    async def join_handler(event):
        invite_input = event.pattern_match.group(1)
        status_msg = await respond(event, "🔄 *Attempting to join chat...*")
        try:
            from core.client import client as userbot_client
            result = await perform_join(userbot_client, invite_input)
            await edit_or_reply(status_msg, result, event)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to join!**\nError: `{e}`", event)
