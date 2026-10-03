from copy import deepcopy
import json

from telethon import events
from telethon.tl.types import (
    MessageMediaWebPage,
    MessageEntityCustomEmoji
)
from telethon.errors import (
    ChatAdminRequiredError, ChatWriteForbiddenError, FloodWaitError,
    UserBannedInChannelError, ChannelPrivateError, UserNotParticipantError
)
from database.database import (
    get_targets_for_source, get_replacements, get_header_footer,
    save_message_map, get_mapped_message,
    is_regex_enabled, get_regex_rules,
    get_media_filters
)
from core.logger import logger
from telethon.extensions import markdown
from telethon.tl import types as tl_types
import asyncio
import re

import config
from modules.auto_react import react_to_sent_message

# ─── Media Group Buffer ───────────────────────────────────────────────────────
# grouped_id -> {"messages": [...], "timer_task": asyncio.Task, "chat_id": int}
_media_group_buffer = {}
# Messages created by this forwarding pipeline are remembered temporarily so
# reverse source/target configurations cannot create an infinite A -> B -> A loop.
# Key: (chat_id, message_id). Value: monotonic expiry timestamp.
_loop_guard = {}
_LOOP_GUARD_TTL = 600  # 10 minutes; loops happen immediately, so no permanent storage is needed.
_loop_guard_lock = asyncio.Lock()


async def _mark_forwarded_message(chat_id, message_id):
    """Remember a message produced by this module for a short TTL."""
    if not chat_id or not message_id:
        return
    now = asyncio.get_running_loop().time()
    async with _loop_guard_lock:
        # Opportunistic cleanup keeps the in-memory map bounded.
        expired = [k for k, expiry in _loop_guard.items() if expiry <= now]
        for k in expired:
            _loop_guard.pop(k, None)
        _loop_guard[(int(chat_id), int(message_id))] = now + _LOOP_GUARD_TTL


async def _is_loop_message(chat_id, message_id):
    """Return True when this exact incoming message was recently created by us."""
    if not chat_id or not message_id:
        return False
    now = asyncio.get_running_loop().time()
    key = (int(chat_id), int(message_id))
    async with _loop_guard_lock:
        expiry = _loop_guard.get(key)
        if expiry is None:
            return False
        if expiry <= now:
            _loop_guard.pop(key, None)
            return False
        return True


# Seconds to wait for remaining group messages before processing
_GROUP_WAIT_SECONDS = 1.5


def utf16_len(s):
    """
    Telegram entity offsets/lengths are counted in UTF-16 code units,
    NOT Python codepoints. Emojis (incl. premium custom emoji placeholders)
    and other chars outside the BMP take 2 UTF-16 units but only 1 Python
    char. Using plain len() here was the main reason premium emoji
    entities were getting misaligned and silently dropped by Telegram.
    """
    if not s:
        return 0
    return len(s.encode("utf-16-le")) // 2


async def copy_message(client, target_id, message, text, reply_to_id=None, entities=None,
                        media_allowed=1, text_allowed=1, reply_markup_override=None):
    """Sends a copy of the message (re-sending media or text) to target_id.
    Preserves formatting_entities (premium emoji, bold, italic, spoiler, etc.)
    when provided and text is unmodified.

    media_allowed: 1 = send the file, 0 = send only text/caption (or skip if text_allowed=0 too)
    text_allowed:  1 = include caption/text, 0 = send media with empty caption
    reply_markup_override: None = keep original, [] = strip all buttons

    parse_mode logic:
    - entities=None  -> text was transformed, use 'markdown' so **bold**/__italic__ render
    - entities=[...] -> original premium entities, no parse_mode (avoid double-parse)
    """
    is_real_media = message.media and not isinstance(message.media, MessageMediaWebPage)

    logger.info(f"[COPY] text={repr(text)}")
    logger.info(f"[COPY] entities={entities}")

    send_media = is_real_media and bool(media_allowed)
    caption    = text if text_allowed else ""
    cap_ents   = entities if text_allowed else None

    # parse_mode selection:
    # - cap_ents present  -> original/shifted entities attached, disable parse_mode
    #                        so Telethon doesn't double-parse and corrupt them.
    # - cap_ents is None  -> body changed (replace/regex), no safe entities.
    #                        Use 'md' so **bold** / __italic__ in header/footer
    #                        or in transformed text renders correctly.
    # NOTE: Telethon uses 'md' not 'markdown' as the parse_mode keyword.
    parse_mode = None if cap_ents else 'md'

    if reply_markup_override is not None:
        markup = None
    else:
        markup = message.reply_markup if message.reply_markup else None

    if send_media:
        supports_cap = True
        if message.sticker or message.poll or message.contact or message.geo or message.venue:
            supports_cap = False

        final_caption  = caption if supports_cap else None
        final_cap_ents = cap_ents if supports_cap else None
        final_parse    = parse_mode if supports_cap else None

        logger.info(f"[SEND_FILE] caption={repr(final_caption)} parse_mode={final_parse}")
        logger.info(f"[SEND_FILE] formatting_entities={final_cap_ents}")

        return await client.send_file(
            target_id,
            file=message.media,
            caption=final_caption,
            formatting_entities=final_cap_ents,
            parse_mode=final_parse,
            reply_to=reply_to_id,
            buttons=markup
        )
    else:
        logger.info(f"[SEND_MESSAGE] caption={repr(caption)} parse_mode={parse_mode}")
        logger.info(f"[SEND_MESSAGE] formatting_entities={cap_ents}")
        return await client.send_message(
            target_id,
            message=caption,
            formatting_entities=cap_ents,
            parse_mode=parse_mode,
            reply_to=reply_to_id,
            buttons=markup
        )


