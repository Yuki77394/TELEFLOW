import re
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from core.logger import logger

async def respond(event, text):
    """Edits the message if outgoing, otherwise replies if incoming."""
    if event.out:
        try:
            return await event.edit(text)
        except Exception:
            return await event.respond(text)
    else:
        return await event.reply(text)

async def edit_or_reply(msg, text, event=None):
    """Tries to edit the message. If blocked, deletes it and sends a new message."""
    try:
        return await msg.edit(text)
    except Exception:
        try:
            await msg.delete()
        except Exception:
            pass
        
        # Send a new message instead of editing
        if event:
            return await event.respond(text)
        else:
            return await msg.respond(text)

async def perform_join(client, invite_input):
    """Core logic to join a public username or private invite link."""
    invite_input = invite_input.strip()
    
    # Check for private invite link (extract invite hash)
    match = re.search(r'(?:\+|joinchat/|join/)([^/?#\s]+)', invite_input)
    if match:
        hash_val = match.group(1)
        logger.info(f"UserBot joining private chat via hash: {hash_val}")
        await client(ImportChatInviteRequest(hash_val))
        return "✅ **Successfully joined private chat!**"
    else:
        # Public username or public link
        username = invite_input
        if '/' in invite_input:
            username = invite_input.split('/')[-1]
        if not username.startswith('@') and not invite_input.startswith('t.me'):
            username = '@' + username
            
        logger.info(f"UserBot joining public chat: {username}")
        await client(JoinChannelRequest(username))
        return f"✅ **Successfully joined public chat:** `{username}`"
