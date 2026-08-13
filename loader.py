import importlib
import os
import glob
from core.logger import logger

def load_plugins(client, bot_client=None):
    """Dynamically imports and registers modules from commands/ (on bot_client if available) and modules/ (on client)."""
    root_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Load commands (listen on Assistant Bot if configured, otherwise fallback to UserBot)
    commands_client = bot_client if bot_client else client
    commands_pattern = os.path.join(root_dir, "commands", "*.py")
    for file_path in glob.glob(commands_pattern):
        module_name = os.path.basename(file_path)[:-3]
        if module_name == "__init__":
            continue
        try:
            module = importlib.import_module(f"commands.{module_name}")
            if hasattr(module, "register"):
                module.register(commands_client)
                logger.info(f"Loaded command module: commands.{module_name} on {'Assistant Bot' if bot_client else 'UserBot'}")
        except Exception as e:
            logger.error(f"Error loading command commands.{module_name}: {e}", exc_info=True)
            
    # Load feature modules (listen on UserBot always for message events)
    modules_pattern = os.path.join(root_dir, "modules", "*.py")
    for file_path in glob.glob(modules_pattern):
        module_name = os.path.basename(file_path)[:-3]
        if module_name == "__init__":
            continue
        try:
            module = importlib.import_module(f"modules.{module_name}")
            if hasattr(module, "register"):
                module.register(client)
                logger.info(f"Loaded feature module: modules.{module_name} on UserBot")
        except Exception as e:
            logger.error(f"Error loading feature modules.{module_name}: {e}", exc_info=True)

def load_assistant_plugins(bot_client):
    """Dynamically imports and registers modules from assistant/."""
    if not bot_client:
        return
        
    root_dir = os.path.dirname(os.path.abspath(__file__))
    assistant_pattern = os.path.join(root_dir, "assistant", "*.py")
    
    logger.info("Loading Assistant Bot plugins...")
    for file_path in glob.glob(assistant_pattern):
        module_name = os.path.basename(file_path)[:-3]
        if module_name == "__init__":
            continue
        try:
            module = importlib.import_module(f"assistant.{module_name}")
            if hasattr(module, "register"):
                module.register(bot_client)
                logger.info(f"Loaded Assistant module: assistant.{module_name}")
        except Exception as e:
            logger.error(f"Error loading Assistant module assistant.{module_name}: {e}", exc_info=True)