def format_inline_buttons(reply_markup):
    """Formats inline keyboard buttons to text markdown links/labels."""
    if not reply_markup:
        return ""
    from telethon.tl.types import ReplyInlineMarkup, KeyboardButtonUrl
    if not isinstance(reply_markup, ReplyInlineMarkup):
        return ""

    lines = []
    for row in reply_markup.rows:
        row_str = []
        for button in row.buttons:
            if isinstance(button, KeyboardButtonUrl):
                row_str.append(f"[{button.text}]({button.url})")
            else:
                row_str.append(f"[{button.text}]")
        if row_str:
            lines.append(" | ".join(row_str))

    if lines:
        return "\n\n" + "\n".join(lines)
    return ""


def shift_entities(entities, shift):
    """Shift entity offsets by `shift` UTF-16 code units (e.g. when a
    header/prefix is prepended before the original text)."""
    if not entities:
        return None
    new_entities = []
    for entity in entities:
        e = deepcopy(entity)
        e.offset += shift
        new_entities.append(e)
    return new_entities


def utf16_offset(s, char_offset):
    """Convert a character offset (Python string index) to UTF-16 code unit offset."""
    return utf16_len(s[:char_offset])


# ─── Entity Serialization ─────────────────────────────────────────────────────

def serialize_entities(entities):
    """
    Serialize a list of Telethon MessageEntity objects to a JSON string.
    Used to persist formatting/custom emoji entities in the database.
    Returns None if entities is empty or None.
    """
    if not entities:
        return None
    result = []
    for e in entities:
        d = {"_type": type(e).__name__, "offset": e.offset, "length": e.length}
        # Preserve type-specific attributes
        for attr in ("document_id", "url", "user_id", "language", "collapsed"):
            val = getattr(e, attr, None)
            if val is not None:
                d[attr] = val
        result.append(d)
    try:
        return json.dumps(result)
    except Exception:
        return None


def deserialize_entities(entities_json):
    """
    Deserialize a JSON string back into Telethon MessageEntity objects.
    Returns [] if entities_json is None or invalid.
    """
    if not entities_json:
        return []
    try:
        data = json.loads(entities_json)
    except Exception:
        return []

    # Map type names to Telethon entity classes
    _TYPE_MAP = {
        "MessageEntityBold": tl_types.MessageEntityBold,
        "MessageEntityItalic": tl_types.MessageEntityItalic,
        "MessageEntityCode": tl_types.MessageEntityCode,
        "MessageEntityPre": tl_types.MessageEntityPre,
        "MessageEntityUnderline": tl_types.MessageEntityUnderline,
        "MessageEntityStrike": tl_types.MessageEntityStrike,
        "MessageEntitySpoiler": tl_types.MessageEntitySpoiler,
        "MessageEntityTextUrl": tl_types.MessageEntityTextUrl,
        "MessageEntityMentionName": tl_types.MessageEntityMentionName,
        "MessageEntityCustomEmoji": tl_types.MessageEntityCustomEmoji,
        "MessageEntityHashtag": tl_types.MessageEntityHashtag,
        "MessageEntityCashtag": tl_types.MessageEntityCashtag,
        "MessageEntityPhone": tl_types.MessageEntityPhone,
        "MessageEntityMention": tl_types.MessageEntityMention,
        "MessageEntityEmail": tl_types.MessageEntityEmail,
        "MessageEntityUrl": tl_types.MessageEntityUrl,
        "MessageEntityBotCommand": tl_types.MessageEntityBotCommand,
        "MessageEntityBlockquote": tl_types.MessageEntityBlockquote,
    }
    entities = []
    for d in data:
        cls = _TYPE_MAP.get(d.get("_type"))
        if cls is None:
            continue
        try:
            kwargs = {"offset": d["offset"], "length": d["length"]}
            if "document_id" in d:
                kwargs["document_id"] = d["document_id"]
            if "url" in d:
                kwargs["url"] = d["url"]
            if "user_id" in d:
                kwargs["user_id"] = d["user_id"]
            if "language" in d:
                kwargs["language"] = d["language"]
            if "collapsed" in d:
                kwargs["collapsed"] = d["collapsed"]
            entities.append(cls(**kwargs))
        except Exception:
            continue
    return entities


