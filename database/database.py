import sqlite3
import os
import time
from core.logger import logger

# Try importing pymongo
try:
    import pymongo
    from pymongo import MongoClient
except ImportError:
    pymongo = None
    MongoClient = None

DB_PATH = "data/userbot.db"
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "teleflow")

# Global client/db variables
mongo_client = None
mongo_db = None
is_mongo_active = False

def _create_sqlite_schema():
    """Creates all SQLite tables. Called only when SQLite is the active backend."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS forwards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1,
            UNIQUE(source_id, target_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS super_users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS replacements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER,
            find_text TEXT NOT NULL,
            replace_text TEXT NOT NULL,
            replace_entities TEXT,
            UNIQUE(source_id, target_id, find_text)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS header_footer (
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            header TEXT,
            footer TEXT,
            header_entities TEXT,
            footer_entities TEXT,
            PRIMARY KEY(source_id, target_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS message_map (
            source_id INTEGER NOT NULL,
            source_msg_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            target_msg_id INTEGER NOT NULL,
            PRIMARY KEY(source_id, source_msg_id, target_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            source_id INTEGER PRIMARY KEY,
            regex_enabled INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS regex_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            rule_name TEXT NOT NULL,
            pattern TEXT NOT NULL,
            replacement TEXT NOT NULL,
            created_at INTEGER DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
            UNIQUE(source_id, rule_name)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS media_filters (
            source_id   INTEGER NOT NULL,
            target_id   INTEGER NOT NULL,
            text        INTEGER DEFAULT 1,
            sticker     INTEGER DEFAULT 1,
            photo       INTEGER DEFAULT 1,
            audio       INTEGER DEFAULT 1,
            video       INTEGER DEFAULT 1,
            gif         INTEGER DEFAULT 1,
            inline_btn  INTEGER DEFAULT 1,
            PRIMARY KEY (source_id, target_id)
        )
    """)
    conn.commit()
    # ── Safe migrations for existing DBs (add new columns if missing) ──
    try:
        c.execute("ALTER TABLE replacements ADD COLUMN replace_entities TEXT")
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        c.execute("ALTER TABLE header_footer ADD COLUMN header_entities TEXT")
        conn.commit()
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE header_footer ADD COLUMN footer_entities TEXT")
        conn.commit()
    except Exception:
        pass
    conn.close()


def init_db():
    """Initializes database. Uses MongoDB if MONGO_URI is set (required), otherwise SQLite."""
    global mongo_client, mongo_db, is_mongo_active

    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # ── MongoDB Mode (MONGO_URI configured) ────────────────────────────────
    if MONGO_URI:
        if not pymongo:
            raise RuntimeError(
                "MONGO_URI is configured but 'pymongo' library is not installed!\n"
                "Please run: pip install pymongo dnspython"
            )
        try:
            logger.info("Connecting to MongoDB Atlas...")
            mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            mongo_client.list_database_names()  # Force connection check
            mongo_db = mongo_client[MONGO_DB_NAME]
            is_mongo_active = True
            logger.info(f"MongoDB Atlas connected successfully (database: {MONGO_DB_NAME}).")

            # Ensure indexes
            mongo_db.forwards.create_index([("source_id", 1), ("target_id", 1)], unique=True)
            mongo_db.super_users.create_index("user_id", unique=True)
            mongo_db.replacements.create_index([("source_id", 1), ("target_id", 1), ("find_text", 1)], unique=True)
            mongo_db.header_footer.create_index([("source_id", 1), ("target_id", 1)], unique=True)
            mongo_db.message_map.create_index([("source_id", 1), ("source_msg_id", 1), ("target_id", 1)], unique=True)
            mongo_db.chat_settings.create_index("source_id", unique=True)
            mongo_db.regex_rules.create_index([("source_id", 1), ("rule_name", 1)], unique=True)
            mongo_db.media_filters.create_index([("source_id", 1), ("target_id", 1)], unique=True)

            # One-time migration from SQLite (only if SQLite file exists with old data)
            migrate_sqlite_to_mongodb()
            return  # ← MongoDB active, SQLite file is NOT created

        except Exception as e:
            # MongoDB failed — raise error so bot.py can notify owner and stop
            raise RuntimeError(f"MongoDB connection failed: {e}")

    # ── SQLite Mode (no MONGO_URI configured) ──────────────────────────────
    logger.info("No MONGO_URI set. Using local SQLite database.")
    _create_sqlite_schema()


