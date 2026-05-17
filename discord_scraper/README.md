# Discord Scraper

A pipeline for exporting Discord channels with threads and downloading Minecraft schematic files (.litematic, .schematic, etc.).

## Overview

This tool:
1. Exports Discord channels and their threads to JSON using [DiscordChatExporter](https://github.com/Tyrrrz/DiscordChatExporter)
2. Parses the exported JSON to find schematic file URLs in message content
3. Downloads the actual schematic files by fetching signed URLs from Discord's API

## Prerequisites

- **Python 3.10+**
- **.NET SDK 10.0+** (required to build DiscordChatExporter from source)
- **Discord user token** (from a dummy account that has access to the target servers)

## Setup

### 1. Install .NET SDK 10.0

If you don't have .NET SDK installed:

```bash
wget https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh
chmod +x /tmp/dotnet-install.sh
/tmp/dotnet-install.sh --channel 10.0 --install-dir ~/.dotnet
export PATH="$PATH:$HOME/.dotnet"
hash -r
dotnet --info
```

If you previously installed dotnet and now see `dotnet: command not found`, it is almost always a **PATH** issue. Ensure `"$HOME/.dotnet"` is in your PATH (ideally in `~/.bashrc`).

### 2. Build DiscordChatExporter CLI

```bash
cd DiscordChatExporter
dotnet publish DiscordChatExporter.Cli/DiscordChatExporter.Cli.csproj -c Release -o ./cli_output
```

This creates the CLI in `DiscordChatExporter/cli_output/DiscordChatExporter.Cli`

Note: `cli_output/` is a **local build artifact** and is intentionally **gitignored**. If you clean your repo or switch machines, you must rebuild it.

### 3. Set Your Discord Token

```bash
export DISCORD_TOKEN="your-user-token-here"
```

**Important:** Use a dummy Discord account, not your main one. Extract the token from browser dev tools (Local Storage) after logging into Discord.

## Usage

```bash
python3 export_discord.py --help
```

### 1) Configure servers (`config.json`)

```bash
cp config.json config.local.json
# Edit config.local.json to enable servers + select channels/categories
```

### 2) Scrape (raw collection)

```bash
export DISCORD_TOKEN="your-user-token-here"

# Scrape all enabled servers/channels from config
python3 export_discord.py --config config.local.json scrape

# Scrape a specific server/channel (overrides config channels)
python3 export_discord.py --config config.local.json scrape --server <server_id>
python3 export_discord.py --config config.local.json scrape --server <server_id> --channel <channel_id>

# Keep looping forever (useful for long-running jobs)
python3 export_discord.py --config config.local.json scrape --loop

# Quick test limits
python3 export_discord.py --config config.local.json scrape --max-channels 1 --max-messages 10
```

### 3) Clean (post-processing)

Cleaning is intentionally **permissive**: it removes obvious noise (Discord CDN/media URLs, duplicate image links, empty spam), but keeps the full descriptive content so the dataset stays diverse.

```bash
# Clean everything we have raw data for
python3 export_discord.py clean

# Clean just one server
python3 export_discord.py clean --server <server_id>

# Clean messages only (no Minecraft server needed)
python3 export_discord.py clean --messages-only --server <server_id>

# Validate schematics by pasting into Minecraft (requires server/RCON running)
python3 export_discord.py clean --schematics-only --server <server_id>
```

### Recommended Python

Use the repo venv when running these commands:

```bash
./.venv/bin/python discord_scraper/export_discord.py --help
```

## Output Structure

```
discord_scraper/
├── DiscordChatExporter/          # Cloned repo (git submodule)
│   └── cli_output/               # Built CLI
├── raw_data/                     # Raw JSON exports (per channel)
├── data/
│   ├── raw_messages/{server_id}/{channel_id}/messages.jsonl
│   ├── raw_schematics/{server_id}/*
│   ├── clean_messages/{server_id}/{channel_id}/messages.jsonl
│   ├── clean_schematics/{server_id}/*
│   └── metadata/{server_id}/
│       ├── scrape_status.json    # Per-channel scrape progress
│       └── cleaning_status.json  # Cleaning/validation progress
├── export_discord.py             # Main pipeline script
├── config.json                   # Example config (copy and edit)
└── README.md                     # This file
```

## Message JSON Schema

Each line in `messages.jsonl` is a JSON object:

```json
{
  "message_id": "123456789",
  "server_id": "987654321",
  "channel_id": "111222333",
  "channel_name": "thread-name",
  "category": "parent-category",
  "author_id": "444555666",
  "author_name": "Username",
  "timestamp": "2026-03-11T14:30:00Z",
  "content": "Message text...",
  "schematics": [
    {
      "filename": "123_1_myschematic.litematic",
      "type": "litematic",
      "original_url": "https://cdn.discordapp.com/...",
      "status": "success"
    }
  ],
  "images": ["https://cdn.discordapp.com/.../image.png"],
  "reactions": [],
  "reply_to_message_id": null
}
```

## Known Limitations

1. **Channel Access**: Files in channels your account can't access will fail with "Access denied"
2. **Signed URL lookup cost**: If a schematic is linked by CDN URL (not a direct message attachment), resolving the signed URL may require paging message history for that channel.
3. **Deleted Files**: If the original file was deleted from Discord, it will be marked as "deleted"

## Troubleshooting

### "DISCORD_TOKEN environment variable is not set"
Set the token: `export DISCORD_TOKEN='your-token-here'`

### "DiscordChatExporter CLI not found"
Build the CLI first (see Setup step 2).

### "Access denied to channel XXX"
Your account doesn't have permission to access that channel. This is normal for staff-only or private channels.

### "Attachment not found in channel"
The file may be older than 100 messages back, or was deleted. The Discord API only returns recent messages.

## License

This scraper is for personal use with Discord servers you have explicit permission to scrape. Respect Discord's Terms of Service and server rules.

DiscordChatExporter is licensed under MIT (see DiscordChatExporter/License.txt).
