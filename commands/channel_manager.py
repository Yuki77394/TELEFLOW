import time
import datetime
from telethon import events
from core.permissions import authorized_only
from core.utils import respond, edit_or_reply
from database.database import add_forward_rule, remove_forward_rule, get_forward_rules

async def resolve_chat(client, chat_input):
    """Resolves a username or ID into a signed integer peer ID.

    For numeric input, we no longer blindly trust int(chat_input). A bare
    channel/supergroup ID typed without the '-100' prefix (e.g. someone
    pastes '2696349600' instead of '-1002696349600') is otherwise silently
    treated as a *user* ID, which breaks forwarding with:
    'Could not find the input entity for PeerUser(...)'.

    Strategy for numeric input:
      1. Try the number exactly as given.
      2. If that fails to resolve, and it looks like a bare channel id
         (positive, no existing -100 prefix), retry with -100 prepended.
      3. Whatever resolves successfully wins; otherwise raise the original error.
    """
    chat_input = chat_input.strip()

    try:
        as_int = int(chat_input)
    except ValueError:
        as_int = None

    if as_int is not None:
        # 1. Try exactly as given
        try:
            entity = await client.get_entity(as_int)
            return await client.get_peer_id(entity)
        except Exception as first_err:
            # 2. If it's a bare positive number (no -100 prefix), retry as a channel id
            if as_int > 0 and not chat_input.startswith("-100"):
                candidate = int(f"-100{chat_input}")
                try:
                    entity = await client.get_entity(candidate)
                    return await client.get_peer_id(entity)
                except Exception:
                    pass
            raise first_err

    # If not a number, resolve via Telegram Client
    entity = await client.get_entity(chat_input)
    return await client.get_peer_id(entity)

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]add\s+(\S+)\s+(\S+)'))
    @authorized_only()
    async def add_handler(event):
        source_raw = event.pattern_match.group(1)
        target_raw = event.pattern_match.group(2)
        
        status_msg = await respond(event, "🔄 *Resolving chat entities...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
            target_id = await resolve_chat(userbot_client, target_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve chats!**\nError: `{e}`", event)
            return
            
        success = add_forward_rule(source_id, target_id)
        if success:
            from database.database import init_media_filters
            init_media_filters(source_id, target_id)
            await edit_or_reply(status_msg, 
                f"✅ **Forwarding Rule Added!**\n"
                f"• **Source:** `{source_id}` (from `{source_raw}`)\n"
                f"• **Target:** `{target_id}` (from `{target_raw}`)",
                event
            )
        else:
            await edit_or_reply(status_msg, "❌ **Database error!** Failed to save rule.", event)

    @client.on(events.NewMessage(pattern=r'^[./]remove\s+(\S+)\s+(\S+)'))
    @authorized_only()
    async def remove_handler(event):
        source_raw = event.pattern_match.group(1)
        target_raw = event.pattern_match.group(2)
        
        status_msg = await respond(event, "🔄 *Resolving chat entities...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
            target_id = await resolve_chat(userbot_client, target_raw)
        except Exception as e:
            # Failsafe: Try using raw integer inputs directly
            try:
                source_id = int(source_raw)
                target_id = int(target_raw)
            except ValueError:
                await edit_or_reply(status_msg, f"❌ **Failed to resolve chats!**\nError: `{e}`", event)
                return
                
        success = remove_forward_rule(source_id, target_id)
        if success:
            await edit_or_reply(status_msg, 
                f"🗑️ **Forwarding Rule Removed!**\n"
                f"• **Source:** `{source_id}`\n"
                f"• **Target:** `{target_id}`",
                event
            )
        else:
            await edit_or_reply(status_msg, "❌ **Rule not found** or database error.", event)

    @client.on(events.NewMessage(pattern=r'^[./]list$'))
    @authorized_only()
    async def list_handler(event):
        rules = get_forward_rules()
        if not rules:
            await respond(event, "ℹ️ **No forwarding rules found.** Use `.add <source> <target>` to add one.")
            return
            
        response = "📋 **Active Forwarding Rules:**\n\n"
        for idx, (source, target, active) in enumerate(rules, 1):
            status = "🟢" if active else "🔴"
            response += f"{idx}. {status} `{source}` ➡️ `{target}`\n"
            
        await respond(event, response)

    @client.on(events.NewMessage(pattern=r'^[./]status$'))
    @authorized_only()
    async def status_handler(event):
        from core.client import client as userbot_client
        import config
        uptime_seconds = int(time.time() - config.START_TIME)
        uptime = str(datetime.timedelta(seconds=uptime_seconds))
        
        rules = get_forward_rules()
        active_count = sum(1 for r in rules if r[2] == 1)
        total_count = len(rules)
        
        response = (
            "📊 **TeleFlow Status**\n\n"
            f"• **Uptime:** `{uptime}`\n"
            f"• **Active Rules:** `{active_count}`\n"
            f"• **Total Rules:** `{total_count}`\n"
            f"• **Database:** `Connected` ✅"
        )
        await respond(event, response)
