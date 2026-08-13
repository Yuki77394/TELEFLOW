"""
Auto-Reaction Module
─────────────────────
After the UserBot copies/forwards a message into a target channel, this
module makes the UserBot react to its OWN freshly-sent message.

Rules:
- Reactions are only picked from the ones actually enabled/available in
  that target channel (never guessed/hardcoded blindly).
- If the UserBot's logged-in account has Telegram Premium -> up to 3
  reactions on the message (Telegram's multi-reaction limit for Premium).
- If the account is a regular (non-Premium) account -> exactly 1 reaction
  (Telegram only allows a single reaction per message for non-Premium
  accounts).
- Fully best-effort: any failure here is logged and swallowed so it can
  never break the forwarding pipeline.
"""

import random

from telethon.tl.functions.messages import SendReactionRequest, GetFullChatRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.types import (
    Channel, Chat,
    ReactionEmoji,
    ChatReactionsAll, ChatReactionsSome, ChatReactionsNone,
)

from core.logger import logger

# Number of reactions to place when the account has Telegram Premium.
PREMIUM_REACTION_COUNT = 3
# Number of reactions to place when the account does NOT have Premium.
FREE_REACTION_COUNT = 1

# Fallback pool used only when a chat reports "ChatReactionsAll" (i.e. every
# standard emoji is allowed) -- Telegram doesn't hand us the literal emoji
# list in that case, so we pick from a safe pool of common reactions.
_DEFAULT_POOL = ["👍", "❤️", "🔥", "🎉", "👏", "😁", "💯", "🙏"]

# target_id -> list[str] of emoji reactions actually available in that chat.
_reaction_cache = {}

# Cached premium status of the logged-in UserBot account (None = not checked yet).
_is_premium = None


async def _get_is_premium(client):
    global _is_premium
    if _is_premium is None:
        me = await client.get_me()
        _is_premium = bool(getattr(me, "premium", False))
        logger.info(f"[AutoReact] UserBot premium status detected: {_is_premium}")
    return _is_premium


async def _get_available_reactions(client, target_id):
    """Return the list of emoji reactions enabled in target_id.
    Empty list => reactions are turned off in that chat."""
    if target_id in _reaction_cache:
        return _reaction_cache[target_id]

    reactions = []
    try:
        entity = await client.get_entity(target_id)
        full = None
        if isinstance(entity, Channel):
            full = (await client(GetFullChannelRequest(entity))).full_chat
        elif isinstance(entity, Chat):
            full = (await client(GetFullChatRequest(entity.id))).full_chat

        available = getattr(full, "available_reactions", None) if full else None

        if isinstance(available, ChatReactionsAll):
            reactions = _DEFAULT_POOL[:]
        elif isinstance(available, ChatReactionsSome):
            reactions = [
                r.emoticon for r in available.reactions
                if isinstance(r, ReactionEmoji)
            ]
        elif isinstance(available, ChatReactionsNone) or available is None:
            reactions = []
    except Exception as e:
        logger.warning(f"[AutoReact] Could not read available reactions for {target_id}: {e}")
        reactions = _DEFAULT_POOL[:]  # best-effort fallback so the feature still works

    _reaction_cache[target_id] = reactions
    return reactions


def clear_reaction_cache(target_id=None):
    """Call this if a channel's reaction settings change while the bot is
    running (e.g. admin disables reactions), so it's picked up fresh next time.
    Not required for normal operation -- cache also just expires per-run."""
    if target_id is None:
        _reaction_cache.clear()
    else:
        _reaction_cache.pop(target_id, None)


async def react_to_sent_message(client, target_id, message_id):
    """React to the UserBot's own message (message_id) inside target_id.

    - 3 reactions if the account has Telegram Premium.
    - 1 reaction otherwise.
    - Only ever uses reactions that are actually enabled in that chat.
    - Silently does nothing if reactions are disabled in the chat, or if
      anything goes wrong (never raises up into the caller).
    """
    try:
        available = await _get_available_reactions(client, target_id)
        if not available:
            logger.debug(f"[AutoReact] Reactions disabled/unavailable in {target_id}, skipping.")
            return

        premium = await _get_is_premium(client)
        count = PREMIUM_REACTION_COUNT if premium else FREE_REACTION_COUNT
        count = min(count, len(available))

        chosen = random.sample(available, count)

        await client(SendReactionRequest(
            peer=target_id,
            msg_id=message_id,
            reaction=[ReactionEmoji(emoticon=e) for e in chosen],
        ))
        logger.info(
            f"[AutoReact] Reacted to {target_id}:{message_id} with {chosen} "
            f"(premium={premium})"
        )

    except Exception as e:
        # Reaction failures (e.g. FloodWait, REACTIONS_TOO_MANY, permission
        # changes mid-run) must never break message forwarding.
        logger.warning(f"[AutoReact] Failed to react on {target_id}:{message_id}: {e}")