def preprocess_newlines(text, entities):
    """
    Replace literal backslash-n (\\n) with an actual newline character.
    Adjusts entity offsets/lengths correctly using UTF-16 arithmetic.
    Telegram strips leading/trailing newlines from messages, so users
    can use \\n in headers/footers to insert newlines programmatically.
    """
    find = "\\n"
    actual = "\n"
    return apply_replace_with_entities(text, entities, find, actual)


def apply_replace_with_entities(text, entities, find, replace, replace_entities=None):
    """
    Perform a plain text replacement find -> replace in text,
    dynamically updating entity offsets/lengths (using correct UTF-16
    arithmetic). Optionally inserts replace_entities at the replacement site.
    """
    if not find or find not in text:
        return text, list(entities) if entities else []
    if entities is None:
        entities = []

    new_entities = [deepcopy(e) for e in entities]
    curr_char = 0  # Python character index
    while True:
        pos_char = text.find(find, curr_char)
        if pos_char == -1:
            break

        # Convert Python char positions to UTF-16 code unit positions
        pos_utf16 = utf16_offset(text, pos_char)
        find_utf16_len = utf16_len(find)
        replace_utf16_len = utf16_len(replace)
        diff = replace_utf16_len - find_utf16_len

        # Adjust existing entities
        for e in new_entities:
            entity_end = e.offset + e.length
            if e.offset >= pos_utf16 + find_utf16_len:
                # Entity starts after the replaced range → shift right/left
                e.offset += diff
            elif e.offset <= pos_utf16 and entity_end >= pos_utf16 + find_utf16_len:
                # Entity spans the entire replaced range → stretch/shrink
                e.length += diff
            elif e.offset > pos_utf16 and entity_end < pos_utf16 + find_utf16_len:
                # Entity is fully inside the replaced range → clamp to new range
                e.offset = pos_utf16
                e.length = min(e.length, replace_utf16_len)

        # Insert replace_entities at the replacement position (shifted)
        if replace_entities:
            for re_ent in replace_entities:
                cloned = deepcopy(re_ent)
                cloned.offset += pos_utf16
                new_entities.append(cloned)

        # Apply the string replacement (Python char-level)
        text = text[:pos_char] + replace + text[pos_char + len(find):]
        curr_char = pos_char + len(replace)

    return text, new_entities


def apply_regex_with_entities(text, entities, pattern, replacement):
    """
    Apply a regular expression replacement to text,
    and dynamically update the offsets and lengths of entities.
    Processes matches right-to-left so that already-processed (rightward)
    entities are NOT re-shifted when a leftward match is applied.
    """
    if not entities:
        try:
            return re.sub(pattern, replacement, text), []
        except Exception:
            return text, []

    try:
        compiled = re.compile(pattern)
    except Exception:
        return text, list(entities)

    new_entities = [deepcopy(e) for e in entities]
    matches = list(compiled.finditer(text))
    if not matches:
        return text, list(entities)

    # Process from right to left so earlier (left) index positions are not
    # invalidated when we replace later (right) matches.
    # We track a "right boundary" in UTF-16 units: entities that start at or
    # beyond this boundary have already been shifted for rightward matches and
    # must NOT be shifted again for the current (leftward) match.
    right_boundary_utf16 = None  # set after each processed match

    for match in reversed(matches):
        pos_chars = match.start()
        end_chars = match.end()

        pos_utf16 = utf16_offset(text, pos_chars)
        find_utf16_len = utf16_offset(text, end_chars) - pos_utf16

        try:
            replaced_str = match.expand(replacement)
        except Exception:
            replaced_str = replacement

        replace_utf16_len = utf16_len(replaced_str)
        diff = replace_utf16_len - find_utf16_len

        if diff != 0:
            for e in new_entities:
                entity_end = e.offset + e.length
                # Skip entities that are in the already-processed right region
                if right_boundary_utf16 is not None and e.offset >= right_boundary_utf16:
                    continue
                if e.offset >= pos_utf16 + find_utf16_len:
                    e.offset += diff
                elif e.offset <= pos_utf16 and entity_end >= pos_utf16 + find_utf16_len:
                    e.length += diff
                elif e.offset > pos_utf16 and entity_end < pos_utf16 + find_utf16_len:
                    e.offset = min(e.offset, pos_utf16 + replace_utf16_len)

        text = text[:pos_chars] + replaced_str + text[end_chars:]
        # After replacement, the region at pos_utf16 onward (with new length) is
        # now the rightmost processed boundary.
        right_boundary_utf16 = pos_utf16 + replace_utf16_len

    return text, new_entities


