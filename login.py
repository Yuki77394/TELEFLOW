import asyncio
import os
from dotenv import load_dotenv

# Try to load existing .env configuration
load_dotenv()

async def main():
    print("==================================================")
    print("          TeleFlow String Session Generator       ")
    print("==================================================")
    
    # Fetch credentials or ask user
    api_id_env = os.getenv("API_ID")
    if not api_id_env:
        api_id = input("Enter your Telegram API_ID: ").strip()
    else:
        api_id = api_id_env
        print(f"Using API_ID from env: {api_id}")
        
    api_hash_env = os.getenv("API_HASH")
    if not api_hash_env:
        api_hash = input("Enter your Telegram API_HASH: ").strip()
    else:
        api_hash = api_hash_env
        print(f"Using API_HASH from env: {api_hash}")

    if not api_id or not api_hash:
        print("Error: Both API_ID and API_HASH are required!")
        return

    # Import Telethon after confirming we have basic inputs
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    # Initialize client with empty StringSession to perform login and generate session string
    client = TelegramClient(StringSession(''), int(api_id), api_hash)
    
    print("\nStarting login flow...")
    await client.start()
    
    session_str = client.session.save()
    
    print("\n================ LOGIN SUCCESSFUL ================")
    print("Add this line to your .env file:")
    print(f"SESSION_STRING={session_str}")
    print("==================================================\n")
    
    await client.disconnect()

if __name__ == "__main__":
    # Ensure correct asyncio event loop on Windows
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
