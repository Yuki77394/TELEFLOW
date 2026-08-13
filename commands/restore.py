"""
commands/restore.py
Owner-only database restore command.

Usage:
  .restore / /restore  — No reply, no attachment:
      UserBot searches its own Saved Messages for the latest TeleFlow
      backup JSON file and restores from it.

  .restore / /restore  — While replying to a JSON backup file message:
      Downloads and restores from that specific file.
"""

import io
import json
import re
import os
from telethon import events
from telethon.tl.types import MessageMediaDocument
from core.permissions import is_owner
from core.logger import logger


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _format_report(report: dict, source: str) -> str:
    """Formats a restore report as a Telegram message."""
    lines = [
        "✅ **Database Restore Successful!**\n",
        f"📂 **Source:** `{source}`\n",
        "━━━━━━━━━━━━━━━━━━━",
        "📊 **Restored Data:**\n",
    ]
    icons = {
        "forwards":      "📢",
        "super_users":   "👑",
        "replacements":  "🔄",
        "header_footer": "🗂️",
        "regex_rules":   "🔧",
        "media_filters": "🎛️",
        "chat_settings": "⚙️",
        "message_map":   "🗺️",
    }
    total = 0
    for col, count in report.items():
        icon = icons.get(col, "•")
        lines.append(f"{icon} **{col}**: `{count}` records")
        total += count
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📦 **Total records restored:** `{total}`")
    return "\n".join(lines)


async def _restore_from_bytes(data_bytes: bytes) -> dict:
    """Parse and validate JSON bytes, then restore. Returns report."""
    from database.database import restore_db_from_json
    try:
        backup_data = json.loads(data_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"File is not valid JSON: {e}")
    return restore_db_from_json(backup_data)


async def _find_latest_backup_in_saved_messages(userbot_client):
    """
    Searches the userbot's Saved Messages (dialogs 'me') for the most recent
    message containing a TeleFlow backup JSON file.
    Returns (message, file_name) or (None, None).
    """
    # Search recent messages in Saved Messages
    async for message in userbot_client.iter_messages("me", limit=200):
        if not message.media:
            continue
        if not isinstance(message.media, MessageMediaDocument):
            continue
        doc = message.media.document
        if not doc:
            continue
        # Find file name from attributes
        file_name = None
        for attr in doc.attributes:
            if hasattr(attr, "file_name"):
                file_name = attr.file_name
                break
        if file_name and file_name.endswith(".json") and "backup" in file_name.lower():
            return message, file_name
    return None, None


# ─── Command Registration ─────────────────────────────────────────────────────

def register(client):
    @client.on(events.NewMessage(pattern=r'^[./]restore$'))
    async def restore_handler(event):
        # ── Owner check ───────────────────────────────────────────────────────
        if not await is_owner(event):
            return  # Silently ignore non-owners

        status_msg = await event.reply("🔍 **Restore process started...**\n_Please wait..._")

        try:
            replied = await event.get_reply_message()

            # ── Case 1: Owner replied to a JSON file ─────────────────────────
            if replied and replied.media and isinstance(replied.media, MessageMediaDocument):
                doc = replied.media.document
                # Check it is a JSON file
                file_name = None
                for attr in doc.attributes:
                    if hasattr(attr, "file_name"):
                        file_name = attr.file_name
                        break

                if not file_name or not file_name.endswith(".json"):
                    await status_msg.edit(
                        "❌ **Invalid file!**\n\n"
                        "Sirf `.json` backup files hi restore ki ja sakti hain.\n"
                        "Please ek valid TeleFlow backup JSON file par reply karein."
                    )
                    return

                await status_msg.edit(f"⬇️ **Downloading:** `{file_name}`...\n_Validating..._")
                data_bytes = await client.download_media(replied.media, bytes)
                report = await _restore_from_bytes(data_bytes)
                await status_msg.edit(_format_report(report, f"Replied file: {file_name}"))
                logger.info(f"Database restored from replied file: {file_name} | Report: {report}")

            # ── Case 2: No reply — search Saved Messages ──────────────────────
            else:
                await status_msg.edit(
                    "🔍 **Saved Messages mein dhundh raha hoon...**\n"
                    "_Latest TeleFlow backup JSON file search ho rahi hai..._"
                )
                # Get userbot client (could be same client or separate)
                try:
                    from core.client import client as userbot_client
                except Exception:
                    userbot_client = client

                backup_msg, file_name = await _find_latest_backup_in_saved_messages(userbot_client)

                if backup_msg is None:
                    await status_msg.edit(
                        "❌ **Koi backup nahi mila!**\n\n"
                        "Aapke **Saved Messages** mein koi TeleFlow backup JSON file nahi mili.\n\n"
                        "**Kya karein:**\n"
                        "1️⃣ Bot ke main menu mein jaayein.\n"
                        "2️⃣ **🗄️ DB Dashboard** → **📤 Backup to Saved Msgs** click karein.\n"
                        "3️⃣ Backup file send hone ke baad dobara `/restore` try karein.\n\n"
                        "**Ya:** Kisi backup file par **reply karke** `/restore` bhejein."
                    )
                    return

                await status_msg.edit(
                    f"📂 **Backup mila:** `{file_name}`\n"
                    f"⬇️ **Download aur restore ho raha hai...**"
                )
                data_bytes = await userbot_client.download_media(backup_msg.media, bytes)
                report = await _restore_from_bytes(data_bytes)
                await status_msg.edit(_format_report(report, f"Saved Messages: {file_name}"))
                logger.info(f"Database restored from Saved Messages: {file_name} | Report: {report}")

        except ValueError as ve:
            await status_msg.edit(
                f"❌ **Restore Failed — Invalid Backup!**\n\n`{ve}`\n\n"
                "Please ek valid TeleFlow backup JSON file use karein."
            )
            logger.warning(f"Restore validation error: {ve}")
        except Exception as e:
            await status_msg.edit(f"❌ **Restore Failed!**\n\n`{e}`")
            logger.error(f"Restore error: {e}", exc_info=True)