def apply_text_transformations(original_text, original_entities, replacements, regex_active,
                                is_regex_enabled_flag, regex_rules, header, footer,
                                buttons_text, chat_id, msg_id,
                                header_entities_json=None, footer_entities_json=None):
    """
    Apply replacements, regex, header, footer, buttons to text.
    Returns (final_text, entities_to_send)

    Smart entity strategy:
    - Preserve all original entities (bold, italic, links, premium custom emoji).
    - Dynamically adjust offsets/lengths as plain replacements and regex are applied.
    - Header/footer can carry their own premium entities (deserialized from JSON).
    - Literal \\n in header/footer/replacement text is converted to actual newlines.
    """
    transformed_text = original_text
    transformed_entities = list(original_entities) if original_entities else []

    # Priority 1: plain str.replace (with optional per-replacement entities)
    for rule in replacements:
        if len(rule) == 3:
            find, replace, rep_ent_json = rule
        else:
            find, replace = rule[0], rule[1]
            rep_ent_json = None
        # Convert literal \n in the replacement text to actual newlines
        replace = replace.replace("\\n", "\n")
        rep_ents = deserialize_entities(rep_ent_json)
        transformed_text, transformed_entities = apply_replace_with_entities(
            transformed_text, transformed_entities, find, replace,
            replace_entities=rep_ents if rep_ents else None
        )

    # Priority 2: regex rules (only if enabled)
    if is_regex_enabled_flag:
        for rule_name, pattern, replacement in regex_rules:
            transformed_text, transformed_entities = apply_regex_with_entities(
                transformed_text, transformed_entities, pattern, replacement
            )

    # ── Build prefix (header) ────────────────────────────────────────────────
    if header:
        h_entities = deserialize_entities(header_entities_json) if header_entities_json else []
        if h_entities:
            # Premium entities stored in DB: use apply_replace_with_entities to
            # convert literal \n → real newlines while adjusting entity offsets
            # correctly (a 2-char "\n" sequence becomes a 1-char "\n").
            header_clean, h_entities = apply_replace_with_entities(
                header, h_entities, "\\n", "\n"
            )
            prefix_text = header_clean + "\n\n"
            prefix_entities_list = h_entities
        else:
            # No premium entities → parse markdown to get clean text + basic entities
            header_text_raw = header.replace("\\n", "\n")
            prefix_text, prefix_entities_list = markdown.parse(header_text_raw + "\n\n")
    else:
        prefix_text = ""
        prefix_entities_list = []

    # ── Build suffix (buttons + footer) ─────────────────────────────────────
    suffix_parts = []
    suffix_entities_list = []
    if buttons_text:
        # Buttons are always markdown text (no premium emojis expected)
        clean_btn, btn_ents = markdown.parse(buttons_text)
        suffix_parts.append(clean_btn)
        if btn_ents:
            suffix_entities_list.extend(btn_ents)

    if footer:
        f_entities = deserialize_entities(footer_entities_json) if footer_entities_json else []
        footer_sep = "\n\n" if suffix_parts else ""
        # Offset of footer entities inside the suffix block
        btn_utf16 = utf16_len("".join(suffix_parts) + footer_sep)
        if f_entities:
            # Premium entities: replace \n with proper offset adjustment
            footer_clean, f_entities = apply_replace_with_entities(
                footer, f_entities, "\\n", "\n"
            )
            for fe in f_entities:
                cloned = deepcopy(fe)
                cloned.offset += btn_utf16
                suffix_entities_list.append(cloned)
        else:
            footer_text_raw = footer.replace("\\n", "\n")
            footer_clean, f_ents_parsed = markdown.parse(footer_text_raw)
            for fe in (f_ents_parsed or []):
                cloned = deepcopy(fe)
                cloned.offset += btn_utf16
                suffix_entities_list.append(cloned)
        suffix_parts.append(footer_sep + footer_clean)

    suffix_text = "".join(suffix_parts)

    final_clean_text = prefix_text + transformed_text + suffix_text

    # Merge all three entity lists
    merged_entities = []

    # 1. Prefix entities (offsets are already correct — relative to start of prefix)
    if prefix_entities_list:
        merged_entities.extend(prefix_entities_list)

    # 2. Transformed original entities (shift by UTF-16 length of prefix)
    if transformed_entities:
        shift = utf16_len(prefix_text)
        shifted_orig = shift_entities(transformed_entities, shift)
        if shifted_orig:
            merged_entities.extend(shifted_orig)

    # 3. Suffix entities (shift by UTF-16 length of prefix + transformed body)
    if suffix_entities_list:
        shift = utf16_len(prefix_text) + utf16_len(transformed_text)
        for se in suffix_entities_list:
            cloned = deepcopy(se)
            cloned.offset += shift
            merged_entities.append(cloned)

    return final_clean_text, (merged_entities or None)


