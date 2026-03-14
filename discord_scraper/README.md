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
export PATH=$PATH:~/.dotnet
```

### 2. Build DiscordChatExporter CLI

```bash
cd DiscordChatExporter
dotnet publish DiscordChatExporter.Cli/DiscordChatExporter.Cli.csproj -c Release -o ./cli_output
```

This creates the CLI in `DiscordChatExporter/cli_output/DiscordChatExporter.Cli`

### 3. Set Your Discord Token

```bash
export DISCORD_TOKEN="your-user-token-here"
```

**Important:** Use a dummy Discord account, not your main one. Extract the token from browser dev tools (Local Storage) after logging into Discord.

## Usage

```bash
python3 export_discord.py --server <server_id> --channel <channel_id>
```

### Example

```bash
export DISCORD_TOKEN="MTV...Lwk"
python3 export_discord.py --server ... --channel ...
```

### Arguments

- `--server` - Discord server (guild) ID
- `--channel` - Discord channel ID (parent channel containing threads)

## Output Structure

```
discord_scraper/
├── DiscordChatExporter/          # Cloned repo (git submodule)
│   └── cli_output/               # Built CLI
├── raw_data/                     # Raw JSON exports
├── data/
│   ├── messages/{server_id}/{channel_id}/
│   │   └── messages.jsonl        # Processed messages with metadata
│   ├── schematics/{server_id}/   # Downloaded schematic files
│   └── metadata/{server_id}/
│       └── scrape_manifest.json  # Summary of what was collected
├── export_discord.py             # Main pipeline script
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
2. **Message History**: Discord API only returns last 100 messages per channel. Older attachments may not be found.
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