def get_db_status():
    """Returns database connection status for system dashboard."""
    if is_mongo_active:
        t0 = time.time()
        try:
            mongo_client.admin.command('ping')
            latency = int((time.time() - t0) * 1000)
            return "MongoDB Atlas (Cloud)", f"{latency} ms"
        except Exception:
            return "MongoDB Atlas (Cloud)", "Disconnected/Error"
    else:
        return "SQLite (Local File)", "Local Latency (~0 ms)"


# Valid TeleFlow backup collection names
_BACKUP_COLLECTIONS = [
    "forwards", "super_users", "replacements", "header_footer",
    "regex_rules", "media_filters", "chat_settings", "message_map"
]

def restore_db_from_json(backup_data: dict) -> dict:
    """
    Restores the database from a TeleFlow backup dict (parsed from JSON).
    Clears all existing data and rebuilds from backup.
    Returns a report: {collection_name: restored_count, ...}
    Raises ValueError if backup_data is not a valid TeleFlow backup.
    """
    # ── Validate ──────────────────────────────────────────────────────────────
    if not isinstance(backup_data, dict):
        raise ValueError("Backup data must be a JSON object (dict).")
    found = [k for k in _BACKUP_COLLECTIONS if k in backup_data]
    if not found:
        raise ValueError(
            "This does not appear to be a valid TeleFlow backup file. "
            f"Expected at least one of: {_BACKUP_COLLECTIONS}"
        )

    report = {}

    if is_mongo_active:
        # ── MongoDB Restore ───────────────────────────────────────────────────
        for col in _BACKUP_COLLECTIONS:
            rows = backup_data.get(col, [])
            # Clear collection
            mongo_db[col].delete_many({})
            if rows:
                # Strip any leftover _id fields from export
                clean = [{k: v for k, v in r.items() if k != "_id"} for r in rows]
                mongo_db[col].insert_many(clean)
            report[col] = len(rows)
        logger.info(f"MongoDB restore completed: {report}")
    else:
        # ── SQLite Restore ────────────────────────────────────────────────────
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            c = conn.cursor()

            # forwards
            rows = backup_data.get("forwards", [])
            c.execute("DELETE FROM forwards")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO forwards (source_id, target_id, is_active) VALUES (?,?,?)",
                    (r["source_id"], r["target_id"], r.get("is_active", 1))
                )
            report["forwards"] = len(rows)

            # super_users
            rows = backup_data.get("super_users", [])
            c.execute("DELETE FROM super_users")
            for r in rows:
                c.execute("INSERT OR IGNORE INTO super_users (user_id) VALUES (?)", (r["user_id"],))
            report["super_users"] = len(rows)

            # replacements
            rows = backup_data.get("replacements", [])
            c.execute("DELETE FROM replacements")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO replacements (source_id, target_id, find_text, replace_text, replace_entities) VALUES (?,?,?,?,?)",
                    (r["source_id"], r.get("target_id"), r["find_text"], r["replace_text"], r.get("replace_entities"))
                )
            report["replacements"] = len(rows)

            # header_footer
            rows = backup_data.get("header_footer", [])
            c.execute("DELETE FROM header_footer")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO header_footer (source_id, target_id, header, footer, header_entities, footer_entities) VALUES (?,?,?,?,?,?)",
                    (r["source_id"], r["target_id"], r.get("header"), r.get("footer"), r.get("header_entities"), r.get("footer_entities"))
                )
            report["header_footer"] = len(rows)

            # chat_settings
            rows = backup_data.get("chat_settings", [])
            c.execute("DELETE FROM chat_settings")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO chat_settings (source_id, regex_enabled) VALUES (?,?)",
                    (r["source_id"], r.get("regex_enabled", 0))
                )
            report["chat_settings"] = len(rows)

            # regex_rules
            rows = backup_data.get("regex_rules", [])
            c.execute("DELETE FROM regex_rules")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO regex_rules (source_id, rule_name, pattern, replacement) VALUES (?,?,?,?)",
                    (r["source_id"], r["rule_name"], r["pattern"], r["replacement"])
                )
            report["regex_rules"] = len(rows)

            # media_filters
            rows = backup_data.get("media_filters", [])
            c.execute("DELETE FROM media_filters")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO media_filters (source_id, target_id, text, sticker, photo, audio, video, gif, inline_btn) VALUES (?,?,?,?,?,?,?,?,?)",
                    (r["source_id"], r["target_id"], r.get("text",1), r.get("sticker",1),
                     r.get("photo",1), r.get("audio",1), r.get("video",1), r.get("gif",1), r.get("inline_btn",1))
                )
            report["media_filters"] = len(rows)

            # message_map
            rows = backup_data.get("message_map", [])
            c.execute("DELETE FROM message_map")
            for r in rows:
                c.execute(
                    "INSERT OR IGNORE INTO message_map (source_id, source_msg_id, target_id, target_msg_id) VALUES (?,?,?,?)",
                    (r["source_id"], r["source_msg_id"], r["target_id"], r["target_msg_id"])
                )
            report["message_map"] = len(rows)

            conn.commit()
            logger.info(f"SQLite restore completed: {report}")
        finally:
            conn.close()

    return report