async def process_media_group(client, grouped_id, chat_id):
    """
    Called after _GROUP_WAIT_SECONDS to process all buffered messages for a
    given grouped_id. Sends them as a media album (same-to-same) to each
    target, then applies replace/regex/header/footer to the caption.
    """
    global _media_group_buffer

    entry = _media_group_buffer.pop(grouped_id, None)
    if not entry:
        return

    messages = sorted(entry["messages"], key=lambda m: m.id)
    if not messages:
        return

    logger.info(f"Processing media group {grouped_id} with {len(messages)} messages from {chat_id}")

    targets = get_targets_for_source(chat_id)
    if not targets:
        return

    for target_id in targets:
        try:
            replacements = get_replacements(chat_id, target_id)
            header, footer, header_ents_json, footer_ents_json = get_header_footer(chat_id, target_id)
            regex_active = is_regex_enabled(chat_id) and bool(get_regex_rules(chat_id))
            regex_rules = get_regex_rules(chat_id) if is_regex_enabled(chat_id) else []

            filters = get_media_filters(chat_id, target_id)
            photo_allowed = filters.get("photo", 1)
            text_allowed  = filters.get("text", 1)
            btn_allowed   = filters.get("inline_btn", 1)

            if not photo_allowed and not text_allowed:
                logger.debug(f"Media group {grouped_id}: skipped for {target_id} (photo+text both OFF)")
                continue

            # Collect all real media files from the group
            files = []
            for msg in messages:
                is_real = msg.media and not isinstance(msg.media, MessageMediaWebPage)
                if is_real:
                    files.append(msg.media)

            if not files:
                logger.warning(f"Media group {grouped_id}: no real media found, skipping.")
                continue

            # Find caption message (last message with text)
            caption_msg = None
            for msg in reversed(messages):
                if msg.text or msg.message:
                    caption_msg = msg
                    break
            if caption_msg is None:
                caption_msg = messages[-1]

            original_text = caption_msg.message or ""
            original_entities = caption_msg.entities or []

            has_inline_buttons = bool(caption_msg.reply_markup)
            buttons_text = ""
            if has_inline_buttons and btn_allowed:
                buttons_text = format_inline_buttons(caption_msg.reply_markup)

            # Apply transformations (replace, regex, header, footer) on caption
            transformed_text, entities_to_send = apply_text_transformations(
                original_text, original_entities,
                replacements, regex_active,
                is_regex_enabled(chat_id), regex_rules,
                header, footer, buttons_text,
                chat_id, f"group_{grouped_id}",
                header_entities_json=header_ents_json,
                footer_entities_json=footer_ents_json
            )

            # Resolve reply mapping
            reply_to_msg_id = None
            if caption_msg.reply_to:
                source_reply_id = caption_msg.reply_to.reply_to_msg_id
                mapped_reply_id = get_mapped_message(chat_id, source_reply_id, target_id)
                if mapped_reply_id:
                    reply_to_msg_id = mapped_reply_id

            final_caption  = transformed_text if text_allowed else ""
            final_cap_ents = entities_to_send if text_allowed else None

            # Same parse_mode logic as copy_message:
            # entities present -> parse_mode=None (don't double-parse, preserve premium emoji)
            # entities absent  -> parse_mode='md' (so **bold**/__italic__ render correctly)
            final_parse = None if final_cap_ents else 'md'

            if photo_allowed and files:
                # send_file with list of files = Telegram album (same-to-same group)
                sent_msgs = await client.send_file(
                    target_id,
                    file=files,
                    caption=final_caption,
                    formatting_entities=final_cap_ents,
                    parse_mode=final_parse,
                    reply_to=reply_to_msg_id,
                )
                logger.info(f"Sent media group {grouped_id} ({len(files)} files) -> {target_id}")

                if sent_msgs:
                    if not isinstance(sent_msgs, list):
                        sent_msgs = [sent_msgs]
                    for orig_msg, sent_msg in zip(messages, sent_msgs):
                        save_message_map(chat_id, orig_msg.id, target_id, sent_msg.id)
                        await _mark_forwarded_message(target_id, sent_msg.id)

                    if config.AUTO_REACT_ENABLED and sent_msgs:
                        # React once on the album -- reacting on the last item
                        # (the one carrying the caption) is enough; Telegram
                        # ties the reaction to that single message.
                        asyncio.create_task(
                            react_to_sent_message(client, target_id, sent_msgs[-1].id)
                        )
            else:
                # Photo off but text on -- send caption text only
                if text_allowed and final_caption:
                    sent_msg = await client.send_message(
                        target_id,
                        message=final_caption,
                        formatting_entities=final_cap_ents,
                        parse_mode=final_parse,
                        reply_to=reply_to_msg_id,
                    )
                    if sent_msg:
                        save_message_map(chat_id, caption_msg.id, target_id, sent_msg.id)
                        await _mark_forwarded_message(target_id, sent_msg.id)

                        if config.AUTO_REACT_ENABLED:
                            asyncio.create_task(react_to_sent_message(client, target_id, sent_msg.id))

        except FloodWaitError as fw:
            logger.warning(f"FloodWait {fw.seconds}s for target {target_id} (media group). Waiting...")
            await asyncio.sleep(fw.seconds + 2)
        except ChatWriteForbiddenError:
            logger.error(f"Cannot write to target {target_id}. Reason: Chat write forbidden.")
        except ChatAdminRequiredError:
            logger.error(f"Cannot write to target {target_id}. Reason: Admin permissions required.")
        except UserBannedInChannelError:
            logger.error(f"Cannot write to target {target_id}. Reason: UserBot is banned in this channel.")
        except ChannelPrivateError:
            logger.error(f"Cannot write to target {target_id}. Reason: Target channel is private.")
        except UserNotParticipantError:
            logger.error(f"Cannot write to target {target_id}. Reason: UserBot is not a participant.")
        except Exception as e:
            logger.error(
                f"Failed to process media group {grouped_id} from {chat_id} to {target_id}. Error: {e}",
                exc_info=True
            )


