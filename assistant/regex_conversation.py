"""
regex_conversation.py
─────────────────────
Conversation wizards for the Assistant Bot:

  • Regex rule add wizard  (called from inline button "➕ Add Regex Rule")
  • Plain replacement add wizard (called from inline "➕ Add Plain Rule")
  • The existing Add-Forward-Rule wizard (join + source/target) is kept in
    conversation.py — this module only handles the new rule wizards.

State dict format:
  conversations[user_id] = {
      "step": "join" | "source" | "target"          (forwarding wizard)
             | "plain_find" | "plain_replace"        (plain add wizard)
             | "regex_name" | "regex_pat" | "regex_repl"  (regex add wizard)
      "source_id": int  (when known)
      ...
  }
"""

import re
from telethon import events, Button
from assistant.menu import is_bot_owner, MAIN_MENU_TEXT, MAIN_MENU_BUTTONS
from database.database import (
    add_replacement, add_regex_rule,
    get_regex_rules, is_regex_enabled,
    add_super_user, set_header_footer, get_header_footer,
    add_forward_rule
)
from modules.forwarder import serialize_entities
from core.logger import logger

# Shared conversation state (user_id → state dict)
conversations: dict = {}


# ─── Entry points called by callbacks.py ────────────────────────────────────

async def plain_add_start(event, source_id: int):
    """Begin the plain-replacement add wizard."""
    user_id = event.sender_id
    conversations[user_id] = {"step": "plain_find", "source_id": source_id}
    await event.edit(
        "✏️ **Add Plain Replacement — Step 1/2**\n\n"
        "Send the **text to find** (exact match, case-sensitive).\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


async def regex_add_start(event, source_id: int):
    """Begin the regex rule add wizard."""
    user_id = event.sender_id
    conversations[user_id] = {"step": "regex_name", "source_id": source_id}
    await event.edit(
        "🔧 **Add Regex Rule — Step 1/3**\n\n"
        "Send a **rule name** (e.g. `re1_regex`, `links_rule`).\n"
        "Names must be unique per chat.\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


async def target_add_start(event, source_id: int):
    """Begin target adding wizard."""
    user_id = event.sender_id
    conversations[user_id] = {"step": "target_only", "source_id": source_id}
    await event.edit(
        "🎯 **Add Target Chat**\n\n"
        "Send the **Target Channel/Group ID or @username** to link to this source.\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


async def header_set_start(event, source_id: int, target_id: int):
    """Begin header setting wizard."""
    user_id = event.sender_id
    conversations[user_id] = {"step": "header_text", "source_id": source_id, "target_id": target_id}
    await event.edit(
        "📝 **Set Header**\n\n"
        "Send the **header text** or **forward a message** to use as header.\n"
        "Supports Markdown and premium custom emojis.\n"
        "Use `\\n` in your text to insert a real newline.\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


async def footer_set_start(event, source_id: int, target_id: int):
    """Begin footer setting wizard."""
    user_id = event.sender_id
    conversations[user_id] = {"step": "footer_text", "source_id": source_id, "target_id": target_id}
    await event.edit(
        "📝 **Set Footer**\n\n"
        "Send the **footer text** or **forward a message** to use as footer.\n"
        "Supports Markdown and premium custom emojis.\n"
        "Use `\\n` in your text to insert a real newline.\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


async def superuser_add_start(event):
    """Begin adding superuser wizard."""
    user_id = event.sender_id
    conversations[user_id] = {"step": "superuser_add"}
    await event.edit(
        "👑 **Add Super User**\n\n"
        "Send the **User ID or @username** of the user you want to authorize as a Super User.\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


async def tgt_plain_add_start(event, source_id: int, target_id: int):
    """Begin per-target replacement add wizard."""
    user_id = event.sender_id
    conversations[user_id] = {
        "step": "plain_find",
        "source_id": source_id,
        "target_id": target_id,   # non-None = per-target rule
    }
    await event.edit(
        "✏️ **Add Per-Target Replacement — Step 1/2**\n\n"
        "Send the **text to find** (exact match, case-sensitive).\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


# ─── Existing join-chat entry point (unchanged) ─────────────────────────────

async def join_chat_start(event):
    """Called by callbacks.py when user clicks 🚪 Join Chat."""
    if not await is_bot_owner(event):
        return
    user_id = event.sender_id
    conversations[user_id] = {"step": "join"}
    await event.edit(
        "🚪 **Join Channel / Group**\n\n"
        "Send the **invite link** or **@username** for the UserBot to join.\n\n"
        "**Examples:**\n"
        "• Public: `@my_channel`\n"
        "• Private: `https://t.me/+AbCdEfGh`\n\n"
        "_(or /cancel to abort)_",
        buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
    )


# ─── Message handler (handles ALL wizard steps) ──────────────────────────────

def register(bot_client):
    # ── Add Forward Rule — inline button start ────────────────────────────
    @bot_client.on(events.CallbackQuery(data=b"menu:add"))
    async def add_rule_start(event):
        if not await is_bot_owner(event):
            return
        user_id = event.sender_id
        conversations[user_id] = {"step": "source"}
        await event.edit(
            "➕ **Add Forwarding Rule — Step 1/2**\n\n"
            "Send the **Source Channel ID or @username**.\n\n"
            "_(or /cancel to abort)_",
            buttons=[[Button.inline("❌ Cancel", b"add:cancel")]]
        )

    # ── Conversation message handler ──────────────────────────────────────
    @bot_client.on(events.NewMessage)
    async def conversation_message_handler(event):
        if not await is_bot_owner(event):
            return

        user_id = event.sender_id
        if user_id not in conversations:
            return

        # Failsafe: Prevent double-step execution on the same message event
        if getattr(event, "_handled", False):
            return
        event._handled = True

        state = conversations[user_id]
        text  = event.text.strip() if event.text else ""

        # Global cancel
        if text.lower() in ("/cancel", "cancel"):
            conversations.pop(user_id, None)
            await event.respond(
                "❌ **Cancelled.**",
                buttons=MAIN_MENU_BUTTONS
            )
            return

        step = state.get("step")

        # ══════════════════════════════════════════════════════════════════
        # JOIN wizard
        # ══════════════════════════════════════════════════════════════════
        if step == "join":
            status_msg = await event.respond("🔄 *Joining chat…*")
            try:
                from core.client import client
                from core.utils import perform_join
                result = await perform_join(client, text)
                conversations.pop(user_id, None)
                await status_msg.edit(
                    f"{result}\n\nYou can now add a forwarding rule for this chat.",
                    buttons=MAIN_MENU_BUTTONS
                )
            except Exception as e:
                await status_msg.edit(
                    f"❌ **Failed to join!**\nError: `{e}`\n\n"
                    "_Fix the link/username and try again, or send /cancel._"
                )

        # ══════════════════════════════════════════════════════════════════
        # FORWARD RULE wizard  (source → target)
        # ══════════════════════════════════════════════════════════════════
        elif step == "source":
            status_msg = await event.respond("🔄 *Resolving source…*")
            try:
                from core.client import client
                from commands.channel_manager import resolve_chat
                from assistant.callbacks import get_chat_name
                source_id   = await resolve_chat(client, text)
                source_name = await get_chat_name(client, source_id)
                state["source_id"]  = source_id
                state["source_raw"] = text
                state["step"]       = "target"
                await status_msg.edit(
                    f"✅ **Source:** {source_name} (`{source_id}`)\n\n"
                    "Now send the **Target Channel ID or @username**.\n\n"
                    "_(or /cancel to abort)_"
                )
            except Exception as e:
                await status_msg.edit(
                    f"❌ **Could not resolve source!**\nError: `{e}`\n\n"
                    "_Try again or /cancel._"
                )

        elif step == "target":
            status_msg = await event.respond("🔄 *Resolving target…*")
            try:
                from core.client import client
                from commands.channel_manager import resolve_chat
                from assistant.callbacks import get_chat_name
                from database.database import add_forward_rule
                target_id   = await resolve_chat(client, text)
                source_id   = state["source_id"]
                source_name = await get_chat_name(client, source_id)
                target_name = await get_chat_name(client, target_id)
                success = add_forward_rule(source_id, target_id)
                conversations.pop(user_id, None)
                if success:
                    await status_msg.edit(
                        "🎉 **Forwarding Rule Created!**\n\n"
                        f"🔹 **Source:** {source_name} (`{source_id}`)\n"
                        f"🔸 **Target:** {target_name} (`{target_id}`)\n\n"
                        "TeleFlow will now forward messages in real-time. 🚀",
                        buttons=MAIN_MENU_BUTTONS
                    )
                else:
                    await status_msg.edit(
                        "❌ **Database error!** Could not save the rule.",
                        buttons=MAIN_MENU_BUTTONS
                    )
            except Exception as e:
                await status_msg.edit(
                    f"❌ **Could not resolve target!**\nError: `{e}`\n\n"
                    "_Try again or /cancel._"
                )

        # ══════════════════════════════════════════════════════════════════
        # PLAIN REPLACEMENT wizard  (find → replace)
        # ══════════════════════════════════════════════════════════════════
        elif step == "plain_find":
            if not text:
                await event.respond("⚠️ Please send the text to find, or /cancel.")
                return
            state["find_text"] = text
            state["step"]      = "plain_replace"
            await event.respond(
                f"✏️ **Add Plain Replacement — Step 2/2**\n\n"
                f"Find text: `{text}`\n\n"
                f"Now send the **replacement text** (what it should become).\n"
                f"You can also **forward a message** — its text/caption (with premium emojis) will be used.\n"
                f"Use `\\n` to insert a real newline.\n\n"
                f"_(or /cancel to abort)_"
            )

        elif step == "plain_replace":
            source_id  = state["source_id"]
            target_id  = state.get("target_id", None)  # None = global/source-level
            find_text  = state["find_text"]

            # Support forwarded messages: use caption or text of the forwarded message
            msg = event.message
            if msg.forward and (msg.message or msg.text):
                replace_text = msg.message or msg.text or ""
                raw_entities = msg.entities or []
            else:
                replace_text = text
                raw_entities = msg.entities or []

            rep_ent_json = serialize_entities(raw_entities) if raw_entities else None
            success = add_replacement(source_id, target_id, find_text, replace_text, rep_ent_json)
            conversations.pop(user_id, None)
            if success:
                if target_id:
                    # Per-target rule — go back to per-target replacements page
                    await event.respond(
                        f"✅ **Per-Target rule saved!**\n\n"
                        f"• Find: `{find_text}`\n"
                        f"• Replace: `{replace_text}`",
                        buttons=[
                            [Button.inline("✏️ View Rules",
                                           f"tgt_replace:{source_id}:{target_id}".encode())],
                            [Button.inline("🔙 Main Menu", b"menu:back")],
                        ]
                    )
                else:
                    # Source-level (global) rule
                    await event.respond(
                        f"✅ **Plain rule saved!**\n\n"
                        f"• Find: `{find_text}`\n"
                        f"• Replace: `{replace_text}`\n\n"
                        f"Source ID: `{source_id}`",
                        buttons=[
                            [Button.inline("✏️ View Plain Rules",
                                           f"chat_plain:{source_id}".encode())],
                            [Button.inline("🔙 Main Menu", b"menu:back")],
                        ]
                    )
            else:
                await event.respond(
                    "❌ **Failed to save rule.** Try again.",
                    buttons=MAIN_MENU_BUTTONS
                )

        # ══════════════════════════════════════════════════════════════════
        # REGEX RULE wizard  (name → pattern → replacement)
        # ══════════════════════════════════════════════════════════════════
        elif step == "regex_name":
            if not text or " " in text:
                await event.respond(
                    "⚠️ Rule name must be a **single word** (no spaces), e.g. `re1_regex`.\nTry again or /cancel."
                )
                return
            state["rule_name"] = text
            state["step"]      = "regex_pat"
            await event.respond(
                f"🔧 **Add Regex Rule — Step 2/3**\n\n"
                f"Rule name: `{text}`\n\n"
                f"Send the **regex pattern**.\n"
                f"Examples:\n"
                f"• `(@)\\S+`\n"
                f"• `(www|https?)\\S+`\n"
                f"• `(@|t\\.me?)\\S+`\n\n"
                f"_(or /cancel to abort)_"
            )

        elif step == "regex_pat":
            # Validate pattern
            try:
                re.compile(text)
            except re.error as rx_err:
                await event.respond(
                    f"❌ **Invalid regex pattern:** `{text}`\n"
                    f"Error: `{rx_err}`\n\n"
                    "_Fix it and try again, or /cancel._"
                )
                return
            state["pattern"] = text
            state["step"]    = "regex_repl"
            await event.respond(
                f"🔧 **Add Regex Rule — Step 3/3**\n\n"
                f"Rule name: `{state['rule_name']}`\n"
                f"Pattern: `{text}`\n\n"
                f"Send the **replacement text** (e.g. `@myUsername`).\n\n"
                f"_(or /cancel to abort)_"
            )

        elif step == "regex_repl":
            source_id   = state["source_id"]
            rule_name   = state["rule_name"]
            pattern     = state["pattern"]
            replacement = text
            success = add_regex_rule(source_id, rule_name, pattern, replacement)
            conversations.pop(user_id, None)

            if success:
                # Show current regex status
                regex_on = is_regex_enabled(source_id)
                rules    = get_regex_rules(source_id)
                note = (
                    "\n\n✅ Regex is **already ON** for this chat."
                    if regex_on
                    else f"\n\n⚠️ Regex is currently **OFF** for this chat.\n"
                         f"Tap **🟢 Enable Regex** in the Regex Rules menu to activate it."
                )
                await event.respond(
                    f"✅ **Regex rule saved!**\n\n"
                    f"• Name: `{rule_name}`\n"
                    f"• Pattern: `{pattern}`\n"
                    f"• Replacement: `{replacement}`\n"
                    f"• Total rules for this chat: `{len(rules)}`"
                    f"{note}",
                    buttons=[
                        [Button.inline("🔧 View Regex Rules",
                                       f"chat_regex:{source_id}".encode())],
                        [Button.inline("🔙 Main Menu", b"menu:back")],
                    ]
                )
            else:
                await event.respond(
                    "❌ **Failed to save regex rule.** Try again.",
                    buttons=MAIN_MENU_BUTTONS
                )

        # ══════════════════════════════════════════════════════════════════
        # TARGET ONLY wizard (linked to a known source)
        # ══════════════════════════════════════════════════════════════════
        elif step == "target_only":
            status_msg = await event.respond("🔄 *Resolving target…*")
            try:
                from core.client import client
                from commands.channel_manager import resolve_chat
                from assistant.callbacks import get_chat_name
                from database.database import add_forward_rule
                target_id   = await resolve_chat(client, text)
                source_id   = state["source_id"]
                source_name = await get_chat_name(client, source_id)
                target_name = await get_chat_name(client, target_id)
                success = add_forward_rule(source_id, target_id)
                conversations.pop(user_id, None)
                if success:
                    await status_msg.edit(
                        "🎉 **Target Chat Linked Successfully!**\n\n"
                        f"🔹 **Source:** {source_name} (`{source_id}`)\n"
                        f"🔸 **Target:** {target_name} (`{target_id}`)\n\n"
                        "TeleFlow will now forward messages. 🚀",
                        buttons=[
                            [Button.inline("📋 View Targets", f"chat_targets:{source_id}".encode())],
                            [Button.inline("🔙 Main Menu", b"menu:back")]
                        ]
                    )
                else:
                    await status_msg.edit(
                        "❌ **Database error!** Could not link the target.",
                        buttons=MAIN_MENU_BUTTONS
                    )
            except Exception as e:
                await status_msg.edit(
                    f"❌ **Could not resolve target!**\nError: `{e}`\n\n"
                    "_Try again or /cancel._"
                )

        # ══════════════════════════════════════════════════════════════════
        # HEADER TEXT wizard
        # ══════════════════════════════════════════════════════════════════
        elif step == "header_text":
            source_id = state["source_id"]
            target_id = state["target_id"]

            # Support forwarding a message: use its text/caption + entities
            msg = event.message
            if msg.forward and (msg.message or msg.text):
                header_val = msg.message or msg.text or ""
                raw_entities = msg.entities or []
            elif text.lower() in ("none", "clear", "/clear"):
                header_val = None
                raw_entities = []
            else:
                header_val = text
                raw_entities = msg.entities or []

            h_ent_json = serialize_entities(raw_entities) if (raw_entities and header_val) else None
            _, footer, _, footer_ents_json = get_header_footer(source_id, target_id)
            success = set_header_footer(source_id, target_id, header_val, footer, h_ent_json, footer_ents_json)
            conversations.pop(user_id, None)
            if success:
                status_lbl = f"Header set: `{header_val}`" if header_val else "Header cleared."
                await event.respond(
                    f"✅ **{status_lbl}**\n\nSource: `{source_id}`\nTarget: `{target_id}`",
                    buttons=[
                        [Button.inline("🔙 Back to Link", f"target_detail:{source_id}:{target_id}".encode())],
                        [Button.inline("🔙 Main Menu", b"menu:back")]
                    ]
                )
            else:
                await event.respond("❌ **Failed to update header.**", buttons=MAIN_MENU_BUTTONS)

        # ══════════════════════════════════════════════════════════════════
        # FOOTER TEXT wizard
        # ══════════════════════════════════════════════════════════════════
        elif step == "footer_text":
            source_id = state["source_id"]
            target_id = state["target_id"]

            # Support forwarding a message: use its text/caption + entities
            msg = event.message
            if msg.forward and (msg.message or msg.text):
                footer_val = msg.message or msg.text or ""
                raw_entities = msg.entities or []
            elif text.lower() in ("none", "clear", "/clear"):
                footer_val = None
                raw_entities = []
            else:
                footer_val = text
                raw_entities = msg.entities or []

            f_ent_json = serialize_entities(raw_entities) if (raw_entities and footer_val) else None
            header, _, header_ents_json, _ = get_header_footer(source_id, target_id)
            success = set_header_footer(source_id, target_id, header, footer_val, header_ents_json, f_ent_json)
            conversations.pop(user_id, None)
            if success:
                status_lbl = f"Footer set: `{footer_val}`" if footer_val else "Footer cleared."
                await event.respond(
                    f"✅ **{status_lbl}**\n\nSource: `{source_id}`\nTarget: `{target_id}`",
                    buttons=[
                        [Button.inline("🔙 Back to Link", f"target_detail:{source_id}:{target_id}".encode())],
                        [Button.inline("🔙 Main Menu", b"menu:back")]
                    ]
                )
            else:
                await event.respond("❌ **Failed to update footer.**", buttons=MAIN_MENU_BUTTONS)

        # ══════════════════════════════════════════════════════════════════
        # SUPERUSER ADD wizard
        # ══════════════════════════════════════════════════════════════════
        elif step == "superuser_add":
            status_msg = await event.respond("🔄 *Resolving user…*")
            try:
                from core.client import client
                from commands.channel_manager import resolve_chat
                user_id_resolved = await resolve_chat(client, text)
                success = add_super_user(user_id_resolved)
                conversations.pop(user_id, None)
                if success:
                    await status_msg.edit(
                        f"✅ **User authorized as Super User!**\n\nID: `{user_id_resolved}`",
                        buttons=[
                            [Button.inline("👑 View Super Users", b"menu:super_users")],
                            [Button.inline("🔙 Main Menu", b"menu:back")]
                        ]
                    )
                else:
                    await status_msg.edit("❌ **Database error!** Failed to add super user.", buttons=MAIN_MENU_BUTTONS)
            except Exception as e:
                await status_msg.edit(
                    f"❌ **Could not resolve user!**\nError: `{e}`\n\n"
                    "_Try again or /cancel._"
                )
