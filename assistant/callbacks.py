import time
import datetime
import base64
from telethon import events, Button
from assistant.menu import is_bot_owner, MAIN_MENU_TEXT, MAIN_MENU_BUTTONS
from database.database import (
    get_forward_rules, remove_forward_rule,
    get_replacements, remove_replacement, add_replacement,
    get_regex_rules, remove_regex_rule,
    is_regex_enabled, set_regex_enabled,
    get_header_footer, set_header_footer,
    get_target_specific_replacements
)
from core.logger import logger
import config


# ─── Utility ─────────────────────────────────────────────────────────────────

async def get_chat_name(client, chat_id):
    """Resolve chat ID → readable title/name."""
    try:
        entity = await client.get_entity(chat_id)
        if hasattr(entity, 'title') and entity.title:
            return entity.title
        elif hasattr(entity, 'first_name') and entity.first_name:
            last = f" {entity.last_name}" if getattr(entity, 'last_name', None) else ""
            return f"{entity.first_name}{last}"
        return f"Chat {chat_id}"
    except Exception:
        return f"Chat {chat_id}"


def _encode(text: str) -> str:
    """Base64-encode a string for use in callback data."""
    return base64.urlsafe_b64encode(text.encode()).decode()


def _decode(b64: str) -> str:
    """Decode a base64-encoded callback data value."""
    return base64.urlsafe_b64decode(b64.encode()).decode()


# ─── Screen builders ─────────────────────────────────────────────────────────

async def show_menu(event):
    await event.edit(MAIN_MENU_TEXT, buttons=MAIN_MENU_BUTTONS)


async def show_active_rules(event):
    rules = get_forward_rules()
    if not rules:
        await event.edit(
            "📋 **Active Forwarding Rules:**\n\n_No rules configured yet._",
            buttons=[[Button.inline("🔙 Back", b"menu:back")]]
        )
        return

    await event.edit("🔄 *Fetching chat details…*")
    from core.client import client

    text = "📋 **Active Forwarding Rules:**\n\n"
    buttons = []
    for idx, (source, target, active) in enumerate(rules, 1):
        src_name = await get_chat_name(client, source)
        tgt_name = await get_chat_name(client, target)
        status = "🟢" if active else "🔴"
        text += (
            f"**Rule #{idx}** {status}\n"
            f"🔹 **Source:** {src_name} (`{source}`)\n"
            f"🔸 **Target:** {tgt_name} (`{target}`)\n\n"
        )
        buttons.append([
            Button.inline(f"❌ Delete Rule #{idx}", f"delete:{source}:{target}".encode())
        ])

    buttons.append([Button.inline("🔙 Back", b"menu:back")])
    await event.edit(text, buttons=buttons)


async def show_status(event):
    uptime = str(datetime.timedelta(seconds=int(time.time() - config.START_TIME)))
    rules = get_forward_rules()
    active_count = sum(1 for r in rules if r[2] == 1)

    from core.client import client
    ub_status = "🟢 Online" if client.is_connected() else "🔴 Offline"

    from database.database import get_db_status
    db_name, db_latency = get_db_status()

    text = (
        "📊 **TeleFlow System Status:**\n\n"
        f"• **UserBot Engine:** {ub_status}\n"
        f"• **Uptime:** `{uptime}`\n"
        f"• **Active Rules:** `{active_count}` / `{len(rules)}`\n"
        f"• **Database Engine:** `{db_name}`\n"
        f"• **Database Ping:** `{db_latency}`\n\n"
        "*(Real-time)*"
    )
    buttons = [
        [Button.inline("🔄 Refresh", b"menu:status")],
        [Button.inline("🔙 Back",    b"menu:back")],
    ]
    await event.edit(text, buttons=buttons)


# ─── Source Chat selector ─────────────────────────────────────────────────────

