from config import OWNER_ID
from core.logger import logger

async def is_owner(event):
    """Checks if the event's sender is the main owner."""
    if getattr(event, 'out', False):
        return True
        
    if OWNER_ID:
        sender_id = event.sender_id
        if not sender_id:
            sender = await event.get_sender()
            if sender:
                sender_id = sender.id
        
        if sender_id == OWNER_ID:
            return True
            
    return False

async def is_authorized(event):
    """Checks if the event's sender is either the owner or a registered super user."""
    # If the message is outgoing (sent by the userbot account itself), allow it
    if getattr(event, 'out', False):
        return True
        
    sender_id = event.sender_id
    if not sender_id:
        sender = await event.get_sender()
        if sender:
            sender_id = sender.id
            
    if not sender_id:
        return False
        
    # Check if owner
    if OWNER_ID and sender_id == OWNER_ID:
        return True
        
    # Check if super user (import dynamically to avoid circular dependency)
    try:
        from database.database import is_super_user
        if is_super_user(sender_id):
            return True
    except Exception as e:
        logger.error(f"Error checking super user auth: {e}")
        
    return False

def owner_only():
    """Decorator to restrict event handlers to the owner only."""
    def decorator(func):
        async def wrapper(event):
            if not await is_owner(event):
                sender_id = event.sender_id or "Unknown"
                logger.warning(f"Unauthorized attempt to execute owner command from user ID {sender_id}")
                return
            return await func(event)
        return wrapper
    return decorator

def authorized_only():
    """Decorator to restrict event handlers to the owner and super users only."""
    def decorator(func):
        async def wrapper(event):
            if not await is_authorized(event):
                sender_id = event.sender_id or "Unknown"
                logger.warning(f"Unauthorized attempt to interact from user ID {sender_id}")
                return
            return await func(event)
        return wrapper
    return decorator
