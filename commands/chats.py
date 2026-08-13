from telethon import events
from core.permissions import authorized_only
from core.utils import respond, edit_or_reply

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]chats$'))
    @authorized_only()
    async def chats_handler(event):
        status_msg = await respond(event, "🔄 *Fetching chats and channels...*")
        try:
            from core.client import client as userbot_client
            dialogs = await userbot_client.get_dialogs()
            
            channels_list = []
            groups_list = []
            
            for dialog in dialogs:
                if dialog.is_channel:
                    channels_list.append(f"• `{dialog.id}` - **{dialog.name}**")
                elif dialog.is_group:
                    groups_list.append(f"• `{dialog.id}` - **{dialog.name}**")
            
            # Form response text
            response = "📢 **Channels (Channels/Supergroups):**\n"
            if channels_list:
                response += "\n".join(channels_list[:35])
            else:
                response += "_No channels found_"
                
            response += "\n\n👥 **Groups:**\n"
            if groups_list:
                response += "\n".join(groups_list[:35])
            else:
                response += "_No groups found_"
                
            if len(channels_list) > 35 or len(groups_list) > 35:
                response += "\n\n*(Showing top 35 channels and groups. If you have more, please manage them by name/ID)*"
                
            await edit_or_reply(status_msg, response, event)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to fetch chats!**\nError: `{e}`", event)