async def show_source_chats(event):
    """List all groups/channels where the userbot is joined."""
    await event.edit("🔄 *Loading chats from UserBot…*")
    from core.client import client
    try:
        dialogs = await client.get_dialogs()
        buttons = []
        count = 0
        for d in dialogs:
            # Check for channel (supergroup or channel) or group
            if d.is_channel or d.is_group:
                icon = "📣" if d.is_channel else "👥"
                buttons.append([Button.inline(f"{icon} {d.name[:25]}", f"chat_detail:{d.id}".encode())])
                count += 1
        
        if not buttons:
            await event.edit(
                "📢 **No groups or channels found.**\n\nMake sure the UserBot has joined some chats.",
                buttons=[[Button.inline("🔙 Back", b"menu:back")]]
            )
            return
            
        text = f"📢 **Select a Chat to Manage ({count} chats loaded):**"
        buttons.append([Button.inline("🔙 Back", b"menu:back")])
        await event.edit(text, buttons=buttons)
    except Exception as e:
        await event.edit(f"❌ **Failed to load chats:**\n`{e}`", buttons=[[Button.inline("🔙 Back", b"menu:back")]])


# ─── Chat detail page ─────────────────────────────────────────────────────────

async def show_chat_detail(event, source_id: int):
    """Per-chat overview: plain rules count, regex status, targets."""
    from core.client import client
    src_name = await get_chat_name(client, source_id)

    plain_rules  = get_replacements(source_id, None)
    regex_rules  = get_regex_rules(source_id)
    regex_on     = is_regex_enabled(source_id)
    regex_status = f"🟢 ON ({len(regex_rules)} rules)" if regex_on else f"🔴 OFF ({len(regex_rules)} rules)"

    all_rules = get_forward_rules()
    targets = [t for s, t, _ in all_rules if s == source_id]
    target_names = []
    for t in targets:
        target_names.append(await get_chat_name(client, t))

    targets_str = ", ".join(target_names) if target_names else "_none_"

    text = (
        f"📣 **{src_name}**\n"
        f"   ID: `{source_id}`\n\n"
        f"📌 **Plain Replacements:** `{len(plain_rules)}`\n"
        f"🔧 **Regex Rules:** {regex_status}\n"
        f"🎯 **Targets:** {targets_str}\n"
    )
    buttons = [
        [Button.inline("✏️ Plain Replacements", f"chat_plain:{source_id}".encode())],
        [Button.inline("🔧 Regex Rules",         f"chat_regex:{source_id}".encode())],
        [Button.inline("📋 Forward Targets",     f"chat_targets:{source_id}".encode())],
        [Button.inline("🔙 Back",                b"menu:chats_src")],
    ]
    await event.edit(text, buttons=buttons)


# ─── Plain Replacements page ──────────────────────────────────────────────────

async def show_plain_rules(event, source_id: int):
    from core.client import client
    src_name = await get_chat_name(client, source_id)
    rules = get_replacements(source_id, None)

    text = f"✏️ **Plain Replacements — {src_name}**\n\n"
    buttons = []

    if not rules:
        text += "_No plain replacement rules yet._\n"
    else:
        for idx, rule in enumerate(rules, 1):
            find, replace = rule[0], rule[1]
            text += f"`{idx}.` `{find}` ➡️ `{replace}`\n"
            find_b64 = _encode(find)
            buttons.append([
                Button.inline(f"🗑️ Remove #{idx}: \"{find[:20]}\"",
                              f"plain_del:{source_id}:{find_b64}".encode())
            ])

    buttons.append([Button.inline("➕ Add Plain Rule",  f"plain_add_start:{source_id}".encode())])
    buttons.append([Button.inline("🔙 Back",            f"chat_detail:{source_id}".encode())])
    await event.edit(text, buttons=buttons)


# ─── Regex Rules page ────────────────────────────────────────────────────────

