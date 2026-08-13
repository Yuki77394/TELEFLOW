"""
assistant/db_dashboard.py
Inline Database Dashboard — view all MongoDB/SQLite collections directly in Telegram.
"""
import asyncio
import datetime
import json
import os
from telethon import Button
from database.database import (
    get_forward_rules, get_super_users, get_db_status,
    is_mongo_active, mongo_db, DB_PATH
)
from core.logger import logger

# Track file for daily backup
_DAILY_BACKUP_TRACK = os.path.join(os.path.dirname(DB_PATH), ".last_daily_backup")


# ─── Helper ──────────────────────────────────────────────────────────────────

def _chunk(lst, size):
    """Split list into chunks of `size`."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


# ─── DB Overview ─────────────────────────────────────────────────────────────

async def show_db_overview(event):
    """Main database dashboard screen with collection counts."""
    db_name, db_latency = get_db_status()

    if is_mongo_active and mongo_db is not None:
        try:
            fwd_count  = mongo_db.forwards.count_documents({})
            su_count   = mongo_db.super_users.count_documents({})
            rep_count  = mongo_db.replacements.count_documents({})
            hf_count   = mongo_db.header_footer.count_documents({})
            rr_count   = mongo_db.regex_rules.count_documents({})
            mf_count   = mongo_db.media_filters.count_documents({})
            mm_count   = mongo_db.message_map.count_documents({})
            cs_count   = mongo_db.chat_settings.count_documents({})
        except Exception as e:
            await event.edit(f"❌ Error fetching DB stats:\n`{e}`",
                             buttons=[[Button.inline("🔙 Back", b"menu:back")]])
            return
    else:
        import sqlite3
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            c = conn.cursor()
            def cnt(tbl): c.execute(f"SELECT COUNT(*) FROM {tbl}"); return c.fetchone()[0]
            fwd_count = cnt("forwards"); su_count = cnt("super_users")
            rep_count = cnt("replacements"); hf_count = cnt("header_footer")
            rr_count  = cnt("regex_rules"); mf_count = cnt("media_filters")
            mm_count  = cnt("message_map"); cs_count = cnt("chat_settings")
            conn.close()
        except Exception as e:
            await event.edit(f"❌ SQLite error:\n`{e}`",
                             buttons=[[Button.inline("🔙 Back", b"menu:back")]])
            return

    text = (
        "🗄️ **Database Dashboard**\n\n"
        f"**Engine:** `{db_name}`\n"
        f"**Ping:** `{db_latency}`\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📢 **Forward Rules:** `{fwd_count}`\n"
        f"👑 **Super Users:** `{su_count}`\n"
        f"🔄 **Replacements:** `{rep_count}`\n"
        f"🗂️ **Header/Footer:** `{hf_count}`\n"
        f"🔧 **Regex Rules:** `{rr_count}`\n"
        f"🎛️ **Media Filters:** `{mf_count}`\n"
        f"🗺️ **Message Map:** `{mm_count}`\n"
        f"⚙️ **Chat Settings:** `{cs_count}`\n"
        "━━━━━━━━━━━━━━━━━━━"
    )

    buttons = [
        [Button.inline("📢 Forwards",    b"db:forwards"),
         Button.inline("👑 Super Users", b"db:super_users")],
        [Button.inline("🔄 Replacements", b"db:replacements"),
         Button.inline("🔧 Regex Rules", b"db:regex_rules")],
        [Button.inline("🗂️ Header/Footer", b"db:header_footer"),
         Button.inline("🗺️ Msg Map",     b"db:message_map")],
        [Button.inline("📤 Backup to Saved Msgs", b"db:backup")],
        [Button.inline("🔙 Back", b"menu:back")],
    ]
    await event.edit(text, buttons=buttons)


# ─── Collection viewers ───────────────────────────────────────────────────────

async def show_db_forwards(event):
    rules = get_forward_rules()
    if not rules:
        text = "📢 **Forward Rules**\n\nKoi rule nahi hai abhi."
    else:
        lines = ["📢 **Forward Rules:**\n"]
        for i, r in enumerate(rules, 1):
            status = "🟢" if r[2] == 1 else "🔴"
            lines.append(f"{status} **#{i}** Source: `{r[0]}` → Target: `{r[1]}`")
        text = "\n".join(lines)
        if len(text) > 3800:
            text = text[:3800] + "\n\n_...aur bhi hain, truncated_"

    await event.edit(text, buttons=[
        [Button.inline("🔄 Refresh", b"db:forwards")],
        [Button.inline("🔙 DB Dashboard", b"menu:db")],
    ])


async def show_db_super_users(event):
    users = get_super_users()
    if not users:
        text = "👑 **Super Users**\n\nKoi super user nahi hai."
    else:
        lines = ["👑 **Super Users:**\n"]
        for i, uid in enumerate(users, 1):
            lines.append(f"**{i}.** `{uid}`")
        text = "\n".join(lines)

    await event.edit(text, buttons=[
        [Button.inline("🔄 Refresh", b"db:super_users")],
        [Button.inline("🔙 DB Dashboard", b"menu:db")],
    ])


async def show_db_replacements(event):
    if is_mongo_active and mongo_db is not None:
        try:
            docs = list(mongo_db.replacements.find({}, {"_id": 0}).limit(50))
        except Exception as e:
            await event.edit(f"❌ Error: `{e}`",
                             buttons=[[Button.inline("🔙 DB Dashboard", b"menu:db")]])
            return
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("SELECT source_id, target_id, find_text, replace_text FROM replacements LIMIT 50")
        rows = c.fetchall()
        conn.close()
        docs = [{"source_id": r[0], "target_id": r[1], "find_text": r[2], "replace_text": r[3]} for r in rows]

    if not docs:
        text = "🔄 **Replacements**\n\nKoi replacement nahi hai."
    else:
        lines = ["🔄 **Replacements** (max 50):\n"]
        for i, d in enumerate(docs, 1):
            tgt = f" → `{d['target_id']}`" if d.get('target_id') else ""
            lines.append(f"**{i}.** `{d['find_text']}` → `{d['replace_text']}`\n"
                         f"   Src:`{d['source_id']}`{tgt}")
        text = "\n".join(lines)
        if len(text) > 3800:
            text = text[:3800] + "\n\n_...truncated_"

    await event.edit(text, buttons=[
        [Button.inline("🔄 Refresh", b"db:replacements")],
        [Button.inline("🔙 DB Dashboard", b"menu:db")],
    ])


async def show_db_regex_rules(event):
    if is_mongo_active and mongo_db is not None:
        try:
            docs = list(mongo_db.regex_rules.find({}, {"_id": 0}).sort("created_at", 1).limit(30))
        except Exception as e:
            await event.edit(f"❌ Error: `{e}`",
                             buttons=[[Button.inline("🔙 DB Dashboard", b"menu:db")]])
            return
        if not docs:
            text = "🔧 **Regex Rules**\n\nKoi regex rule nahi hai."
        else:
            lines = ["🔧 **Regex Rules:**\n"]
            for i, d in enumerate(docs, 1):
                lines.append(f"**{i}.** `{d['rule_name']}` (src:`{d['source_id']}`)"
                             f"\n   Pattern: `{d['pattern']}`"
                             f"\n   Replace: `{d['replacement']}`")
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n\n_...truncated_"
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("SELECT source_id, rule_name, pattern, replacement FROM regex_rules ORDER BY created_at ASC LIMIT 30")
        rows = c.fetchall()
        conn.close()
        if not rows:
            text = "🔧 **Regex Rules**\n\nKoi regex rule nahi hai."
        else:
            lines = ["🔧 **Regex Rules:**\n"]
            for i, r in enumerate(rows, 1):
                lines.append(f"**{i}.** `{r[1]}` (src:`{r[0]}`)"
                             f"\n   Pattern: `{r[2]}`"
                             f"\n   Replace: `{r[3]}`")
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n\n_...truncated_"

    await event.edit(text, buttons=[
        [Button.inline("🔄 Refresh", b"db:regex_rules")],
        [Button.inline("🔙 DB Dashboard", b"menu:db")],
    ])


async def show_db_header_footer(event):
    if is_mongo_active and mongo_db is not None:
        try:
            docs = list(mongo_db.header_footer.find({}, {"_id": 0}).limit(30))
        except Exception as e:
            await event.edit(f"❌ Error: `{e}`",
                             buttons=[[Button.inline("🔙 DB Dashboard", b"menu:db")]])
            return
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("SELECT source_id, target_id, header, footer FROM header_footer LIMIT 30")
        rows = c.fetchall()
        conn.close()
        docs = [{"source_id": r[0], "target_id": r[1], "header": r[2], "footer": r[3]} for r in rows]

    if not docs:
        text = "🗂️ **Header/Footer**\n\nKoi header/footer set nahi hai."
    else:
        lines = ["🗂️ **Header/Footer** (max 30):\n"]
        for i, d in enumerate(docs, 1):
            h = (d.get("header") or "—")[:40]
            f = (d.get("footer") or "—")[:40]
            lines.append(f"**{i}.** Src:`{d['source_id']}` → Tgt:`{d['target_id']}`\n"
                         f"   Header: `{h}`\n"
                         f"   Footer: `{f}`")
        text = "\n".join(lines)
        if len(text) > 3800:
            text = text[:3800] + "\n_...truncated_"

    await event.edit(text, buttons=[
        [Button.inline("🔄 Refresh", b"db:header_footer")],
        [Button.inline("🔙 DB Dashboard", b"menu:db")],
    ])


async def show_db_message_map(event):
    if is_mongo_active and mongo_db is not None:
        try:
            total = mongo_db.message_map.count_documents({})
            sample = list(mongo_db.message_map.find({}, {"_id": 0}).limit(10))
        except Exception as e:
            await event.edit(f"❌ Error: `{e}`",
                             buttons=[[Button.inline("🔙 DB Dashboard", b"menu:db")]])
            return
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=5)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM message_map"); total = c.fetchone()[0]
        c.execute("SELECT source_id, source_msg_id, target_id, target_msg_id FROM message_map ORDER BY rowid DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        sample = [{"source_id": r[0], "source_msg_id": r[1], "target_id": r[2], "target_msg_id": r[3]} for r in rows]

    lines = [f"🗺️ **Message Map** (Total: `{total}` entries)\n",
             "_Showing last 10 entries:_\n"]
    for d in sample:
        lines.append(f"Src:`{d['source_id']}`/Msg:`{d['source_msg_id']}` → "
                     f"Tgt:`{d['target_id']}`/Msg:`{d['target_msg_id']}`")
    text = "\n".join(lines)

    await event.edit(text, buttons=[
        [Button.inline("🔄 Refresh", b"db:message_map")],
        [Button.inline("🔙 DB Dashboard", b"menu:db")],
    ])


# ─── Backup ───────────────────────────────────────────────────────────────────

async def do_db_backup(event):
    """Export all MongoDB collections as JSON and send to Saved Messages."""
    await event.edit("⏳ **Backup ban raha hai...**\n_Please wait..._")

    from core.client import client
    import json, datetime, os

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        if is_mongo_active and mongo_db is not None:
            backup_data = {}
            collections = ["forwards", "super_users", "replacements",
                           "header_footer", "regex_rules", "media_filters",
                           "chat_settings", "message_map"]
            for col in collections:
                docs = list(mongo_db[col].find({}, {"_id": 0}))
                backup_data[col] = docs

            backup_path = f"data/mongo_backup_{timestamp}.json"
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)

            caption = (
                f"📦 **MongoDB Backup**\n"
                f"🕐 `{timestamp}`\n\n"
                f"Collections exported:\n"
                + "\n".join(f"• `{k}`: {len(v)} records" for k, v in backup_data.items())
            )
        else:
            # SQLite backup
            import shutil
            backup_path = f"data/sqlite_backup_{timestamp}.db"
            shutil.copy2(DB_PATH, backup_path)
            caption = f"📦 **SQLite Backup**\n🕐 `{timestamp}`"

        # Send to the user who clicked the button via Assistant Bot
        await event.client.send_file(event.sender_id, backup_path, caption=caption, force_document=True)

        # Clean up temp file
        try:
            os.remove(backup_path)
        except Exception:
            pass

        await event.edit(
            f"✅ **Backup Successfully Sent!**\n\n"
            f"📁 File: `{os.path.basename(backup_path)}`\n"
            f"📩 Aapko backup file isi chat me bhej di gayi hai.",
            buttons=[
                [Button.inline("🔙 DB Dashboard", b"menu:db")],
            ]
        )
    except Exception as e:
        logger.error(f"Backup error: {e}", exc_info=True)
        await event.edit(
            f"❌ **Backup failed!**\n`{e}`",
            buttons=[[Button.inline("🔙 DB Dashboard", b"menu:db")]]
        )


# ─── Daily Backup Loop ────────────────────────────────────────────────────────

async def _run_daily_backup(bot_client):
    """Export all collections as JSON and send to owner + userbot Saved Messages."""
    import config
    from core.client import client as userbot_client

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_data = {}

    try:
        if is_mongo_active and mongo_db is not None:
            collections = ["forwards", "super_users", "replacements",
                           "header_footer", "regex_rules", "media_filters",
                           "chat_settings", "message_map"]
            for col in collections:
                docs = list(mongo_db[col].find({}, {"_id": 0}))
                backup_data[col] = docs
        else:
            import sqlite3
            conn = sqlite3.connect(DB_PATH, timeout=5)
            c = conn.cursor()
            def _fetch(table, cols):
                c.execute(f"SELECT {cols} FROM {table}")
                keys = [d[0] for d in c.description]
                return [dict(zip(keys, row)) for row in c.fetchall()]
            backup_data["forwards"]      = _fetch("forwards", "source_id,target_id,is_active")
            backup_data["super_users"]   = _fetch("super_users", "user_id")
            backup_data["replacements"]  = _fetch("replacements", "source_id,target_id,find_text,replace_text")
            backup_data["header_footer"] = _fetch("header_footer", "source_id,target_id,header,footer")
            backup_data["regex_rules"]   = _fetch("regex_rules", "source_id,rule_name,pattern,replacement")
            backup_data["media_filters"] = _fetch("media_filters", "source_id,target_id,text,sticker,photo,audio,video,gif,inline_btn")
            backup_data["chat_settings"] = _fetch("chat_settings", "source_id,regex_enabled")
            backup_data["message_map"]   = _fetch("message_map", "source_id,source_msg_id,target_id,target_msg_id")
            conn.close()

        backup_path = os.path.join(os.path.dirname(DB_PATH), f"daily_backup_{timestamp}.json")
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)

        caption = (
            f"📅 **TeleFlow Daily Backup**\n"
            f"🕐 `{timestamp}`\n\n"
            "Collections:\n"
            + "\n".join(f"• `{k}`: {len(v)} records" for k, v in backup_data.items())
        )

        # 1. Send to userbot's Saved Messages
        try:
            await userbot_client.send_file("me", backup_path, caption=caption, force_document=True)
            logger.info("Daily backup sent to Saved Messages (userbot).")
        except Exception as e:
            logger.warning(f"Daily backup: Failed to send to Saved Messages: {e}")

        # 2. Send to owner via bot_client
        if config.OWNER_ID and bot_client:
            try:
                await bot_client.send_file(
                    config.OWNER_ID, backup_path,
                    caption=caption + "\n\n🔒 _Owner ko bheja gaya daily backup._",
                    force_document=True
                )
                logger.info(f"Daily backup sent to owner ({config.OWNER_ID}) via Assistant Bot.")
            except Exception as e:
                logger.warning(f"Daily backup: Failed to send to owner: {e}")

        # Update tracking
        with open(_DAILY_BACKUP_TRACK, "w") as f:
            f.write(datetime.date.today().isoformat())

        # Cleanup temp file
        try:
            os.remove(backup_path)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Daily backup error: {e}", exc_info=True)


async def daily_backup_loop(bot_client):
    """
    Background task: checks every hour if today's backup has been sent.
    If not, runs the backup. Runs forever until bot stops.
    """
    logger.info("Daily backup loop started.")
    while True:
        try:
            today = datetime.date.today().isoformat()
            last = ""
            if os.path.exists(_DAILY_BACKUP_TRACK):
                with open(_DAILY_BACKUP_TRACK) as f:
                    last = f.read().strip()

            if last != today:
                logger.info(f"Daily backup due (last: {last or 'never'}). Running now...")
                await _run_daily_backup(bot_client)
        except Exception as e:
            logger.error(f"Daily backup loop error: {e}", exc_info=True)

        # Check again in 1 hour
        await asyncio.sleep(3600)


# ─── Registration ─────────────────────────────────────────────────────────────

def register(bot_client):
    """Start the daily backup background loop when the assistant bot loads."""
    asyncio.get_event_loop().create_task(daily_backup_loop(bot_client))
    logger.info("Daily backup scheduler registered.")
