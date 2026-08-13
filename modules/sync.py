from telethon import events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import UpdateMessageReactions, ReactionEmoji
from telethon.tl.functions.messages import SendReactionRequest
from database.database import (
    get_targets_for_source, get_mapped_message, delete_message_map,
    get_replacements, get_header_footer,
    is_regex_enabled, get_regex_rules,
    get_media_filters
)
from core.logger import logger
from modules.forwarder import format_inline_buttons, apply_text_transformations
import re

def register(client):
    # ─── EDIT SYNC ────────────────────────────────────────────────────────────
    @client.on(events.MessageEdited)
    async def edit_sync_handler(event):
        chat_id = event.chat_id
        if not chat_id:
            return

        logger.info(f"Received edit event: msg_id={event.id}, chat_id={chat_id}")
        targets = get_targets_for_source(chat_id)
        if not targets:
            logger.info(f"No active forward rules found for source chat: {chat_id}")
            return

        for target_id in targets:
            target_msg_id = get_mapped_message(chat_id, event.id, target_id)
            if not target_msg_id:
                logger.info(f"No message mapping found in DB for source message {event.id} (chat {chat_id}) to target {target_id}")
                continue


            try:
                # Get current raw message text/caption and its formatting entities
                original_text = event.message.message or ""
                original_entities = event.message.entities or []

                # Fetch config and transformations details
                replacements = get_replacements(chat_id, target_id)
                regex_active = is_regex_enabled(chat_id) and bool(get_regex_rules(chat_id))
                regex_rules = get_regex_rules(chat_id) if is_regex_enabled(chat_id) else []

                # Fetch media filters for the target link
                filters = get_media_filters(chat_id, target_id)
                btn_allowed = filters.get("inline_btn", 1)

                # Append Inline Buttons as text links if filter is ON
                has_inline_buttons = bool(event.message.reply_markup)
                buttons_text = ""
                if has_inline_buttons and btn_allowed:
                    buttons_text = format_inline_buttons(event.message.reply_markup)

                # Fetch Header & Footer
                header, footer, header_ents_json, footer_ents_json = get_header_footer(chat_id, target_id)

                # Transform text and shift formatting entities correctly
                transformed_text, entities_to_send = apply_text_transformations(
                    original_text, original_entities,
                    replacements, regex_active,
                    is_regex_enabled(chat_id), regex_rules,
                    header, footer, buttons_text,
                    chat_id, event.id,
                    header_entities_json=header_ents_json,
                    footer_entities_json=footer_ents_json
                )

                # Update target message text or caption preserving entities
                # Set parse_mode logic same as copy_message:
                # - entities present -> parse_mode=None (don't double-parse, preserve premium emoji)
                # - entities absent  -> parse_mode='md' (so **bold**/__italic__ render correctly)
                parse_mode = None if entities_to_send else 'md'
                await event.client.edit_message(
                    target_id,
                    target_msg_id,
                    transformed_text,
                    formatting_entities=entities_to_send,
                    parse_mode=parse_mode,
                    buttons=None
                )
                logger.info(f"Edited synced message {event.id} in target {target_id}")
            except MessageNotModifiedError:
                logger.debug(
                    f"Skipped no-op edit sync for message {event.id} "
                    f"(text unchanged, likely triggered by reaction/metadata update)."
                )
            except Exception as e:
                logger.error(
                    f"Failed to sync edit for message {event.id} "
                    f"from {chat_id} to {target_id}: {e}"
                )

    # ─── DELETE SYNC ──────────────────────────────────────────────────────────
    @client.on(events.MessageDeleted)
    async def delete_sync_handler(event):
        chat_id = event.chat_id
        if not chat_id:
            return

        targets = get_targets_for_source(chat_id)
        if not targets:
            return

        for deleted_id in event.deleted_ids:
            for target_id in targets:
                target_msg_id = get_mapped_message(chat_id, deleted_id, target_id)
                if not target_msg_id:
                    continue

                try:
                    await event.client.delete_messages(target_id, target_msg_id)
                    logger.info(f"Deleted synced message {deleted_id} in target {target_id}")
                except Exception as e:
                    logger.error(
                        f"Failed to sync delete for message {deleted_id} "
                        f"from {chat_id} to {target_id}: {e}"
                    )

            # Clean up mapping from DB
            delete_message_map(chat_id, deleted_id)

    # ─── REACTION SYNC (via raw updates) ─────────────────────────────────────
    @client.on(events.Raw(types=UpdateMessageReactions))
    async def reaction_sync_handler(update):
        try:
            peer = update.peer
            chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "user_id", None)
            if chat_id is None:
                return

            # Telethon raw peer IDs need the -100 prefix for channels/supergroups
            if hasattr(peer, "channel_id"):
                chat_id = int(f"-100{peer.channel_id}")
            elif hasattr(peer, "chat_id"):
                chat_id = -peer.chat_id

            source_msg_id = update.msg_id
        except Exception as e:
            logger.error(f"Failed to parse reaction update peer/msg_id: {e}")
            return

        targets = get_targets_for_source(chat_id)
        if not targets:
            return

        # Build reaction list from the update's current aggregated reactions
        reactions = []
        try:
            for rc in (update.reactions.results or []):
                if isinstance(rc.reaction, type(rc.reaction)) and hasattr(rc.reaction, "emoticon"):
                    reactions.append(ReactionEmoji(emoticon=rc.reaction.emoticon))
        except Exception as e:
            logger.error(f"Failed to parse reaction list for msg {source_msg_id}: {e}")
            return

        for target_id in targets:
            target_msg_id = get_mapped_message(chat_id, source_msg_id, target_id)
            if not target_msg_id:
                continue

            try:
                await client(SendReactionRequest(
                    peer=target_id,
                    msg_id=target_msg_id,
                    reaction=reactions if reactions else None
                ))
                logger.info(
                    f"Synced reaction for msg {source_msg_id}: "
                    f"{chat_id} → {target_id}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to sync reaction for msg {source_msg_id} "
                    f"from {chat_id} to {target_id}: {e}"
                )