async def show_regex_rules(event, source_id: int):
    from core.client import client
    src_name  = await get_chat_name(client, source_id)
    rules     = get_regex_rules(source_id)
    regex_on  = is_regex_enabled(source_id)
    toggle_lbl = "🔴 Disable Regex" if regex_on else "🟢 Enable Regex"
    status_str = "🟢 **ON**" if regex_on else "🔴 **OFF**"

    text = (
        f"🔧 **Regex Rules — {src_name}**\n"
        f"Status: {status_str}\n\n"
    )
    buttons = []

    if not rules:
        text += "_No regex rules yet._\n"
    else:
        for idx, (name, pat, repl) in enumerate(rules, 1):
            text += f"`{idx}.` **{name}**\n   `{pat}` ➡️ `{repl}`\n\n"
            buttons.append([
                Button.inline(f"🗑️ Delete {name}",
                              f"regex_del:{source_id}:{name}".encode())
            ])

    buttons.append([Button.inline(toggle_lbl,          f"regex_toggle:{source_id}".encode())])
    buttons.append([Button.inline("➕ Add Regex Rule",  f"regex_add_start:{source_id}".encode())])
    buttons.append([Button.inline("🔙 Back",            f"chat_detail:{source_id}".encode())])
    await event.edit(text, buttons=buttons)


# ─── Targets page ────────────────────────────────────────────────────────────

async def show_targets(event, source_id: int):
    from core.client import client
    src_name  = await get_chat_name(client, source_id)
    all_rules = get_forward_rules()
    targets   = [(t, active) for s, t, active in all_rules if s == source_id]

    text = f"📋 **Forward Targets — {src_name}**\n\nSelect a target to manage its header/footer or remove it:\n\n"
    buttons = []

    if not targets:
        text += "_No targets configured._\n"
    else:
        for idx, (t_id, active) in enumerate(targets, 1):
            t_name = await get_chat_name(client, t_id)
            status = "🟢" if active else "🔴"
            text += f"`{idx}.` {status} **{t_name}** (`{t_id}`)\n"
            buttons.append([
                Button.inline(f"🎯 {t_name[:20]} Detail",
                              f"target_detail:{source_id}:{t_id}".encode())
            ])

    buttons.append([Button.inline("➕ Add Target", f"target_add_start:{source_id}".encode())])
    buttons.append([Button.inline("🔙 Back", f"chat_detail:{source_id}".encode())])
    await event.edit(text, buttons=buttons)


# ─── Target Link Detail page ──────────────────────────────────────────────────

async def show_target_detail(event, source_id: int, target_id: int):
    from core.client import client
    src_name = await get_chat_name(client, source_id)
    tgt_name = await get_chat_name(client, target_id)

    header, footer, _, _ = get_header_footer(source_id, target_id)
    plain_rules     = get_replacements(source_id, target_id)
    regex_rules     = get_regex_rules(source_id)
    regex_on        = is_regex_enabled(source_id)
    regex_status    = "🟢 ON" if regex_on else "🔴 OFF"

    from database.database import get_media_filters
    filters = get_media_filters(source_id, target_id)
    filter_icons = {k: "🟢" if v else "🔴" for k, v in filters.items()}

    text = (
        f"🔗 **Forwarding Link Details**\n\n"
        f"📣 **Source:** {src_name} (`{source_id}`)\n"
        f"🎯 **Target:** {tgt_name} (`{target_id}`)\n\n"
        f"📝 **Header:** `{header or 'None'}`\n"
        f"📝 **Footer:** `{footer or 'None'}`\n\n"
        f"✏️ **Per-Target Replacements:** `{len(plain_rules)}`\n"
        f"🔧 **Regex Rules:** {regex_status} (`{len(regex_rules)}` rules)\n\n"
        f"⚙️ **Media Filters:**\n"
        f"  Text {filter_icons['text']}  Sticker {filter_icons['sticker']}  "
        f"Photo {filter_icons['photo']}  Audio {filter_icons['audio']}\n"
        f"  Video {filter_icons['video']}  GIF {filter_icons['gif']}  "
        f"Inline Btns {filter_icons['inline_btn']}\n"
    )

    buttons = [
        [
            Button.inline("📝 Set Header", f"header_set_start:{source_id}:{target_id}".encode()),
            Button.inline("🗑️ Clear Header", f"header_clear:{source_id}:{target_id}".encode())
        ],
        [
            Button.inline("📝 Set Footer", f"footer_set_start:{source_id}:{target_id}".encode()),
            Button.inline("🗑️ Clear Footer", f"footer_clear:{source_id}:{target_id}".encode())
        ],
        [Button.inline("✏️ Per-Target Replacements", f"tgt_replace:{source_id}:{target_id}".encode())],
        [Button.inline("🔧 Regex Rules",             f"chat_regex:{source_id}".encode())],
        [Button.inline("⚙️ Media Filters",           f"mf_show:{source_id}:{target_id}".encode())],
        [Button.inline("❌ Delete Target Link",      f"target_link_del:{source_id}:{target_id}".encode())],
        [Button.inline("🔙 Back",                    f"chat_targets:{source_id}".encode())],
    ]
    await event.edit(text, buttons=buttons)


