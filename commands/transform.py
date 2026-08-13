from telethon import events
from core.permissions import authorized_only
from core.utils import respond, edit_or_reply
from database.database import (
    add_replacement, remove_replacement, get_replacements,
    set_header_footer, get_header_footer,
    add_regex_rule, remove_regex_rule, get_regex_rules,
    set_regex_enabled, is_regex_enabled
)
from commands.channel_manager import resolve_chat
from modules.forwarder import serialize_entities
import re

def register(client):
    # ─── REPLACEMENTS COMMANDS ────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r'^[./]replace\s+(\S+)\s+"([^"]+)"\s+"([^"]+)"'))
    @authorized_only()
    async def replace_add_handler(event):
        source_raw = event.pattern_match.group(1)
        find_text = event.pattern_match.group(2)
        replace_text = event.pattern_match.group(3)

        # Extract formatting entities for the replace text portion
        rep_ent_json = None
        if event.message.entities:
            rep_start_char = event.pattern_match.start(3)
            rep_end_char = event.pattern_match.end(3)
            rep_ents = []
            for ent in event.message.entities:
                # entity offset/length are UTF-16; map to chars via encode trick
                # We use raw message bytes to detect overlap with the match region
                ent_char_start = len(event.message.message.encode("utf-16-le")[:ent.offset * 2].decode("utf-16-le", errors="replace"))
                ent_char_end = ent_char_start + len(event.message.message.encode("utf-16-le")[:( ent.offset + ent.length) * 2].decode("utf-16-le", errors="replace")) - ent_char_start
                if ent_char_start >= rep_start_char and ent_char_end <= rep_end_char:
                    from copy import deepcopy
                    cloned = deepcopy(ent)
                    # Re-zero offset relative to start of replace text
                    from modules.forwarder import utf16_len
                    cloned.offset -= utf16_len(event.message.message[:rep_start_char])
                    rep_ents.append(cloned)
            rep_ent_json = serialize_entities(rep_ents) if rep_ents else None

        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return

        success = add_replacement(source_id, None, find_text, replace_text, rep_ent_json)
        if success:
            await edit_or_reply(
                status_msg,
                f"✅ **Replacement added for `{source_id}`:**\n"
                f"• Find: `{find_text}`\n"
                f"• Replace: `{replace_text}`",
                event
            )
        else:
            await edit_or_reply(status_msg, "❌ **Failed to save replacement rule.**", event)

    @client.on(events.NewMessage(pattern=r'^[./]replace_del\s+(\S+)\s+"([^"]+)"'))
    @authorized_only()
    async def replace_remove_handler(event):
        source_raw = event.pattern_match.group(1)
        find_text = event.pattern_match.group(2)
        
        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return
            
        success = remove_replacement(source_id, None, find_text)
        if success:
            await edit_or_reply(status_msg, f"🗑️ **Replacement rule removed for `{source_id}` (Find: `{find_text}`).**", event)
        else:
            await edit_or_reply(status_msg, "❌ **Replacement rule not found.**", event)

    @client.on(events.NewMessage(pattern=r'^[./]replace_list\s+(\S+)'))
    @authorized_only()
    async def replace_list_handler(event):
        source_raw = event.pattern_match.group(1)
        
        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return
            
        replaces = get_replacements(source_id, None)
        if not replaces:
            await edit_or_reply(status_msg, f"ℹ️ **No replacements configured for `{source_id}`.**", event)
            return

        response = f"📋 **Replacements for `{source_id}`:**\n\n"
        for idx, rule in enumerate(replaces, 1):
            find, replace = rule[0], rule[1]
            response += f"{idx}. Find: `{find}` ➡️ Replace: `{replace}`\n"

        await edit_or_reply(status_msg, response, event)

    # ─── HEADER COMMANDS ──────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r'^[./]header\s+(\S+)\s+(\S+)\s+(.+)'))
    @authorized_only()
    async def header_handler(event):
        source_raw = event.pattern_match.group(1)
        target_raw = event.pattern_match.group(2)
        header_text = event.pattern_match.group(3).strip()
        
        status_msg = await respond(event, "🔄 *Resolving chat entities...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
            target_id = await resolve_chat(userbot_client, target_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve chats!**\nError: `{e}`", event)
            return

        # Extract formatting entities for the header text
        h_ent_json = None
        if event.message.entities:
            hdr_start_char = event.pattern_match.start(3)
            h_ents = []
            from modules.forwarder import utf16_len
            for ent in event.message.entities:
                ent_char_start = len(event.message.message.encode("utf-16-le")[:ent.offset * 2].decode("utf-16-le", errors="replace"))
                ent_char_end = ent_char_start + len(event.message.message.encode("utf-16-le")[:(ent.offset + ent.length) * 2].decode("utf-16-le", errors="replace")) - ent_char_start
                if ent_char_start >= hdr_start_char:
                    from copy import deepcopy
                    cloned = deepcopy(ent)
                    cloned.offset -= utf16_len(event.message.message[:hdr_start_char])
                    h_ents.append(cloned)
            h_ent_json = serialize_entities(h_ents) if h_ents else None

        # Get existing footer (preserve it)
        _, footer, _, footer_ents_json = get_header_footer(source_id, target_id)

        success = set_header_footer(source_id, target_id, header_text, footer, h_ent_json, footer_ents_json)
        if success:
            await edit_or_reply(
                status_msg,
                f"✅ **Header set for forward link `{source_id}` ➡️ `{target_id}`:**\n"
                f"```{header_text}```",
                event
            )
        else:
            await edit_or_reply(status_msg, "❌ **Failed to save header.**", event)

    @client.on(events.NewMessage(pattern=r'^[./]clearheader\s+(\S+)\s+(\S+)'))
    @authorized_only()
    async def clear_header_handler(event):
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
            
        _, footer, _, footer_ents_json = get_header_footer(source_id, target_id)

        success = set_header_footer(source_id, target_id, None, footer, None, footer_ents_json)
        if success:
            await edit_or_reply(status_msg, f"🗑️ **Header cleared for forward link `{source_id}` ➡️ `{target_id}`.**", event)
        else:
            await edit_or_reply(status_msg, "❌ **Failed to clear header.**", event)

    # ─── FOOTER COMMANDS ──────────────────────────────────────────────────────
    @client.on(events.NewMessage(pattern=r'^[./]footer\s+(\S+)\s+(\S+)\s+(.+)'))
    @authorized_only()
    async def footer_handler(event):
        source_raw = event.pattern_match.group(1)
        target_raw = event.pattern_match.group(2)
        footer_text = event.pattern_match.group(3).strip()
        
        status_msg = await respond(event, "🔄 *Resolving chat entities...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
            target_id = await resolve_chat(userbot_client, target_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve chats!**\nError: `{e}`", event)
            return

        # Extract formatting entities for the footer text
        f_ent_json = None
        if event.message.entities:
            ftr_start_char = event.pattern_match.start(3)
            f_ents = []
            from modules.forwarder import utf16_len
            for ent in event.message.entities:
                ent_char_start = len(event.message.message.encode("utf-16-le")[:ent.offset * 2].decode("utf-16-le", errors="replace"))
                ent_char_end = ent_char_start + len(event.message.message.encode("utf-16-le")[:(ent.offset + ent.length) * 2].decode("utf-16-le", errors="replace")) - ent_char_start
                if ent_char_start >= ftr_start_char:
                    from copy import deepcopy
                    cloned = deepcopy(ent)
                    cloned.offset -= utf16_len(event.message.message[:ftr_start_char])
                    f_ents.append(cloned)
            f_ent_json = serialize_entities(f_ents) if f_ents else None

        # Get existing header (preserve it)
        header, _, header_ents_json, _ = get_header_footer(source_id, target_id)

        success = set_header_footer(source_id, target_id, header, footer_text, header_ents_json, f_ent_json)
        if success:
            await edit_or_reply(
                status_msg,
                f"✅ **Footer set for forward link `{source_id}` ➡️ `{target_id}`:**\n"
                f"```{footer_text}```",
                event
            )
        else:
            await edit_or_reply(status_msg, "❌ **Failed to save footer.**", event)

    @client.on(events.NewMessage(pattern=r'^[./]clearfooter\s+(\S+)\s+(\S+)'))
    @authorized_only()
    async def clear_footer_handler(event):
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
            
        header, _, header_ents_json, _ = get_header_footer(source_id, target_id)

        success = set_header_footer(source_id, target_id, header, None, header_ents_json, None)
        if success:
            await edit_or_reply(status_msg, f"🗑️ **Footer cleared for forward link `{source_id}` ➡️ `{target_id}`.**", event)
        else:
            await edit_or_reply(status_msg, "❌ **Failed to clear footer.**", event)

    # ─── REGEX COMMANDS ──────────────────────────────────────────────────────────────

    @client.on(events.NewMessage(pattern=r'^/regex_add\s+(\S+)\s+(\S+)\s+(.+?)\s*->\s*(.+)$'))
    @authorized_only()
    async def regex_add_handler(event):
        """Add a named regex rule: /regex_add <source> <rule_name> <pattern> -> <replacement>"""
        source_raw  = event.pattern_match.group(1)
        rule_name   = event.pattern_match.group(2)
        pattern     = event.pattern_match.group(3).strip()
        replacement = event.pattern_match.group(4).strip()

        # Validate pattern first
        try:
            re.compile(pattern)
        except re.error as rx_err:
            await respond(event, f"❌ **Invalid regex pattern:** `{pattern}`\nError: `{rx_err}`")
            return

        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return

        success = add_regex_rule(source_id, rule_name, pattern, replacement)
        if success:
            await edit_or_reply(
                status_msg,
                f"✅ **Regex rule `{rule_name}` saved for `{source_id}`:**\n"
                f"• Pattern: `{pattern}`\n"
                f"• Replacement: `{replacement}`\n\n"
                f"_Use /regex_on {source_raw} to enable regex processing._",
                event
            )
        else:
            await edit_or_reply(status_msg, "❌ **Failed to save regex rule.**", event)

    @client.on(events.NewMessage(pattern=r'^/regex_del\s+(\S+)\s+(\S+)'))
    @authorized_only()
    async def regex_del_handler(event):
        """Delete a named regex rule: /regex_del <source> <rule_name>"""
        source_raw = event.pattern_match.group(1)
        rule_name  = event.pattern_match.group(2)

        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return

        success = remove_regex_rule(source_id, rule_name)
        if success:
            await edit_or_reply(status_msg, f"🗑️ **Regex rule `{rule_name}` deleted for `{source_id}`.**", event)
        else:
            await edit_or_reply(status_msg, f"❌ **Rule `{rule_name}` not found.**", event)

    @client.on(events.NewMessage(pattern=r'^/regex_list\s+(\S+)'))
    @authorized_only()
    async def regex_list_handler(event):
        """List all regex rules for a source: /regex_list <source>"""
        source_raw = event.pattern_match.group(1)

        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return

        rules = get_regex_rules(source_id)
        enabled = is_regex_enabled(source_id)
        status_icon = "🟢 ON" if enabled else "🔴 OFF"

        if not rules:
            await edit_or_reply(
                status_msg,
                f"ℹ️ **No regex rules for `{source_id}`.**\n"
                f"Regex status: {status_icon}\n\n"
                f"Add one: `/regex_add {source_raw} re1_regex (@)\\S+ -> @username`",
                event
            )
            return

        response = f"🔧 **Regex Rules for `{source_id}` [{status_icon}]:**\n\n"
        for idx, (name, pat, repl) in enumerate(rules, 1):
            response += f"`{idx}.` **{name}**\n   Pattern: `{pat}`\n   Replace: `{repl}`\n\n"
        await edit_or_reply(status_msg, response, event)

    @client.on(events.NewMessage(pattern=r'^/regex_on\s+(\S+)'))
    @authorized_only()
    async def regex_on_handler(event):
        """Enable regex processing for a source: /regex_on <source>"""
        source_raw = event.pattern_match.group(1)

        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return

        set_regex_enabled(source_id, True)
        rules_count = len(get_regex_rules(source_id))
        await edit_or_reply(
            status_msg,
            f"🟢 **Regex ENABLED for `{source_id}`.**\n"
            f"Active rules: `{rules_count}`\n"
            f"All regex rules will now apply after plain replacements.",
            event
        )

    @client.on(events.NewMessage(pattern=r'^/regex_off\s+(\S+)'))
    @authorized_only()
    async def regex_off_handler(event):
        """Disable regex processing for a source: /regex_off <source>"""
        source_raw = event.pattern_match.group(1)

        status_msg = await respond(event, "🔄 *Resolving source chat...*")
        try:
            from core.client import client as userbot_client
            source_id = await resolve_chat(userbot_client, source_raw)
        except Exception as e:
            await edit_or_reply(status_msg, f"❌ **Failed to resolve source!**\nError: `{e}`", event)
            return

        set_regex_enabled(source_id, False)
        await edit_or_reply(
            status_msg,
            f"🔴 **Regex DISABLED for `{source_id}`.**\n"
            f"Only plain text replacements will apply.",
            event
        )