def migrate_sqlite_to_mongodb():
    """Migrates existing configuration from SQLite to MongoDB if MongoDB collections are empty."""
    if not is_mongo_active:
        return

    # Skip migration entirely if MongoDB already has data — no need to touch SQLite at all
    try:
        if mongo_db.forwards.count_documents({}) > 0 or mongo_db.super_users.count_documents({}) > 0:
            logger.info("SQLite to MongoDB Migration Completed successfully!")
            return
    except Exception:
        pass

    # Skip migration if SQLite file doesn't exist
    if not os.path.exists(DB_PATH):
        logger.info("SQLite database not found — skipping migration (MongoDB-only mode).")
        return

    sqlite_conn = None
    try:
        # Open with timeout to avoid blocking if another process briefly holds a lock
        sqlite_conn = sqlite3.connect(DB_PATH, timeout=10)
        sqlite_cursor = sqlite_conn.cursor()

        # 1. forwards
        if mongo_db.forwards.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT source_id, target_id, is_active FROM forwards")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info(f"Migrating {len(rows)} forward rules from SQLite to MongoDB...")
                for r in rows:
                    mongo_db.forwards.update_one(
                        {"source_id": r[0], "target_id": r[1]},
                        {"$set": {"is_active": r[2]}},
                        upsert=True
                    )
        
        # 2. super_users
        if mongo_db.super_users.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT user_id FROM super_users")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info(f"Migrating {len(rows)} super users from SQLite to MongoDB...")
                for r in rows:
                    mongo_db.super_users.update_one(
                        {"user_id": r[0]},
                        {"$set": {"user_id": r[0]}},
                        upsert=True
                    )
                    
        # 3. replacements
        if mongo_db.replacements.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT source_id, target_id, find_text, replace_text FROM replacements")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info(f"Migrating {len(rows)} replacements from SQLite to MongoDB...")
                for r in rows:
                    mongo_db.replacements.update_one(
                        {"source_id": r[0], "target_id": r[1], "find_text": r[2]},
                        {"$set": {"replace_text": r[3]}},
                        upsert=True
                    )

        # 4. header_footer
        if mongo_db.header_footer.count_documents({}) == 0:
            try:
                sqlite_cursor.execute("SELECT source_id, target_id, header, footer, header_entities, footer_entities FROM header_footer")
                col_count = 6
            except Exception:
                sqlite_cursor.execute("SELECT source_id, target_id, header, footer FROM header_footer")
                col_count = 4
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info(f"Migrating {len(rows)} headers/footers from SQLite to MongoDB...")
                for r in rows:
                    doc = {"header": r[2], "footer": r[3]}
                    if col_count == 6:
                        doc["header_entities"] = r[4]
                        doc["footer_entities"] = r[5]
                    mongo_db.header_footer.update_one(
                        {"source_id": r[0], "target_id": r[1]},
                        {"$set": doc},
                        upsert=True
                    )

        # 5. chat_settings
        if mongo_db.chat_settings.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT source_id, regex_enabled FROM chat_settings")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info("Migrating chat regex settings from SQLite to MongoDB...")
                for r in rows:
                    mongo_db.chat_settings.update_one(
                        {"source_id": r[0]},
                        {"$set": {"regex_enabled": r[1]}},
                        upsert=True
                    )

        # 6. regex_rules
        if mongo_db.regex_rules.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT source_id, rule_name, pattern, replacement, created_at FROM regex_rules")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info(f"Migrating {len(rows)} regex rules from SQLite to MongoDB...")
                for r in rows:
                    mongo_db.regex_rules.update_one(
                        {"source_id": r[0], "rule_name": r[1]},
                        {"$set": {"pattern": r[2], "replacement": r[3], "created_at": r[4]}},
                        upsert=True
                    )

        # 7. media_filters
        if mongo_db.media_filters.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT source_id, target_id, text, sticker, photo, audio, video, gif, inline_btn FROM media_filters")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info("Migrating media filters from SQLite to MongoDB...")
                for r in rows:
                    mongo_db.media_filters.update_one(
                        {"source_id": r[0], "target_id": r[1]},
                        {"$set": {
                            "text": r[2], "sticker": r[3], "photo": r[4], 
                            "audio": r[5], "video": r[6], "gif": r[7], "inline_btn": r[8]
                        }},
                        upsert=True
                    )
        
        # 8. message_map (Edit/Delete sync maps)
        if mongo_db.message_map.count_documents({}) == 0:
            sqlite_cursor.execute("SELECT source_id, source_msg_id, target_id, target_msg_id FROM message_map")
            rows = sqlite_cursor.fetchall()
            if rows:
                logger.info(f"Migrating {len(rows)} message mappings from SQLite to MongoDB...")
                docs = [
                    {"source_id": r[0], "source_msg_id": r[1], "target_id": r[2], "target_msg_id": r[3]}
                    for r in rows
                ]
                if docs:
                    mongo_db.message_map.insert_many(docs)

        logger.info("SQLite to MongoDB Migration Completed successfully!")
    except Exception as e:
        logger.error(f"Error during SQLite to MongoDB migration: {e}")
    finally:
        # ALWAYS close — prevents "database is locked" on reconnect
        if sqlite_conn is not None:
            try:
                sqlite_conn.close()
            except Exception:
                pass