# ─── Per-Target Replacements page ────────────────────────────────────────────

async def show_target_replacements(event, source_id: int, target_id: int):
    """Show replacements that apply only to this specific source→target link."""
    from core.client import client
    src_name = await get_chat_name(client, source_id)
    tgt_name = await get_chat_name(client, target_id)
    target_rules = get_target_specific_replacements(source_id, target_id)

    text = f"✏️ **Per-Target Replacements**\n📣 {src_name} → 🎯 {tgt_name}\n\n"
    buttons = []

    if not target_rules:
        text += "_No per-target replacement rules yet._\n"
    else:
        for idx, rule in enumerate(target_rules, 1):
            find, replace = rule[0], rule[1]
            text += f"`{idx}.` `{find}` ➡️ `{replace}`\n"
            find_b64 = _encode(find)
            buttons.append([
                Button.inline(
                    f"🗑️ Remove #{idx}: \"{find[:20]}\"",
                    f"tgt_replace_del:{source_id}:{target_id}:{find_b64}".encode()
                )
            ])

    buttons.append([Button.inline("➕ Add Rule",  f"tgt_replace_add:{source_id}:{target_id}".encode())])
    buttons.append([Button.inline("🔙 Back",      f"target_detail:{source_id}:{target_id}".encode())])
    await event.edit(text, buttons=buttons)




# ─── Super Users page ─────────────────────────────────────────────────────────

async def show_super_users(event):
    from database.database import get_super_users
    users = get_super_users()
    
    text = "👑 **Super Users Management**\n\nAuthorized users:\n"
    buttons = []
    
    if not users:
        text += "_No super users authorized besides the owner._\n"
    else:
        for u in users:
            text += f"• `{u}`\n"
            buttons.append([
                Button.inline(f"🗑️ Deauthorize {u}", f"su_remove:{u}".encode())
            ])
            
    buttons.append([Button.inline("➕ Add Super User", b"su_add_start")])
    buttons.append([Button.inline("🔙 Back", b"menu:back")])
    await event.edit(text, buttons=buttons)


# ─── show_chats (existing – Chat IDs list) ───────────────────────────────────

async def show_chats(event):
    await event.edit("🔄 *Fetching chat list from UserBot…*")
    from core.client import client
    try:
        dialogs = await client.get_dialogs()
        channels_list, groups_list = [], []
        for d in dialogs:
            if d.is_channel:
                channels_list.append(f"📣 **{d.name}**\n   ↳ ID: `{d.id}`")
            elif d.is_group:
                groups_list.append(f"👥 **{d.name}**\n   ↳ ID: `{d.id}`")

        text = "📢 **UserBot Connected Chats:**\n\n💎 **Channels/Supergroups:**\n"
        text += "\n".join(channels_list[:15]) if channels_list else "_None found_"
        text += "\n\n✨ **Groups:**\n"
        text += "\n".join(groups_list[:15]) if groups_list else "_None found_"
        if len(channels_list) > 15 or len(groups_list) > 15:
            text += "\n\n*(Showing top 15 each — more exist)*"
    except Exception as err:
        text = f"❌ **Failed to fetch dialogs:**\n`{err}`"

    await event.edit(text, buttons=[[Button.inline("🔙 Back", b"menu:back")]])


