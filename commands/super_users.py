from telethon import events
from core.permissions import owner_only
from core.utils import respond, edit_or_reply
from database.database import add_super_user, remove_super_user, get_super_users

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]add_user\s+(\S+)'))
    @owner_only()
    async def add_user_handler(event):
        user_input = event.pattern_match.group(1)
        status_msg = await respond(event, "🔄 *Adding super user...*")
        
        try:
            user_id = int(user_input)
        except ValueError:
            # Try to resolve username using UserBot client (wider entities cache)
            try:
                from core.client import client as userbot_client
                entity = await userbot_client.get_entity(user_input)
                user_id = await userbot_client.get_peer_id(entity)
            except Exception as e:
                await edit_or_reply(status_msg, f"❌ **Failed to resolve user!**\nError: `{e}`", event)
                return
                
        success = add_super_user(user_id)
        if success:
            await edit_or_reply(status_msg, f"✅ **User `{user_id}` added as Super User.**", event)
        else:
            await edit_or_reply(status_msg, "❌ **Failed to add super user.**", event)

    @client.on(events.NewMessage(pattern=r'^[./]remove_user\s+(\S+)'))
    @owner_only()
    async def remove_user_handler(event):
        user_input = event.pattern_match.group(1)
        status_msg = await respond(event, "🔄 *Removing super user...*")
        
        try:
            user_id = int(user_input)
        except ValueError:
            # Try to resolve username
            try:
                from core.client import client as userbot_client
                entity = await userbot_client.get_entity(user_input)
                user_id = await userbot_client.get_peer_id(entity)
            except Exception as e:
                await edit_or_reply(status_msg, f"❌ **Failed to resolve user!**\nError: `{e}`", event)
                return
                
        success = remove_super_user(user_id)
        if success:
            await edit_or_reply(status_msg, f"🗑️ **User `{user_id}` removed from Super Users.**", event)
        else:
            await edit_or_reply(status_msg, "❌ **Failed to remove user (not found or database error).**", event)

    @client.on(events.NewMessage(pattern=r'^[./]list_users$'))
    @owner_only()
    async def list_users_handler(event):
        users = get_super_users()
        if not users:
            await respond(event, "ℹ️ **No super users registered yet.**")
            return
            
        response = "👥 **Super Users:**\n\n"
        for idx, user_id in enumerate(users, 1):
            response += f"{idx}. `{user_id}`\n"
            
        await respond(event, response)
