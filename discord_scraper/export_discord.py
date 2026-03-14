#!/usr/bin/env python3
"""
Discord Scraper Pipeline

Exports Discord channels with threads and downloads schematic files.
Usage: export_discord.py --server <server_id> --channel <channel_id>

Required environment variable:
    DISCORD_TOKEN - Your Discord user token
"""

import argparse
import json
import os
import re
import requests
import subprocess
import sys
from pathlib import Path
from typing import Any

# Get CLI path (built from source in DiscordChatExporter/cli_output/)
SCRIPT_DIR = Path(__file__).parent
CLI_PATH = SCRIPT_DIR / "DiscordChatExporter" / "cli_output" / "DiscordChatExporter.Cli"

# Schematic file extensions to look for
SCHEMATIC_EXTENSIONS = [".litematic", ".schematic", ".schem", ".nbt"]


def get_discord_token() -> str:
    """Get Discord token from environment variable."""
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN environment variable is not set.")
        print("Please set it with: export DISCORD_TOKEN='your-token-here'")
        sys.exit(1)
    return token


def export_channel(token: str, server_id: str, channel_id: str, output_dir: Path) -> int:
    """Run DiscordChatExporter CLI to export channel with threads."""
    print(f"Exporting channel {channel_id} from server {server_id}...")
    
    cmd = [
        str(CLI_PATH), "export",
        "--token", token,
        "--channel", channel_id,
        "--format", "Json",
        "--include-threads", "All",
        "--output", str(output_dir)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Print output (filter out Ukraine message if desired)
    for line in result.stdout.split('\n'):
        if 'Thank you for supporting Ukraine' not in line:
            print(line)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    
    json_files = list(output_dir.glob("*.json"))
    print(f"Exported {len(json_files)} thread(s)")
    return len(json_files)


def extract_schematic_info(content: str) -> list[dict[str, str]]:
    """Extract channel_id, attachment_id, and filename from schematic URLs in content."""
    urls = []
    pattern = r'https://cdn\.discordapp\.com/attachments/(\d+)/(\d+)/([^"\s<>]+\.(?:litematic|schematic|schem|nbt))'
    matches = re.findall(pattern, content, re.IGNORECASE)
    
    for channel_id, attachment_id, filename in matches:
        ext = filename.split(".")[-1]
        urls.append({
            "filename": filename,
            "channel_id": channel_id,
            "attachment_id": attachment_id,
            "type": ext
        })
    return urls


def get_signed_url(token: str, channel_id: str, attachment_id: str, filename: str) -> str | None:
    """Query Discord API to get signed URL for an attachment (with pagination)."""
    try:
        headers = {
            "Authorization": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        before = None
        batch_num = 0
        
        while True:
            batch_num += 1
            if before:
                url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100&before={before}"
            else:
                url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100"
            
            resp = requests.get(url, headers=headers, timeout=30)
            
            if resp.status_code == 403:
                print(f"    Access denied to channel {channel_id}")
                return None
            elif resp.status_code != 200:
                print(f"    API error ({resp.status_code}) for channel {channel_id}")
                return None
            
            messages = resp.json()
            if not messages:
                break
            
            for msg in messages:
                for att in msg.get('attachments', []):
                    if att.get('id') == attachment_id:
                        print(f"    Found after {batch_num} batch(es)")
                        return att.get('url')
            
            # Get oldest message ID for pagination
            before = messages[-1]['id']
            
            # Safety limit: stop after 1000 messages (10 batches)
            if batch_num >= 10:
                break
        
        print(f"    Attachment {attachment_id} ({filename}) not found in channel {channel_id} (checked {batch_num} batches)")
        return None
    except Exception as e:
        print(f"    Error fetching signed URL: {e}")
        return None


def download_file(token: str, url: str, output_dir: Path, message_id: str, index: int, original_filename: str | None = None) -> tuple[Path | None, str]:
    """Download a file from URL and save to output directory."""
    try:
        cookies = {
            "token": token,
            "user_id": "148142253530730088",
            "__discord_locale": "en-US"
        }
        response = requests.get(url, cookies=cookies, timeout=30)
        
        if response.status_code == 404 or b"no longer available" in response.content:
            return None, "deleted"
        
        response.raise_for_status()
        
        if original_filename:
            filename = f"{message_id}_{index}_{original_filename}"
        else:
            ext = Path(url.split('?')[0]).suffix or ".litematic"
            filename = f"{message_id}_{index}{ext}"
        
        filename = re.sub(r'[^\w\.\-]', '_', filename)
        filepath = output_dir / filename
        
        filepath.write_bytes(response.content)
        print(f"  Downloaded: {filename} ({len(response.content)} bytes)")
        return filepath, "success"
    except Exception as e:
        return None, f"error: {e}"


def extract_image_urls(content: str, embeds: list[dict]) -> list[str]:
    """Extract image CDN links from message content and embeds."""
    urls = set()
    
    for match in re.findall(r'https://cdn\.discordapp\.com/attachments/\d+/\d+/[^"\s]+?\.(png|jpg|jpeg|gif|webp)', content, re.IGNORECASE):
        urls.add(match)
    
    for embed in embeds:
        if "thumbnail" in embed and "url" in embed["thumbnail"]:
            urls.add(embed["thumbnail"]["url"])
        if "image" in embed and "url" in embed["image"]:
            urls.add(embed["image"]["url"])
        if "url" in embed and embed["url"].endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
            urls.add(embed["url"])
    
    return list(urls)


def process_json_file(json_path: Path, token: str, schematics_dir: Path) -> list[dict[str, Any]]:
    """Process a single DiscordChatExporter JSON file."""
    messages = []
    
    with open(json_path, "r") as f:
        data = json.load(f)
    
    channel_data = data.get("channel", {})
    guild_data = data.get("guild", {})
    
    for msg in data.get("messages", []):
        msg_id = msg.get("id")
        content = msg.get("content", "")
        embeds = msg.get("embeds", [])
        attachments = msg.get("attachments", [])
        
        # Extract schematic info from content
        schematic_info = extract_schematic_info(content)
        
        # Check direct attachments
        for att in attachments:
            filename = att.get("filename", "")
            if any(filename.endswith(ext) for ext in SCHEMATIC_EXTENSIONS):
                schematic_info.append({
                    "filename": filename,
                    "channel_id": channel_data.get("id"),
                    "attachment_id": att.get("id"),
                    "type": filename.split(".")[-1],
                    "url": att.get("url", "")
                })
        
        # Extract image URLs
        image_urls = extract_image_urls(content, embeds)
        
        # Build message record
        message_record = {
            "message_id": msg_id,
            "server_id": guild_data.get("id"),
            "channel_id": channel_data.get("id"),
            "channel_name": channel_data.get("name", ""),
            "category": channel_data.get("category", ""),
            "author_id": msg.get("author", {}).get("id"),
            "author_name": msg.get("author", {}).get("name"),
            "timestamp": msg.get("timestamp"),
            "content": content,
            "schematics": [],
            "images": image_urls,
            "reactions": msg.get("reactions", []),
            "reply_to_message_id": msg.get("reference", {}).get("message_id") if msg.get("reference") else None
        }
        
        # Download schematics
        for idx, sch in enumerate(schematic_info):
            url = sch.get("url")
            if not url and "channel_id" in sch and "attachment_id" in sch:
                print(f"  Fetching signed URL for {sch['filename']}...")
                url = get_signed_url(token, sch["channel_id"], sch["attachment_id"], sch["filename"])
            
            if url:
                filepath, status = download_file(token, url, schematics_dir, msg_id, idx + 1, sch.get("filename"))
                if filepath:
                    message_record["schematics"].append({
                        "filename": filepath.name,
                        "type": sch["type"],
                        "original_url": url,
                        "status": status
                    })
                else:
                    message_record["schematics"].append({
                        "filename": sch["filename"],
                        "type": sch["type"],
                        "original_url": url,
                        "status": status,
                        "downloaded": False
                    })
            else:
                message_record["schematics"].append({
                    "filename": sch["filename"],
                    "type": sch["type"],
                    "status": "error: could not get signed URL",
                    "downloaded": False
                })
        
        messages.append(message_record)
    
    return messages


def main():
    parser = argparse.ArgumentParser(
        description="Discord scraper pipeline - exports channels and downloads schematics"
    )
    parser.add_argument("--server", required=True, help="Server ID")
    parser.add_argument("--channel", required=True, help="Channel ID")
    args = parser.parse_args()
    
    server_id = args.server
    channel_id = args.channel
    
    # Get token from environment
    token = get_discord_token()
    
    # Verify CLI exists
    if not CLI_PATH.exists():
        print(f"ERROR: DiscordChatExporter CLI not found at {CLI_PATH}")
        print("Please build it first:")
        print("  cd DiscordChatExporter")
        print("  dotnet publish DiscordChatExporter.Cli/DiscordChatExporter.Cli.csproj -c Release -o ./cli_output")
        sys.exit(1)
    
    # Setup directories
    raw_data_dir = SCRIPT_DIR / "raw_data"
    output_dir = SCRIPT_DIR / "data"
    messages_dir = output_dir / "messages" / server_id / channel_id
    schematics_dir = output_dir / "schematics" / server_id
    metadata_dir = output_dir / "metadata" / server_id
    
    raw_data_dir.mkdir(exist_ok=True)
    messages_dir.mkdir(parents=True, exist_ok=True)
    schematics_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Export channel
    print(f"{'='*60}")
    print(f"Step 1: Exporting Discord channel")
    print(f"{'='*60}")
    num_files = export_channel(token, server_id, channel_id, raw_data_dir)
    
    if num_files == 0:
        print("No files exported. Exiting.")
        sys.exit(1)
    
    # Step 2: Process and download
    print(f"\n{'='*60}")
    print(f"Step 2: Processing and downloading schematics")
    print(f"{'='*60}")
    
    json_files = list(raw_data_dir.glob("*.json"))
    all_messages = []
    total_schematics = 0
    downloaded_schematics = 0
    
    for json_path in json_files:
        print(f"\nProcessing: {json_path.name}")
        messages = process_json_file(json_path, token, schematics_dir)
        all_messages.extend(messages)
        
        for msg in messages:
            total_schematics += len(msg["schematics"])
            downloaded_schematics += sum(1 for s in msg["schematics"] if s.get("status") == "success")
    
    # Step 3: Save results
    print(f"\n{'='*60}")
    print(f"Step 3: Saving results")
    print(f"{'='*60}")
    
    messages_file = messages_dir / "messages.jsonl"
    with open(messages_file, "w") as f:
        for msg in all_messages:
            f.write(json.dumps(msg) + "\n")
    
    manifest = {
        "server_id": server_id,
        "channel_id": channel_id,
        "total_messages": len(all_messages),
        "total_schematics_found": total_schematics,
        "total_schematics_downloaded": downloaded_schematics,
        "json_files_processed": len(json_files),
        "output_files": {
            "messages": str(messages_file),
            "schematics_dir": str(schematics_dir)
        }
    }
    
    manifest_file = metadata_dir / "scrape_manifest.json"
    with open(manifest_file, "w") as f:
        json.dump(manifest, f, indent=2)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"COMPLETE!")
    print(f"{'='*60}")
    print(f"Messages saved: {messages_file}")
    print(f"Total messages: {len(all_messages)}")
    print(f"Schematics found: {total_schematics}")
    print(f"Schematics downloaded: {downloaded_schematics}")
    print(f"Schematics location: {schematics_dir}")
    print(f"Manifest: {manifest_file}")


if __name__ == "__main__":
    main()
