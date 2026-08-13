from telethon import events
from core.permissions import authorized_only, is_owner
from core.utils import respond

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]help$'))
    @authorized_only()
    async def help_handler(event):
        # Determine if the sender is the owner to show owner-only commands
        is_user_owner = await is_owner(event)
        
        help_text = (
            "🚀 **TeleFlow UserBot Command Reference**\n\n"
            "💡 *All commands can be used with a dot (.) or a slash (/).*\n\n"
            "💬 **Basic & System Commands**\n"
            "• `/ping` - Check bot response latency\n"
            "• `/me` - Get info on the UserBot account\n"
            "• `/id` - Get current chat/group ID\n"
            "  ↳ *Tip:* Reply to a user/forwarded message to get their IDs too.\n"
            "• `/chats` - List your channels/groups with IDs\n"
            "• `/status` - Uptime, rule statistics, and database status\n\n"
            
            "⚙️ **Channel Management**\n"
            "• `/join <username/link>` - Join public/private chats\n"
            "  ↳ *Example:* `.join @my_source_channel` or `.join https://t.me/+AbCdEfGh`\n"
            "• `/add <source> <target>` - Link forwarding source to target\n"
            "  ↳ *Example:* `.add -100123456789 -100987654321` or `.add @src_chan @tgt_chan`\n"
            "• `/remove <source> <target>` - Delete forwarding link\n"
            "  ↳ *Example:* `.remove @src_chan @tgt_chan`\n"
            "• `/list` - List all active forwarding rules\n\n"
            
            "✏️ **Replacements, Regex \u0026 Header/Footer**\n"
            "• `/replace <src> \"<find>\" \"<replace>\"` - Add plain text replacement\n"
            "  ↳ *Example:* `/replace @src_chan \"Win\" \"🔥 WIN 🔥\"`\n"
            "• `/replace_del <src> \"<find>\"` - Remove a plain replacement rule\n"
            "• `/replace_list <src>` - View all plain replacements for a source\n\n"
            "🔧 **Regex Rules (via Bot Commands)**\n"
            "• `/regex_add <src> <name> <pattern> -> <replacement>`\n"
            "  ↳ *Example:* `/regex_add @src re1 (@)\\S+ -> @myUser`\n"
            "  ↳ *Example:* `/regex_add @src links (www|https?)\\S+ -> @myLink`\n"
            "• `/regex_del <src> <name>` - Delete a specific regex rule\n"
            "• `/regex_list <src>` - List all regex rules + status\n"
            "• `/regex_on <src>` - Enable regex for a source chat\n"
            "• `/regex_off <src>` - Disable regex for a source chat\n\n"
            "💡 *Tip: All the above can also be managed via inline buttons in the Assistant Bot (/start)*\n\n"
            "📐 **Header / Footer**\n"
            "• `/header <src> <tgt> <text>` - Prepend text on forward\n"
            "• `/footer <src> <tgt> <text>` - Append text on forward\n"
            "• `/clearheader` / `/clearfooter <src> <tgt>` - Clear them\n\n"
        )
        
        if is_user_owner:
            help_text += (
                "👑 **Owner-Only Commands (Super User Management)**\n"
                "• `/add_user <user_id/username>` - Register a new Super User\n"
                "  ↳ *Example:* `.add_user 1217850333` or `.add_user @AsHackerassr`\n"
                "• `/remove_user <user_id/username>` - Remove user access\n"
                "• `/list_users` - List all registered Super Users\n"
            )
            
        await respond(event, help_text)
