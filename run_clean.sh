#!/bin/bash
cd /home/bergerj/main/personal/minecraft-dev/mira
export PYTHONUNBUFFERED=1
exec /home/bergerj/main/personal/minecraft-dev/mira/.venv/bin/python -u discord_scraper/export_discord.py clean --schematics-only >> /tmp/mira_clean3.log 2>&1
