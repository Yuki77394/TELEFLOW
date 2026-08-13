# conversation.py — thin compatibility shim
# All wizard logic has been consolidated into assistant/regex_conversation.py
# This file is kept so any code that does:
#   from assistant.conversation import join_chat_start
# continues to work without modification.
#
# NOTE: 'register' is intentionally NOT exported here.
# regex_conversation.py is loaded directly by the plugin loader — importing
# register here would cause double-registration and step-skip bugs.

from assistant.regex_conversation import join_chat_start, conversations  # noqa: F401