def register(client):
    @client.on(events.NewMessage)
    async def forwarder_handler(event):
        # Skip userbot commands (starting with a period or slash).
        # IMPORTANT: a forwarded channel post can legitimately contain text
        # beginning with '/' (e.g. copied bot-style content). Do not discard
        # such messages when Telegram marks them as forwarded.
        if (
            event.text
            and not getattr(event.message, "fwd_from", None)
            and (event.text.strip().startswith('.') or event.text.strip().startswith('/'))
        ):
            return

        chat_id = event.chat_id
        if not chat_id:
            return

        # Loop protection is deliberately checked before any forwarding logic.
        # It does not inspect whether a message was manually posted or forwarded;
        # it only asks whether this exact message ID was recently produced by us.
        if await _is_loop_message(chat_id, event.id):
            logger.info(
                f"[LOOP-GUARD] Ignoring message {event.id} in {chat_id}: "
                f"message was recently generated by this forwarding pipeline."
            )
            return

        targets = get_targets_for_source(chat_id)
        if not targets:
            return

        msg = event.message

        # Telegram can deliver a post that was forwarded into this source
        # channel as a normal NewMessage event, but its media/forward metadata
        # can make a pure re-upload (Copy Mode) fail or behave differently from
        # a manually-created source post. Keep the source chat as A: the fact
        # that the message originally came from B must NOT make B a new source.
        is_forwarded_message = bool(getattr(msg, "fwd_from", None))
        if is_forwarded_message:
            fwd = msg.fwd_from
            logger.info(
                f"[FORWARDED-IN] source={chat_id} msg={event.id} "
                f"fwd_from={type(fwd).__name__} grouped_id={msg.grouped_id}"
            )

        # ─────────────────────────────────────────────────────────────────────
        # MEDIA GROUP HANDLING
        # If this message belongs to a media group (album), buffer it and wait
        # for all group messages to arrive before processing them together.
        # ─────────────────────────────────────────────────────────────────────
        if msg.grouped_id:
            grouped_id = msg.grouped_id
            logger.info(f"Msg {event.id} is part of media group {grouped_id} from {chat_id}")

            if grouped_id not in _media_group_buffer:
                _media_group_buffer[grouped_id] = {
                    "messages": [],
                    "timer_task": None,
                    "chat_id": chat_id
                }

            _media_group_buffer[grouped_id]["messages"].append(msg)

            # Cancel existing timer (more messages may still be coming)
            existing_task = _media_group_buffer[grouped_id].get("timer_task")
            if existing_task and not existing_task.done():
                existing_task.cancel()

            # Set a new timer -- process after a short wait
            async def delayed_process(gid, cid):
                await asyncio.sleep(_GROUP_WAIT_SECONDS)
                await process_media_group(client, gid, cid)

            task = asyncio.create_task(delayed_process(grouped_id, chat_id))
            _media_group_buffer[grouped_id]["timer_task"] = task
            return  # Don't handle group messages individually

        # ─────────────────────────────────────────────────────────────────────
        # SINGLE MESSAGE HANDLING (non-album)
        # ─────────────────────────────────────────────────────────────────────
        logger.info(f"New message {event.id} in source {chat_id}. Processing for targets: {targets}")
        logger.info(event.message.entities)

        for target_id in targets:
            try:
                # ─────────────────────────────────────────────────────────
                # Pre-resolve the target entity. If target_id is a user
                # (not a chat/channel) that the UserBot has never seen
                # before, Telethon has no cached access_hash for them and
                # send_message/send_file will raise ValueError. Resolving
                # here lets us skip cleanly with a clear log instead of
                # crashing mid-send.
                # ─────────────────────────────────────────────────────────
                try:
                    await client.get_entity(target_id)
                except ValueError:
                    logger.error(
                        f"Skipping target {target_id} for msg {event.id}: UserBot has no cached entity for this "
                        f"user/chat. They must first message the UserBot, or the UserBot must already share a "
                        f"common chat / have them in its dialog list, before forwarding can work."
                    )
                    continue

                # Resolve Reply Mapping
                reply_to_msg_id = None
                if event.message.reply_to:
                    source_reply_id = event.message.reply_to.reply_to_msg_id
                    mapped_reply_id = get_mapped_message(chat_id, source_reply_id, target_id)
                    if mapped_reply_id:
                        reply_to_msg_id = mapped_reply_id
                        logger.info(f"Resolved reply mapping: Source Msg {source_reply_id} -> Target Msg {mapped_reply_id}")

                replacements = get_replacements(chat_id, target_id)
                header, footer, header_ents_json, footer_ents_json = get_header_footer(chat_id, target_id)
                regex_active = is_regex_enabled(chat_id) and bool(get_regex_rules(chat_id))
                regex_rules = get_regex_rules(chat_id) if is_regex_enabled(chat_id) else []

                original_text = event.message.message or ""
                original_entities = event.message.entities or []

                is_real_media = msg.media and not isinstance(msg.media, MessageMediaWebPage)

                is_gif = False
                if msg.document:
                    from telethon.tl.types import DocumentAttributeAnimated
                    if any(isinstance(x, DocumentAttributeAnimated) for x in msg.document.attributes):
                        is_gif = True

                if msg.sticker:
                    msg_media_type = "sticker"
                elif is_gif:
                    msg_media_type = "gif"
                elif msg.photo:
                    msg_media_type = "photo"
                elif msg.audio:
                    msg_media_type = "audio"
                elif msg.video:
                    msg_media_type = "video"
                elif is_real_media:
                    msg_media_type = "photo"
                else:
                    msg_media_type = "text"

                has_inline_buttons = bool(msg.reply_markup)

                filters = get_media_filters(chat_id, target_id)
                media_allowed = filters.get(msg_media_type, 1)
                text_allowed  = filters.get("text", 1)
                btn_allowed   = filters.get("inline_btn", 1)

                if is_real_media:
                    if not media_allowed and not text_allowed:
                        logger.debug(f"Msg {event.id}: skipped for {target_id} (media+text both OFF)")
                        continue
                else:
                    if not text_allowed:
                        logger.debug(f"Msg {event.id}: skipped for {target_id} (text OFF)")
                        continue

                buttons_text = ""
                if has_inline_buttons and btn_allowed:
                    buttons_text = format_inline_buttons(msg.reply_markup)

                transformed_text, entities_to_send = apply_text_transformations(
                    original_text, original_entities,
                    replacements, regex_active,
                    is_regex_enabled(chat_id), regex_rules,
                    header, footer, buttons_text,
                    chat_id, event.id,
                    header_entities_json=header_ents_json,
                    footer_entities_json=footer_ents_json
                )

                reply_markup_override = None
                if has_inline_buttons and not btn_allowed:
                    reply_markup_override = []

                sent_msg = None

                # For a message that Telegram itself marks as forwarded into
                # source A, prefer Telegram's native forward operation when
                # there are no text/media transformations. This avoids trying
                # to download/re-upload media that may be restricted, expired,
                # or otherwise non-copyable. If native forwarding is rejected,
                # fall back to the existing Copy Mode so current behaviour is
                # preserved wherever possible.
                can_native_forward = (
                    is_forwarded_message
                    and not replacements
                    and not header
                    and not footer
                    and not regex_active
                    and reply_to_msg_id is None
                    and text_allowed
                    and media_allowed
                    and not (has_inline_buttons and not btn_allowed)
                )

                if can_native_forward:
                    try:
                        forwarded_result = await client.forward_messages(
                            target_id, event.message, from_peer=chat_id
                        )
                        if isinstance(forwarded_result, list):
                            sent_msg = forwarded_result[0] if forwarded_result else None
                        else:
                            sent_msg = forwarded_result
                        logger.info(
                            f"Forwarded nested-forward msg {event.id}: "
                            f"{chat_id} -> {target_id} (Native Forward Mode)"
                        )
                    except Exception as native_forward_err:
                        logger.warning(
                            f"Native forward failed for nested-forward msg {event.id} "
                            f"to {target_id}; falling back to Copy Mode: {native_forward_err}"
                        )

                if sent_msg is None:
                    sent_msg = await copy_message(
                        client, target_id, event.message, transformed_text,
                        reply_to_id=reply_to_msg_id, entities=entities_to_send,
                        media_allowed=media_allowed, text_allowed=text_allowed,
                        reply_markup_override=reply_markup_override
                    )
                    logger.info(f"Copied msg {event.id}: {chat_id} -> {target_id} (Copy Mode)")

                if sent_msg:
                    save_message_map(chat_id, event.id, target_id, sent_msg.id)
                    await _mark_forwarded_message(target_id, sent_msg.id)
                    logger.debug(f"Saved message mapping: Source {chat_id}:{event.id} -> Target {target_id}:{sent_msg.id}")

                    if config.AUTO_REACT_ENABLED:
                        # Fire-and-forget: never let reacting slow down or break forwarding
                        asyncio.create_task(react_to_sent_message(client, target_id, sent_msg.id))

            except FloodWaitError as fw:
                logger.warning(f"FloodWait {fw.seconds}s for target {target_id}. Waiting...")
                await asyncio.sleep(fw.seconds + 2)
                try:
                    sent_msg = await copy_message(
                        client, target_id, event.message, transformed_text,
                        reply_to_id=reply_to_msg_id, entities=entities_to_send,
                        media_allowed=media_allowed, text_allowed=text_allowed,
                        reply_markup_override=reply_markup_override
                    )
                    if sent_msg:
                        save_message_map(chat_id, event.id, target_id, sent_msg.id)
                        await _mark_forwarded_message(target_id, sent_msg.id)
                        logger.info(f"Copied msg {event.id} after FloodWait: {chat_id} -> {target_id}")

                        if config.AUTO_REACT_ENABLED:
                            asyncio.create_task(react_to_sent_message(client, target_id, sent_msg.id))
                except Exception as retry_err:
                    logger.error(f"Retry failed after FloodWait: {retry_err}")

            except ChatWriteForbiddenError:
                logger.error(
                    f"Cannot write to target {target_id}. Reason: Chat write forbidden. "
                    f"The UserBot might be muted/restricted, or the group has slow mode/restricted posting rights."
                )
            except ChatAdminRequiredError:
                logger.error(
                    f"Cannot write to target {target_id}. Reason: Admin permissions required. "
                    f"The UserBot needs administrative privileges to post in this channel/group."
                )
            except UserBannedInChannelError:
                logger.error(
                    f"Cannot write to target {target_id}. Reason: UserBot is banned/restricted in this channel."
                )
            except ChannelPrivateError:
                logger.error(
                    f"Cannot write to target {target_id}. Reason: Target channel is private, or the UserBot has no access."
                )
            except UserNotParticipantError:
                logger.error(
                    f"Cannot write to target {target_id}. Reason: UserBot is not a participant of this group/channel."
                )
            except ValueError as ve:
                if "Could not find the input entity" in str(ve):
                    logger.error(
                        f"Cannot write to target {target_id}. Reason: UserBot has never 'seen' this user/chat "
                        f"(no cached entity). The target user must first send the UserBot a message, or the "
                        f"UserBot must share a common chat with them / already have them in its dialogs, "
                        f"before it can send messages to this ID."
                    )
                else:
                    logger.error(f"ValueError forwarding msg {event.id} to {target_id}: {ve}")
            except Exception as e:
                logger.error(
                    f"Failed to process msg {event.id} from {chat_id} to {target_id}. Unexpected error: {e}",
                    exc_info=True
                )
