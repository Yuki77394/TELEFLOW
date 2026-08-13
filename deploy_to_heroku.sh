#!/bin/bash
# One-time setup: pushes your bot credentials directly into Heroku Config Vars.
# After this, you never need a local .env file again — Heroku injects these
# as real environment variables at runtime, and os.getenv() picks them up.
#
# Usage:
#   chmod +x deploy_to_heroku.sh
#   ./deploy_to_heroku.sh your-heroku-app-name

set -e

APP_NAME="$1"
if [ -z "$APP_NAME" ]; then
  echo "Usage: ./deploy_to_heroku.sh <heroku-app-name>"
  exit 1
fi

echo "This will prompt you for each credential and set it as a Heroku Config Var."
echo "Nothing is saved to disk locally."
echo ""

read -p "API_ID: " API_ID
read -p "API_HASH: " API_HASH
read -sp "SESSION_STRING (generate fresh via 'python3 login.py' — never reuse an old one): " SESSION_STRING
echo ""
read -p "OWNER_ID (your numeric Telegram user ID): " OWNER_ID
read -p "BOT_TOKEN: " BOT_TOKEN
read -p "MONGO_URI (leave blank to skip / use SQLite): " MONGO_URI

heroku config:set \
  API_ID="$API_ID" \
  API_HASH="$API_HASH" \
  SESSION_STRING="$SESSION_STRING" \
  OWNER_ID="$OWNER_ID" \
  BOT_TOKEN="$BOT_TOKEN" \
  -a "$APP_NAME"

if [ -n "$MONGO_URI" ]; then
  heroku config:set MONGO_URI="$MONGO_URI" -a "$APP_NAME"
fi

echo ""
echo "Done. Verify with: heroku config -a $APP_NAME"
echo "Deploy with: git push heroku main   (or heroku container:push worker && heroku container:release worker)"
