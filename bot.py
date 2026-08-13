import sys
import config
import asyncio
from core.logger import logger
from database.database import init_db
from core.client import client, bot_client
from loader import load_plugins, load_assistant_plugins

async def start_platform():
    logger.info("Starting TeleFlow platform...")
    
    # Initialize Database
    try:
        init_db()
    except Exception as db_err:
        logger.error(f"Database initialization failed: {db_err}. Trying to notify owner...")
        try:
            # Try notifying via Assistant Bot if configured
            if config.BOT_TOKEN:
                from telethon.sessions import MemorySession
                from telethon import TelegramClient
                temp_bot = TelegramClient(MemorySession(), config.API_ID, config.API_HASH)
                await temp_bot.start(bot_token=config.BOT_TOKEN)
                notify_id = config.OWNER_ID if config.OWNER_ID else "me"
                await temp_bot.send_message(
                    notify_id,
                    f"⚠️ **TeleFlow Critical Alert!**\n\n"
                    f"Database connection failed and the platform has stopped.\n"
                    f"Local SQLite fallback is disabled.\n\n"
                    f"❌ **Error:** `{db_err}`"
                )
                await temp_bot.disconnect()
            # Or try notifying via UserBot String Session
            elif config.SESSION_STRING:
                from telethon.sessions import StringSession
                from telethon import TelegramClient
                temp_client = TelegramClient(StringSession(config.SESSION_STRING), config.API_ID, config.API_HASH)
                await temp_client.start()
                notify_id = config.OWNER_ID if config.OWNER_ID else "me"
                await temp_client.send_message(
                    notify_id,
                    f"⚠️ **TeleFlow Critical Alert!**\n\n"
                    f"Database connection failed and the platform has stopped.\n"
                    f"Local SQLite fallback is disabled.\n\n"
                    f"❌ **Error:** `{db_err}`"
                )
                await temp_client.disconnect()
        except Exception as notify_err:
            logger.error(f"Failed to send critical error notification to owner: {notify_err}")
        
        # Raise the error to stop bot execution
        raise db_err

    
    # Load Command/Feature Plugins on correct clients
    load_plugins(client, bot_client)
    
    logger.info("Starting Telegram Clients...")
    
    # Start UserBot client asynchronously
    await client.start()
    me = await client.get_me()
    logger.info(f"TeleFlow UserBot is running as {me.first_name} (@{me.username or ''}) [ID: {me.id}]")
    
    # Start Assistant Bot asynchronously if configured
    bot_me = None
    if bot_client:
        logger.info("Starting Assistant Bot...")
        await bot_client.start(bot_token=config.BOT_TOKEN)
        load_assistant_plugins(bot_client)
        bot_me = await bot_client.get_me()
        logger.info(f"Assistant Bot is running as @{bot_me.username} [ID: {bot_me.id}]")
        
    # Send startup notification to owner
    try:
        # Fetch recent dialogs to populate entity cache (covers most chats,
        # plus anything the account has interacted with recently)
        try:
            await client.get_dialogs(limit=None)
        except Exception as cache_err:
            logger.debug(f"Failed to fetch dialogs for cache: {cache_err}")

        # Explicitly warm the cache for every source/target used in
        # forwarding rules. get_dialogs(limit=N) only covers the N most
        # recently active chats -- a channel/group that isn't in that
        # recent window (or a user chat with no message history) never
        # gets its access_hash cached, causing:
        # "ValueError: Could not find the input entity for PeerUser/PeerChannel(...)"
        # the first time the forwarder tries to send to it.
        try:
            from database.database import get_forward_rules
            known_ids = set()
            for source_id, target_id, _active in get_forward_rules():
                known_ids.add(source_id)
                known_ids.add(target_id)

            for entity_id in known_ids:
                try:
                    await client.get_entity(entity_id)
                except Exception as warm_err:
                    logger.warning(
                        f"Could not pre-cache entity {entity_id} at startup: {warm_err}. "
                        f"Forwarding to/from this chat will fail until it's resolved "
                        f"(e.g. the UserBot receives a message from it, or shares a mutual chat)."
                    )
        except Exception as warm_all_err:
            logger.warning(f"Entity cache warmup for forward rules failed: {warm_all_err}")

        notify_id = config.OWNER_ID if config.OWNER_ID else "me"
        bot_info = f"\n🤖 **Assistant Bot:** @{bot_me.username}" if bot_me else ""
        startup_text = (
            "🚀 **TeleFlow successfully started!**\n\n"
            f"👤 **UserBot Account:** {me.first_name} (@{me.username or ''})\n"
            f"🆔 **ID:** `{me.id}`"
            f"{bot_info}"
        )
        
        try:
            await client.send_message(notify_id, startup_text)
            logger.info(f"Startup notification sent to {notify_id}")
        except ValueError as val_err:
            if notify_id != "me":
                logger.warning(f"Could not resolve owner ID {notify_id}, sending to 'me'. Error: {val_err}")
                await client.send_message("me", startup_text)
                logger.info("Startup notification sent to 'me'")
    except Exception as notify_err:
        logger.warning(f"Failed to send startup notification: {notify_err}")
        
    # Keep both running concurrently in the same event loop
    tasks = [client.run_until_disconnected()]
    if bot_client:
        tasks.append(bot_client.run_until_disconnected())
        
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(start_platform())
    except KeyboardInterrupt:
        logger.info("TeleFlow stopped by user.")
    except Exception as e:
        logger.critical(f"Critical error: {e}", exc_info=True)
        sys.exit(1)