def register(bot_client):
    @bot_client.on(events.CallbackQuery)
    async def callback_handler(event):
        from telethon.errors import MessageNotModifiedError
        try:
            await process_callback(event)
        except MessageNotModifiedError:
            try:
                await event.answer()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Unhandled exception on callback_handler: {e}", exc_info=True)

    async def process_callback(event):
        if not await is_bot_owner(event):
            await event.answer("⚠️ Unauthorized!", alert=True)
            return

        data = event.data  # bytes

        # ── Main menu ──────────────────────────────────────────────────────
        if data == b"menu:back":
            await show_menu(event)

        elif data == b"menu:list":
            await show_active_rules(event)

        elif data == b"menu:status":
            await show_status(event)

        elif data == b"menu:join":
            from assistant.regex_conversation import join_chat_start
            await join_chat_start(event)

        elif data == b"menu:chats_src":
            await show_source_chats(event)

        elif data == b"menu:super_users":
            await show_super_users(event)

        elif data == b"su_add_start":
            from assistant.regex_conversation import superuser_add_start
            await superuser_add_start(event)

        elif data.startswith(b"su_remove:"):
            from database.database import remove_super_user
            user_id = int(data.decode().split(":")[1])
            success = remove_super_user(user_id)
            await event.answer("🗑️ Super User removed!" if success else "❌ Failed.", alert=not success)
            await show_super_users(event)

        # ── Forward rule delete (existing pattern) ─────────────────────────
        elif data.startswith(b"delete:"):
            parts = data.decode().split(":")
            src_id, tgt_id = int(parts[1]), int(parts[2])
            success = remove_forward_rule(src_id, tgt_id)
            if success:
                await event.answer("🗑️ Rule deleted!", alert=False)
            else:
                await event.answer("❌ Failed to delete.", alert=True)
            await show_active_rules(event)

        # ── Chat detail ────────────────────────────────────────────────────
        elif data.startswith(b"chat_detail:"):
            source_id = int(data.decode().split(":", 1)[1])
            await show_chat_detail(event, source_id)

        # ── Plain replacements ─────────────────────────────────────────────
        elif data.startswith(b"chat_plain:"):
            source_id = int(data.decode().split(":", 1)[1])
            await show_plain_rules(event, source_id)

        elif data.startswith(b"plain_del:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            find_text = _decode(parts[2])
            success = remove_replacement(source_id, None, find_text)
            await event.answer("🗑️ Rule removed!" if success else "❌ Not found.", alert=not success)
            await show_plain_rules(event, source_id)

        elif data.startswith(b"plain_add_start:"):
            source_id = int(data.decode().split(":", 1)[1])
            from assistant.regex_conversation import plain_add_start
            await plain_add_start(event, source_id)

        # ── Regex rules ────────────────────────────────────────────────────
        elif data.startswith(b"chat_regex:"):
            source_id = int(data.decode().split(":", 1)[1])
            await show_regex_rules(event, source_id)

        elif data.startswith(b"regex_toggle:"):
            source_id = int(data.decode().split(":", 1)[1])
            current = is_regex_enabled(source_id)
            set_regex_enabled(source_id, not current)
            state = "🟢 ENABLED" if not current else "🔴 DISABLED"
            await event.answer(f"Regex {state}!", alert=False)
            await show_regex_rules(event, source_id)

        elif data.startswith(b"regex_del:"):
            parts = data.decode().split(":")
            source_id  = int(parts[1])
            rule_name  = parts[2]
            success = remove_regex_rule(source_id, rule_name)
            await event.answer(
                f"🗑️ '{rule_name}' deleted!" if success else "❌ Not found.",
                alert=not success
            )
            await show_regex_rules(event, source_id)

        elif data.startswith(b"regex_add_start:"):
            source_id = int(data.decode().split(":", 1)[1])
            from assistant.regex_conversation import regex_add_start
            await regex_add_start(event, source_id)

        # ── Targets ────────────────────────────────────────────────────────
        elif data.startswith(b"chat_targets:"):
            source_id = int(data.decode().split(":", 1)[1])
            await show_targets(event, source_id)

        elif data.startswith(b"target_detail:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            await show_target_detail(event, source_id, target_id)

        elif data.startswith(b"target_add_start:"):
            source_id = int(data.decode().split(":")[1])
            from assistant.regex_conversation import target_add_start
            await target_add_start(event, source_id)

        elif data.startswith(b"header_set_start:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            from assistant.regex_conversation import header_set_start
            await header_set_start(event, source_id, target_id)

        elif data.startswith(b"header_clear:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            _, footer, _, footer_ents_json = get_header_footer(source_id, target_id)
            success = set_header_footer(source_id, target_id, None, footer, None, footer_ents_json)
            await event.answer("🗑️ Header cleared!" if success else "❌ Failed.", alert=not success)
            await show_target_detail(event, source_id, target_id)

        elif data.startswith(b"footer_set_start:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            from assistant.regex_conversation import footer_set_start
            await footer_set_start(event, source_id, target_id)

        elif data.startswith(b"footer_clear:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            header, _, header_ents_json, _ = get_header_footer(source_id, target_id)
            success = set_header_footer(source_id, target_id, header, None, header_ents_json, None)
            await event.answer("🗑️ Footer cleared!" if success else "❌ Failed.", alert=not success)
            await show_target_detail(event, source_id, target_id)

        elif data.startswith(b"target_link_del:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            success = remove_forward_rule(source_id, target_id)
            await event.answer("🗑️ Forward link deleted!" if success else "❌ Failed.", alert=not success)
            await show_targets(event, source_id)

        # ── Media Filters ──────────────────────────────────────────────────
        elif data.startswith(b"mf_show:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            from assistant.media_filter_menu import show_media_filters
            await show_media_filters(event, source_id, target_id)

        elif data.startswith(b"mf_toggle:"):
            parts = data.decode().split(":")
            source_id  = int(parts[1])
            target_id  = int(parts[2])
            media_type = parts[3]
            from database.database import get_media_filters, set_media_filter
            current = get_media_filters(source_id, target_id).get(media_type, 1)
            new_val = not bool(current)
            set_media_filter(source_id, target_id, media_type, new_val)
            state = "🟢 ON" if new_val else "🔴 OFF"
            await event.answer(f"{media_type} → {state}", alert=False)
            from assistant.media_filter_menu import show_media_filters
            await show_media_filters(event, source_id, target_id)

        # ── Per-Target Replacements ────────────────────────────────────────
        elif data.startswith(b"tgt_replace:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            await show_target_replacements(event, source_id, target_id)

        elif data.startswith(b"tgt_replace_del:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            find_text = _decode(parts[3])
            success = remove_replacement(source_id, target_id, find_text)
            await event.answer("🗑️ Rule removed!" if success else "❌ Not found.", alert=not success)
            await show_target_replacements(event, source_id, target_id)

        elif data.startswith(b"tgt_replace_add:"):
            parts = data.decode().split(":")
            source_id = int(parts[1])
            target_id = int(parts[2])
            from assistant.regex_conversation import tgt_plain_add_start
            await tgt_plain_add_start(event, source_id, target_id)

        # ── Cancel wizard ──────────────────────────────────────────────────
        elif data == b"add:cancel" or data.startswith(b"wizard:cancel:"):
            from assistant.regex_conversation import conversations
            conversations.pop(event.sender_id, None)
            await show_menu(event)

        # ── Database Dashboard ─────────────────────────────────────────────
        elif data == b"menu:db":
            from assistant.db_dashboard import show_db_overview
            await show_db_overview(event)

        elif data == b"db:forwards":
            from assistant.db_dashboard import show_db_forwards
            await show_db_forwards(event)

        elif data == b"db:super_users":
            from assistant.db_dashboard import show_db_super_users
            await show_db_super_users(event)

        elif data == b"db:replacements":
            from assistant.db_dashboard import show_db_replacements
            await show_db_replacements(event)

        elif data == b"db:regex_rules":
            from assistant.db_dashboard import show_db_regex_rules
            await show_db_regex_rules(event)

        elif data == b"db:header_footer":
            from assistant.db_dashboard import show_db_header_footer
            await show_db_header_footer(event)

        elif data == b"db:message_map":
            from assistant.db_dashboard import show_db_message_map
            await show_db_message_map(event)

        elif data == b"db:backup":
            from assistant.db_dashboard import do_db_backup
            await do_db_backup(event)