# ─── FORWARD RULES ────────────────────────────────────────────────────────────

def add_forward_rule(source_id: int, target_id: int):
    """Adds a new forwarding link to the database or activates it."""
    if is_mongo_active:
        try:
            mongo_db.forwards.update_one(
                {"source_id": source_id, "target_id": target_id},
                {"$set": {"is_active": 1}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error adding forward rule: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO forwards (source_id, target_id, is_active) VALUES (?, ?, 1)",
                (source_id, target_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error adding forward rule: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def remove_forward_rule(source_id: int, target_id: int):
    """Removes a forwarding link from the database."""
    if is_mongo_active:
        try:
            result = mongo_db.forwards.delete_one({"source_id": source_id, "target_id": target_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"MongoDB Error removing forward rule: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM forwards WHERE source_id = ? AND target_id = ?",
                (source_id, target_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"SQLite Error removing forward rule: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def get_forward_rules():
    """Returns all configured forwarding rules."""
    if is_mongo_active:
        try:
            cursor = mongo_db.forwards.find({}, {"_id": 0, "source_id": 1, "target_id": 1, "is_active": 1})
            return [(doc["source_id"], doc["target_id"], doc.get("is_active", 1)) for doc in cursor]
        except Exception as e:
            logger.error(f"MongoDB Error fetching forward rules: {e}", exc_info=True)
            return []
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT source_id, target_id, is_active FROM forwards")
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"SQLite Error fetching forward rules: {e}", exc_info=True)
            return []
        finally:
            conn.close()

def get_targets_for_source(source_id: int):
    """Returns list of active target chat IDs for a given source chat ID."""
    if is_mongo_active:
        try:
            cursor = mongo_db.forwards.find({"source_id": source_id, "is_active": 1}, {"_id": 0, "target_id": 1})
            return [doc["target_id"] for doc in cursor]
        except Exception as e:
            logger.error(f"MongoDB Error fetching targets for source {source_id}: {e}", exc_info=True)
            return []
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT target_id FROM forwards WHERE source_id = ? AND is_active = 1", (source_id,))
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"SQLite Error fetching targets for source {source_id}: {e}", exc_info=True)
            return []
        finally:
            conn.close()

# ─── SUPER USERS ──────────────────────────────────────────────────────────────

def add_super_user(user_id: int):
    if is_mongo_active:
        try:
            mongo_db.super_users.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error adding super user: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT OR IGNORE INTO super_users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error adding super user: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def remove_super_user(user_id: int):
    if is_mongo_active:
        try:
            result = mongo_db.super_users.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"MongoDB Error removing super user: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM super_users WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"SQLite Error removing super user: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def get_super_users():
    if is_mongo_active:
        try:
            cursor = mongo_db.super_users.find({}, {"_id": 0, "user_id": 1})
            return [doc["user_id"] for doc in cursor]
        except Exception as e:
            logger.error(f"MongoDB Error fetching super users: {e}", exc_info=True)
            return []
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id FROM super_users")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"SQLite Error fetching super users: {e}", exc_info=True)
            return []
        finally:
            conn.close()

def is_super_user(user_id: int):
    if is_mongo_active:
        try:
            return mongo_db.super_users.find_one({"user_id": user_id}) is not None
        except Exception as e:
            logger.error(f"MongoDB Error checking super user status: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM super_users WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"SQLite Error checking super user status: {e}", exc_info=True)
            return False
        finally:
            conn.close()

# ─── REPLACEMENTS ─────────────────────────────────────────────────────────────

def add_replacement(source_id: int, target_id: int, find_text: str, replace_text: str, replace_entities: str = None):
    if is_mongo_active:
        try:
            mongo_db.replacements.update_one(
                {"source_id": source_id, "target_id": target_id, "find_text": find_text},
                {"$set": {"replace_text": replace_text, "replace_entities": replace_entities}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error adding replacement: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO replacements (source_id, target_id, find_text, replace_text, replace_entities) VALUES (?, ?, ?, ?, ?)",
                (source_id, target_id, find_text, replace_text, replace_entities)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error adding replacement: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def remove_replacement(source_id: int, target_id: int, find_text: str):
    if is_mongo_active:
        try:
            result = mongo_db.replacements.delete_one(
                {"source_id": source_id, "target_id": target_id, "find_text": find_text}
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"MongoDB Error removing replacement: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            if target_id is None:
                cursor.execute(
                    "DELETE FROM replacements WHERE source_id = ? AND target_id IS NULL AND find_text = ?",
                    (source_id, find_text)
                )
            else:
                cursor.execute(
                    "DELETE FROM replacements WHERE source_id = ? AND target_id = ? AND find_text = ?",
                    (source_id, target_id, find_text)
                )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"SQLite Error removing replacement: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def get_replacements(source_id: int, target_id: int):
    """Returns list of (find_text, replace_text, replace_entities_json) tuples."""
    if is_mongo_active:
        try:
            if target_id is None:
                query = {"source_id": source_id, "target_id": None}
            else:
                query = {"source_id": source_id, "$or": [{"target_id": target_id}, {"target_id": None}]}
            cursor = mongo_db.replacements.find(query, {"_id": 0, "find_text": 1, "replace_text": 1, "replace_entities": 1})
            return [(doc["find_text"], doc["replace_text"], doc.get("replace_entities")) for doc in cursor]
        except Exception as e:
            logger.error(f"MongoDB Error fetching replacements: {e}", exc_info=True)
            return []
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            if target_id is None:
                cursor.execute(
                    "SELECT find_text, replace_text, replace_entities FROM replacements WHERE source_id = ? AND target_id IS NULL",
                    (source_id,)
                )
            else:
                cursor.execute(
                    "SELECT find_text, replace_text, replace_entities FROM replacements WHERE source_id = ? AND (target_id = ? OR target_id IS NULL)",
                    (source_id, target_id)
                )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"SQLite Error fetching replacements: {e}", exc_info=True)
            return []
        finally:
            conn.close()

def get_target_specific_replacements(source_id: int, target_id: int):
    """Returns list of (find_text, replace_text, replace_entities_json) for specific link."""
    if is_mongo_active:
        try:
            cursor = mongo_db.replacements.find({"source_id": source_id, "target_id": target_id}, {"_id": 0, "find_text": 1, "replace_text": 1, "replace_entities": 1})
            return [(doc["find_text"], doc["replace_text"], doc.get("replace_entities")) for doc in cursor]
        except Exception as e:
            logger.error(f"MongoDB Error fetching target replacements: {e}", exc_info=True)
            return []
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT find_text, replace_text, replace_entities FROM replacements WHERE source_id = ? AND target_id = ?",
                (source_id, target_id)
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"SQLite Error fetching target replacements: {e}", exc_info=True)
            return []
        finally:
            conn.close()

# ─── HEADER & FOOTER ──────────────────────────────────────────────────────────

def set_header_footer(source_id: int, target_id: int, header: str, footer: str,
                       header_entities: str = None, footer_entities: str = None):
    if is_mongo_active:
        try:
            mongo_db.header_footer.update_one(
                {"source_id": source_id, "target_id": target_id},
                {"$set": {"header": header, "footer": footer,
                          "header_entities": header_entities, "footer_entities": footer_entities}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error setting header/footer: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO header_footer (source_id, target_id, header, footer, header_entities, footer_entities)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id, target_id) DO UPDATE SET
                     header=excluded.header, footer=excluded.footer,
                     header_entities=excluded.header_entities, footer_entities=excluded.footer_entities""",
                (source_id, target_id, header, footer, header_entities, footer_entities)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error setting header/footer: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def get_header_footer(source_id: int, target_id: int):
    """Returns (header, footer, header_entities_json, footer_entities_json)."""
    if is_mongo_active:
        try:
            doc = mongo_db.header_footer.find_one(
                {"source_id": source_id, "target_id": target_id},
                {"_id": 0, "header": 1, "footer": 1, "header_entities": 1, "footer_entities": 1}
            )
            if doc:
                return doc.get("header"), doc.get("footer"), doc.get("header_entities"), doc.get("footer_entities")
            return None, None, None, None
        except Exception as e:
            logger.error(f"MongoDB Error fetching header/footer: {e}", exc_info=True)
            return None, None, None, None
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT header, footer, header_entities, footer_entities FROM header_footer WHERE source_id = ? AND target_id = ?",
                (source_id, target_id)
            )
            row = cursor.fetchone()
            if row:
                return row[0], row[1], row[2], row[3]
            return None, None, None, None
        except Exception as e:
            logger.error(f"SQLite Error fetching header/footer: {e}", exc_info=True)
            return None, None, None, None
        finally:
            conn.close()

# ─── MESSAGE MAPPING ──────────────────────────────────────────────────────────

def save_message_map(source_id: int, source_msg_id: int, target_id: int, target_msg_id: int):
    if is_mongo_active:
        try:
            mongo_db.message_map.update_one(
                {"source_id": source_id, "source_msg_id": source_msg_id, "target_id": target_id},
                {"$set": {"target_msg_id": target_msg_id}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error saving message map: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO message_map (source_id, source_msg_id, target_id, target_msg_id) VALUES (?, ?, ?, ?)",
                (source_id, source_msg_id, target_id, target_msg_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error saving message map: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def get_mapped_message(source_id: int, source_msg_id: int, target_id: int):
    if is_mongo_active:
        try:
            doc = mongo_db.message_map.find_one(
                {"source_id": source_id, "source_msg_id": source_msg_id, "target_id": target_id},
                {"_id": 0, "target_msg_id": 1}
            )
            return doc["target_msg_id"] if doc else None
        except Exception as e:
            logger.error(f"MongoDB Error getting mapped message: {e}", exc_info=True)
            return None
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT target_msg_id FROM message_map WHERE source_id = ? AND source_msg_id = ? AND target_id = ?",
                (source_id, source_msg_id, target_id)
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"SQLite Error getting mapped message: {e}", exc_info=True)
            return None
        finally:
            conn.close()

def delete_message_map(source_id: int, source_msg_id: int):
    if is_mongo_active:
        try:
            mongo_db.message_map.delete_many({"source_id": source_id, "source_msg_id": source_msg_id})
            return True
        except Exception as e:
            logger.error(f"MongoDB Error deleting message map: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM message_map WHERE source_id = ? AND source_msg_id = ?",
                (source_id, source_msg_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error deleting message map: {e}", exc_info=True)
            return False
        finally:
            conn.close()

# ─── REGEX RULES ──────────────────────────────────────────────────────────────

def add_regex_rule(source_id: int, rule_name: str, pattern: str, replacement: str):
    """Adds or updates a named regex rule for a source chat."""
    if is_mongo_active:
        try:
            mongo_db.regex_rules.update_one(
                {"source_id": source_id, "rule_name": rule_name},
                {"$set": {"pattern": pattern, "replacement": replacement}, "$setOnInsert": {"created_at": int(time.time())}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error adding regex rule: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO regex_rules (source_id, rule_name, pattern, replacement)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_id, rule_name)
                   DO UPDATE SET pattern=excluded.pattern, replacement=excluded.replacement""",
                (source_id, rule_name, pattern, replacement)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error adding regex rule: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def remove_regex_rule(source_id: int, rule_name: str):
    """Deletes a named regex rule for a source chat."""
    if is_mongo_active:
        try:
            result = mongo_db.regex_rules.delete_one({"source_id": source_id, "rule_name": rule_name})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"MongoDB Error removing regex rule: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM regex_rules WHERE source_id = ? AND rule_name = ?",
                (source_id, rule_name)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"SQLite Error removing regex rule: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def get_regex_rules(source_id: int):
    """Returns all regex rules for a source chat, ordered by creation time."""
    if is_mongo_active:
        try:
            cursor = mongo_db.regex_rules.find({"source_id": source_id}).sort([("created_at", 1), ("_id", 1)])
            return [(doc["rule_name"], doc["pattern"], doc["replacement"]) for doc in cursor]
        except Exception as e:
            logger.error(f"MongoDB Error fetching regex rules: {e}", exc_info=True)
            return []
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT rule_name, pattern, replacement FROM regex_rules "
                "WHERE source_id = ? ORDER BY created_at ASC, id ASC",
                (source_id,)
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"SQLite Error fetching regex rules: {e}", exc_info=True)
            return []
        finally:
            conn.close()

def set_regex_enabled(source_id: int, enabled: bool):
    """Enables or disables regex processing for a source chat."""
    if is_mongo_active:
        try:
            mongo_db.chat_settings.update_one(
                {"source_id": source_id},
                {"$set": {"regex_enabled": 1 if enabled else 0}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error setting regex_enabled: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO chat_settings (source_id, regex_enabled) VALUES (?, ?)
                   ON CONFLICT(source_id) DO UPDATE SET regex_enabled=excluded.regex_enabled""",
                (source_id, 1 if enabled else 0)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error setting regex_enabled: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def is_regex_enabled(source_id: int) -> bool:
    """Returns True if regex processing is enabled for a source chat."""
    if is_mongo_active:
        try:
            doc = mongo_db.chat_settings.find_one({"source_id": source_id}, {"_id": 0, "regex_enabled": 1})
            return bool(doc and doc.get("regex_enabled"))
        except Exception as e:
            logger.error(f"MongoDB Error checking regex_enabled: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT regex_enabled FROM chat_settings WHERE source_id = ?",
                (source_id,)
            )
            row = cursor.fetchone()
            return bool(row and row[0])
        except Exception as e:
            logger.error(f"SQLite Error checking regex_enabled: {e}", exc_info=True)
            return False
        finally:
            conn.close()

# ─── MEDIA FILTERS ────────────────────────────────────────────────────────────

MEDIA_FILTER_COLUMNS = ["text", "sticker", "photo", "audio", "video", "gif", "inline_btn"]

def get_media_filters(source_id: int, target_id: int) -> dict:
    """Returns dict of media filter states for a source→target link.
    All types default to 1 (enabled) if no row exists."""
    if is_mongo_active:
        try:
            doc = mongo_db.media_filters.find_one({"source_id": source_id, "target_id": target_id}, {"_id": 0})
            if doc:
                return {col: doc.get(col, 1) for col in MEDIA_FILTER_COLUMNS}
            return {col: 1 for col in MEDIA_FILTER_COLUMNS}
        except Exception as e:
            logger.error(f"MongoDB Error fetching media filters: {e}", exc_info=True)
            return {col: 1 for col in MEDIA_FILTER_COLUMNS}
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT text, sticker, photo, audio, video, gif, inline_btn "
                "FROM media_filters WHERE source_id = ? AND target_id = ?",
                (source_id, target_id)
            )
            row = cursor.fetchone()
            if row:
                return dict(zip(MEDIA_FILTER_COLUMNS, row))
            return {col: 1 for col in MEDIA_FILTER_COLUMNS}
        except Exception as e:
            logger.error(f"SQLite Error fetching media filters: {e}", exc_info=True)
            return {col: 1 for col in MEDIA_FILTER_COLUMNS}
        finally:
            conn.close()

def set_media_filter(source_id: int, target_id: int, media_type: str, enabled: bool) -> bool:
    """Toggles a single media type filter for a source→target link."""
    if media_type not in MEDIA_FILTER_COLUMNS:
        logger.error(f"Invalid media_type for filter: {media_type}")
        return False
        
    if is_mongo_active:
        try:
            mongo_db.media_filters.update_one(
                {"source_id": source_id, "target_id": target_id},
                {"$set": {media_type: 1 if enabled else 0}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error setting media filter: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO media_filters (source_id, target_id) VALUES (?, ?)",
                (source_id, target_id)
            )
            cursor.execute(
                f"UPDATE media_filters SET {media_type} = ? WHERE source_id = ? AND target_id = ?",
                (1 if enabled else 0, source_id, target_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error setting media filter: {e}", exc_info=True)
            return False
        finally:
            conn.close()

def init_media_filters(source_id: int, target_id: int) -> bool:
    """Creates default (all ON) media filter row for a new forward link.
    Safe to call multiple times."""
    if is_mongo_active:
        try:
            defaults = {col: 1 for col in MEDIA_FILTER_COLUMNS}
            mongo_db.media_filters.update_one(
                {"source_id": source_id, "target_id": target_id},
                {"$setOnInsert": defaults},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB Error initializing media filters: {e}", exc_info=True)
            return False
    else:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO media_filters (source_id, target_id) VALUES (?, ?)",
                (source_id, target_id)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"SQLite Error initializing media filters: {e}", exc_info=True)
            return False
        finally:
            conn.close()
