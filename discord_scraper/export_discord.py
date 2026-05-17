#!/usr/bin/env python3
"""
Discord Scraper Pipeline (MIRA)

Two modes:
- scrape: export channels and download raw files (permissive, idempotent)
- clean: validate schematics in Minecraft and lightly clean messages

Required environment variable:
    DISCORD_TOKEN - Discord user token with access to the servers
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import requests

# Paths
SCRIPT_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.json"
DEFAULT_CLI_PATH = SCRIPT_DIR / "DiscordChatExporter" / "cli_output" / "DiscordChatExporter.Cli"

DATA_DIR = SCRIPT_DIR / "data"
RAW_MESSAGES_DIR = DATA_DIR / "raw_messages"
RAW_SCHEMATICS_DIR = DATA_DIR / "raw_schematics"
CLEAN_MESSAGES_DIR = DATA_DIR / "clean_messages"
CLEAN_SCHEMATICS_DIR = DATA_DIR / "clean_schematics"
METADATA_DIR = DATA_DIR / "metadata"
RAW_EXPORT_DIR = SCRIPT_DIR / "raw_data"

SCHEMATIC_EXTENSIONS = [".litematic", ".schematic", ".schem", ".nbt"]

# Discord channel types worth scraping: thread (11), forum (15).
# Text channels (0) are typically chat/discussion, not schematic submissions.
# Skip text (0), voice (2), category (4), announcement (5), stage (13), etc.
SCRAPABLE_CHANNEL_TYPES = {11, 15}


class RateLimiter:
    """
    Simple time-based rate limiter.

    Conservative default is 0.5 req/s (1 request every 2 seconds).
    """

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        self._min_interval_s = 1.0 / requests_per_second
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
        self._next_allowed = time.monotonic() + self._min_interval_s


@dataclass(frozen=True)
class ChannelInfo:
    id: str
    name: str
    parent_id: str | None
    type: int | None


class DiscordApi:
    def __init__(
        self,
        token: str,
        rate_limiter: RateLimiter | None = None,
        *,
        retry_on_429: bool = True,
        max_retries: int = 5,
        backoff_multiplier: float = 2.0,
    ) -> None:
        self._token = token
        self._rate_limiter = rate_limiter
        self._retry_on_429 = retry_on_429
        self._max_retries = max_retries
        self._backoff_multiplier = backoff_multiplier

    def _get(self, url: str, *, timeout_s: int = 30) -> requests.Response:
        headers = {
            "Authorization": self._token,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }

        attempt = 0
        sleep_s = 1.0
        while True:
            attempt += 1
            if self._rate_limiter:
                self._rate_limiter.wait()
            resp = requests.get(url, headers=headers, timeout=timeout_s)
            if resp.status_code != 429 or not self._retry_on_429 or attempt > self._max_retries:
                return resp

            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    sleep_s = max(sleep_s, float(retry_after))
                except Exception:
                    pass
            time.sleep(sleep_s)
            sleep_s *= self._backoff_multiplier

    def list_guild_channels(self, guild_id: str) -> list[ChannelInfo]:
        url = f"https://discord.com/api/v9/guilds/{guild_id}/channels"
        resp = self._get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Discord API error {resp.status_code} listing channels for guild {guild_id}")
        data = resp.json()
        channels: list[ChannelInfo] = []
        for ch in data:
            ch_id = str(ch.get("id"))
            name = ch.get("name") or ""
            channels.append(
                ChannelInfo(
                    id=ch_id,
                    name=name,
                    parent_id=str(ch.get("parent_id")) if ch.get("parent_id") else None,
                    type=ch.get("type"),
                )
            )
        return channels


class DiscordChatExporter:
    def __init__(self, cli_path: Path) -> None:
        self._cli_path = cli_path

    def verify_exists(self) -> None:
        if not self._cli_path.exists():
            msg = [
                f"DiscordChatExporter CLI not found at {self._cli_path}",
                "",
                "Build it with:",
                f"  cd {SCRIPT_DIR / 'DiscordChatExporter'}",
                "  dotnet publish DiscordChatExporter.Cli/DiscordChatExporter.Cli.csproj -c Release -o ./cli_output",
                "",
                "Or pass a custom location with --cli-path <path>.",
            ]
            raise FileNotFoundError("\n".join(msg))

    def export_channel_json(
        self,
        token: str,
        channel_id: str,
        output_dir: Path,
        *,
        after: str | None = None,
        before: str | None = None,
        message_filter: str | None = None,
        reverse: bool = False,
        partition: str | None = None,
        disable_ukraine_banner: bool = True,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        _log(f"DiscordChatExporter: exporting channel_id={channel_id} -> {output_dir}")
        cmd = [
            str(self._cli_path),
            "export",
            "--token",
            token,
            "--channel",
            str(channel_id),
            "--format",
            "Json",
            "--include-threads",
            "All",
            "--output",
            str(output_dir),
        ]
        if after:
            cmd += ["--after", after]
        if before:
            cmd += ["--before", before]
        if message_filter:
            cmd += ["--filter", message_filter]
        if reverse:
            cmd += ["--reverse"]
        if partition:
            cmd += ["-p", partition]
        # DiscordChatExporter flag naming varies by version; current uses --fuck-russia
        if disable_ukraine_banner:
            cmd += ["--fuck-russia"]

        import subprocess

        start = time.monotonic()
        # Stream output live so it doesn't look hung.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if "Thank you for supporting Ukraine" not in line:
                print(line, end="")
        rc = proc.wait()
        elapsed = time.monotonic() - start
        if rc != 0:
            raise RuntimeError(f"DiscordChatExporter exited with code {rc} for channel_id={channel_id}")

        jsons = list(output_dir.glob("*.json"))
        _log(f"DiscordChatExporter: done channel_id={channel_id} ({len(jsons)} json file(s)) in {elapsed:.1f}s")
        return jsons

    def export_channel_json_process(
        self,
        token: str,
        channel_id: str,
        output_dir: Path,
        *,
        after: str | None = None,
        before: str | None = None,
        message_filter: str | None = None,
        reverse: bool = False,
        partition: str | None = None,
        disable_ukraine_banner: bool = True,
    ) -> tuple[Any, float]:
        """
        Starts the exporter as an external process (non-blocking).
        Returns (process, start_time_monotonic).
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [
            str(self._cli_path),
            "export",
            "--token",
            token,
            "--channel",
            str(channel_id),
            "--format",
            "Json",
            "--include-threads",
            "All",
            "--output",
            str(output_dir),
        ]
        if after:
            cmd += ["--after", after]
        if before:
            cmd += ["--before", before]
        if message_filter:
            cmd += ["--filter", message_filter]
        if reverse:
            cmd += ["--reverse"]
        if partition:
            cmd += ["-p", partition]
        if disable_ukraine_banner:
            # DiscordChatExporter in this repo uses --fuck-russia to suppress the banner.
            cmd += ["--fuck-russia"]

        import subprocess

        _log(f"DiscordChatExporter: starting (async) channel_id={channel_id} -> {output_dir}")
        start = time.monotonic()
        proc = subprocess.Popen(cmd)
        return proc, start


@dataclass(frozen=True)
class DownloadResult:
    filepath: Path | None
    status: str
    url: str | None


class DiscordCdnDownloader:
    def __init__(
        self,
        token: str,
        rate_limiter: RateLimiter | None = None,
        *,
        retry_on_429: bool = True,
        max_retries: int = 5,
        backoff_multiplier: float = 2.0,
    ) -> None:
        self._token = token
        self._rate_limiter = rate_limiter
        self._retry_on_429 = retry_on_429
        self._max_retries = max_retries
        self._backoff_multiplier = backoff_multiplier

    def _get(self, url: str, *, timeout_s: int = 30) -> requests.Response:
        cookies = {"token": self._token, "__discord_locale": "en-US"}

        attempt = 0
        sleep_s = 1.0
        while True:
            attempt += 1
            if self._rate_limiter:
                self._rate_limiter.wait()
            resp = requests.get(url, cookies=cookies, timeout=timeout_s)
            if resp.status_code != 429 or not self._retry_on_429 or attempt > self._max_retries:
                return resp

            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    sleep_s = max(sleep_s, float(retry_after))
                except Exception:
                    pass
            time.sleep(sleep_s)
            sleep_s *= self._backoff_multiplier

    def download_file(
        self,
        url: str,
        output_dir: Path,
        message_id: str,
        index: int,
        *,
        original_filename: str | None = None,
    ) -> DownloadResult:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            if original_filename:
                filename = f"{message_id}_{index}_{original_filename}"
            else:
                ext = Path(url.split("?")[0]).suffix or ".litematic"
                filename = f"{message_id}_{index}{ext}"

            filename = re.sub(r"[^\w\.\-]", "_", filename)
            filepath = output_dir / filename
            if filepath.exists() and filepath.stat().st_size > 0:
                return DownloadResult(filepath=filepath, status="success", url=url)

            resp = self._get(url)
            if resp.status_code == 404 or b"no longer available" in resp.content:
                return DownloadResult(filepath=None, status="deleted", url=url)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)
            return DownloadResult(filepath=filepath, status="success", url=url)
        except Exception as e:
            return DownloadResult(filepath=None, status=f"error: {e}", url=url)


class DiscordSignedUrlResolver:
    def __init__(
        self,
        token: str,
        rate_limiter: RateLimiter | None = None,
        *,
        retry_on_429: bool = True,
        max_retries: int = 5,
        backoff_multiplier: float = 2.0,
    ) -> None:
        self._token = token
        self._rate_limiter = rate_limiter
        self._retry_on_429 = retry_on_429
        self._max_retries = max_retries
        self._backoff_multiplier = backoff_multiplier

    def _get(self, url: str, *, timeout_s: int = 30) -> requests.Response:
        headers = {
            "Authorization": self._token,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        }

        attempt = 0
        sleep_s = 1.0
        while True:
            attempt += 1
            if self._rate_limiter:
                self._rate_limiter.wait()
            resp = requests.get(url, headers=headers, timeout=timeout_s)
            if resp.status_code != 429 or not self._retry_on_429 or attempt > self._max_retries:
                return resp

            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    sleep_s = max(sleep_s, float(retry_after))
                except Exception:
                    pass
            time.sleep(sleep_s)
            sleep_s *= self._backoff_multiplier

    def get_signed_url(
        self,
        channel_id: str,
        attachment_id: str,
        *,
        max_batches: int = 200,
    ) -> str | None:
        before: str | None = None
        batch = 0

        while batch < max_batches:
            batch += 1
            if before:
                url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100&before={before}"
            else:
                url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=100"

            resp = self._get(url)
            if resp.status_code == 403:
                return None
            if resp.status_code != 200:
                return None

            messages: list[dict[str, Any]] = resp.json()
            if not messages:
                return None

            for msg in messages:
                for att in msg.get("attachments", []):
                    if str(att.get("id")) == str(attachment_id):
                        return att.get("url")

            before = str(messages[-1]["id"])

        return None


def extract_schematic_links_from_content(content: str) -> list[dict[str, str]]:
    urls = []
    pattern = r'https://cdn\.discordapp\.com/attachments/(\d+)/(\d+)/([^"\s<>]+\.(?:litematic|schematic|schem|nbt))'
    matches = re.findall(pattern, content, re.IGNORECASE)
    for channel_id, attachment_id, filename in matches:
        ext = filename.split(".")[-1]
        urls.append(
            {
                "filename": filename,
                "channel_id": channel_id,
                "attachment_id": attachment_id,
                "type": ext,
            }
        )
    return urls


def extract_image_urls(content: str, embeds: list[dict]) -> list[str]:
    urls: set[str] = set()

    for m in re.findall(
        r"https://cdn\.discordapp\.com/attachments/\d+/\d+/[^\s<>\"']+\.(?:png|jpg|jpeg|gif|webp)",
        content,
        re.IGNORECASE,
    ):
        urls.add(m)

    for embed in embeds:
        thumb = embed.get("thumbnail", {})
        if isinstance(thumb, dict) and thumb.get("url"):
            urls.add(str(thumb["url"]))
        img = embed.get("image", {})
        if isinstance(img, dict) and img.get("url"):
            urls.add(str(img["url"]))
        if embed.get("url") and str(embed["url"]).lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            urls.add(str(embed["url"]))

    return sorted(urls)


_SPAM_PATTERNS = [
    re.compile(r"^Message deleted", re.IGNORECASE),
    re.compile(r"^This message was removed", re.IGNORECASE),
    re.compile(r"^\[deleted\]$", re.IGNORECASE),
]


def _dedupe_urls(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        if not v.startswith("http"):
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _strip_cdn_urls_from_text(text: str) -> str:
    text = re.sub(r"https://cdn\.discordapp\.com/attachments/\S+", "", text)
    text = re.sub(r"https://media\.discordapp\.net/attachments/\S+", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Drop lines that became empty placeholders after URL stripping, e.g. "- Foo:" or "- Foo:   "
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if re.match(r"^-+\s*[^:]{0,200}:\s*$", stripped):
            continue
        if stripped in {"-", "*"}:
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class CleanMessageResult:
    cleaned: dict[str, Any] | None
    filtered_reason: str | None


def clean_message(raw_message: dict[str, Any], *, cleaning_version: str = "1.0") -> CleanMessageResult:
    content = str(raw_message.get("content") or "")
    schematics = raw_message.get("schematics") or []

    if not content.strip() and len(schematics) == 0:
        return CleanMessageResult(cleaned=None, filtered_reason="empty_no_schematics")

    for pat in _SPAM_PATTERNS:
        if pat.search(content.strip()):
            return CleanMessageResult(cleaned=None, filtered_reason="spam_deleted_message")

    cleaned_content = _strip_cdn_urls_from_text(content)
    cleaned_schematics = [s for s in schematics if isinstance(s, dict) and s.get("status") == "success"]

    cleaned = {
        "message_id": raw_message.get("message_id"),
        "server_id": raw_message.get("server_id"),
        "channel_id": raw_message.get("channel_id"),
        "channel_name": raw_message.get("channel_name", ""),
        "category": raw_message.get("category", ""),
        "author_id": raw_message.get("author_id"),
        "author_name": raw_message.get("author_name"),
        "timestamp": raw_message.get("timestamp"),
        "content": cleaned_content,
        "schematics": cleaned_schematics,
        "images": [],
        "reactions": raw_message.get("reactions") or [],
        "reply_to_message_id": raw_message.get("reply_to_message_id"),
        "cleaned": True,
        "cleaning_version": cleaning_version,
    }

    if not cleaned["content"] and len(cleaned_schematics) == 0:
        return CleanMessageResult(cleaned=None, filtered_reason="empty_after_cleaning")

    return CleanMessageResult(cleaned=cleaned, filtered_reason=None)


@dataclass(frozen=True)
class SchematicValidationResult:
    valid: bool
    error: str | None
    block_count: int
    bounds: tuple[tuple[int, int, int], tuple[int, int, int]] | None


def validate_schematic_with_minecraft(
    schematic_path: Path,
    *,
    paste_origin: tuple[int, int, int] = (10000, 100, 10000),
) -> SchematicValidationResult:
    try:
        # Import lazily so `--help` works without deps.
        from data_mining.parser import SchematicParser
        from simulation.bridge import MinecraftBridge
        from simulation.replicator import replicate_blocks

        parser = SchematicParser(str(schematic_path))
        bounds = parser.get_bounds()
        blocks = parser.parse_blocks()
        block_count = len(blocks)

        if block_count <= 0:
            return SchematicValidationResult(valid=False, error="empty_schematic", block_count=0, bounds=bounds)

        bridge = MinecraftBridge()
        bridge.connect()
        try:
            replicate_blocks(blocks, paste_origin, bounds, bridge)
        finally:
            bridge.disconnect()

        return SchematicValidationResult(valid=True, error=None, block_count=block_count, bounds=bounds)
    except Exception as e:
        return SchematicValidationResult(valid=False, error=str(e), block_count=0, bounds=None)


def get_discord_token() -> str:
    """Get Discord token from environment variable."""
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("ERROR: DISCORD_TOKEN environment variable is not set.")
        print("Please set it with: export DISCORD_TOKEN='your-token-here'")
        sys.exit(1)
    return token


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _log(msg: str) -> None:
    print(f"[{_utc_now_iso()}] {msg}", flush=True)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True))


def _load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. Create it or pass --config."
        )
    return json.loads(config_path.read_text())


def _migrate_legacy_dirs() -> None:
    """
    Migrates old on-disk layout:
      data/messages -> data/raw_messages
      data/schematics -> data/raw_schematics
    """
    legacy_messages = DATA_DIR / "messages"
    legacy_schematics = DATA_DIR / "schematics"

    if legacy_messages.exists() and not RAW_MESSAGES_DIR.exists():
        legacy_messages.rename(RAW_MESSAGES_DIR)
    if legacy_schematics.exists() and not RAW_SCHEMATICS_DIR.exists():
        legacy_schematics.rename(RAW_SCHEMATICS_DIR)


def _read_existing_message_ids(jsonl_path: Path) -> set[str]:
    if not jsonl_path.exists():
        return set()
    ids: set[str] = set()
    with jsonl_path.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                mid = obj.get("message_id")
                if mid:
                    ids.add(str(mid))
            except Exception:
                continue
    return ids


def _iter_process_export_json(
    export_json_path: Path,
    *,
    token: str,
    schematics_out_dir: Path,
    rate_limiter: RateLimiter,
    signed_url_batches: int,
    retry_on_429: bool,
    max_retries: int,
    backoff_multiplier: float,
    max_new_messages: int,
    existing_ids: set[str],
) -> Iterator[dict[str, Any]]:
    data = json.loads(export_json_path.read_text())

    channel_data = data.get("channel", {}) or {}
    guild_data = data.get("guild", {}) or {}

    resolver = DiscordSignedUrlResolver(
        token,
        rate_limiter=rate_limiter,
        retry_on_429=retry_on_429,
        max_retries=max_retries,
        backoff_multiplier=backoff_multiplier,
    )
    downloader = DiscordCdnDownloader(
        token,
        rate_limiter=rate_limiter,
        retry_on_429=retry_on_429,
        max_retries=max_retries,
        backoff_multiplier=backoff_multiplier,
    )

    raw_msgs = data.get("messages", []) or []
    _log(f"Processing export json: {export_json_path.name} ({len(raw_msgs)} message(s))")
    last_heartbeat = time.monotonic()
    added = 0
    for i, msg in enumerate(raw_msgs):
        msg_id = str(msg.get("id"))
        if max_new_messages > 0 and added >= max_new_messages:
            break
        if msg_id in existing_ids:
            continue

        content = msg.get("content") or ""
        embeds = msg.get("embeds") or []
        attachments = msg.get("attachments") or []

        schematic_info = extract_schematic_links_from_content(str(content))

        for att in attachments:
            # DiscordChatExporter uses camelCase "fileName"; Discord API uses "filename"
            filename = str(att.get("fileName") or att.get("filename") or "")
            if any(filename.lower().endswith(ext) for ext in SCHEMATIC_EXTENSIONS):
                schematic_info.append(
                    {
                        "filename": filename,
                        "channel_id": str(channel_data.get("id") or ""),
                        "attachment_id": str(att.get("id") or ""),
                        "type": filename.split(".")[-1],
                        "url": str(att.get("url") or ""),
                    }
                )

        image_urls = extract_image_urls(str(content), embeds if isinstance(embeds, list) else [])

        message_record: dict[str, Any] = {
            "message_id": msg_id,
            "server_id": str(guild_data.get("id") or ""),
            "channel_id": str(channel_data.get("id") or ""),
            "channel_name": channel_data.get("name", "") or "",
            "category": channel_data.get("category", "") or "",
            "author_id": str((msg.get("author") or {}).get("id") or ""),
            "author_name": (msg.get("author") or {}).get("name"),
            "timestamp": msg.get("timestamp"),
            "content": content,
            "schematics": [],
            "images": image_urls,
            "reactions": msg.get("reactions", []) or [],
            "reply_to_message_id": (msg.get("reference") or {}).get("message_id") if msg.get("reference") else None,
        }

        for idx, sch in enumerate(schematic_info):
            url = sch.get("url")
            if not url and sch.get("channel_id") and sch.get("attachment_id"):
                url = resolver.get_signed_url(
                    sch["channel_id"],
                    sch["attachment_id"],
                    max_batches=signed_url_batches,
                )

            if url:
                res = downloader.download_file(
                    url,
                    schematics_out_dir,
                    msg_id,
                    idx + 1,
                    original_filename=sch.get("filename"),
                )
                if res.filepath:
                    message_record["schematics"].append(
                        {
                            "filename": res.filepath.name,
                            "type": sch.get("type"),
                            "original_url": res.url,
                            "status": res.status,
                        }
                    )
                else:
                    message_record["schematics"].append(
                        {
                            "filename": sch.get("filename"),
                            "type": sch.get("type"),
                            "original_url": res.url,
                            "status": res.status,
                            "downloaded": False,
                        }
                    )
            else:
                message_record["schematics"].append(
                    {
                        "filename": sch.get("filename"),
                        "type": sch.get("type"),
                        "status": "error: could not get signed URL",
                        "downloaded": False,
                    }
                )

        yield message_record
        added += 1

        now = time.monotonic()
        if now - last_heartbeat >= 5.0:
            _log(f"Still working... parsed {i+1}/{len(raw_msgs)} messages from {export_json_path.name}")
            last_heartbeat = now

    return


def _load_scrape_status(server_id: str) -> dict[str, Any]:
    return _read_json(METADATA_DIR / server_id / "scrape_status.json", default={})


def _save_scrape_status(server_id: str, status: dict[str, Any]) -> None:
    status["server_id"] = server_id
    status["last_updated"] = _utc_now_iso()
    _write_json(METADATA_DIR / server_id / "scrape_status.json", status)


def _load_cleaning_status(server_id: str) -> dict[str, Any]:
    return _read_json(METADATA_DIR / server_id / "cleaning_status.json", default={})


def _save_cleaning_status(server_id: str, status: dict[str, Any]) -> None:
    status["server_id"] = server_id
    status["last_updated"] = _utc_now_iso()
    _write_json(METADATA_DIR / server_id / "cleaning_status.json", status)


@dataclass(frozen=True)
class ScrapeSettings:
    requests_per_second: float
    signed_url_max_batches: int
    retry_on_429: bool
    max_retries: int
    backoff_multiplier: float


def _scrape_channel(
    *,
    exporter: DiscordChatExporter,
    token: str,
    server_id: str,
    channel_id: str,
    settings: ScrapeSettings,
    force: bool,
    max_messages: int,
    export_after: str | None,
    export_before: str | None,
    export_filter: str | None,
) -> str:
    rate_limiter = RateLimiter(settings.requests_per_second)

    RAW_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
    RAW_SCHEMATICS_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    status = _load_scrape_status(server_id)
    channels_status = status.get("channels", {}) if isinstance(status.get("channels", {}), dict) else {}
    if not force and channel_id in channels_status and channels_status[channel_id].get("status") == "complete":
        _log(f"Skipping channel {channel_id} (already complete). Use --force to re-scrape.")
        return "skipped"

    export_dir = RAW_EXPORT_DIR / server_id / channel_id
    _log(f"Scrape start server_id={server_id} channel_id={channel_id}")
    channels_status[channel_id] = {
        "status": "in_progress",
        "total_messages_added": 0,
        "total_schematics_found": 0,
        "downloaded_schematics": 0,
        "last_scraped": _utc_now_iso(),
        "errors": [],
    }
    status["channels"] = channels_status
    _save_scrape_status(server_id, status)
    messages_out_dir = RAW_MESSAGES_DIR / server_id / channel_id
    messages_out_dir.mkdir(parents=True, exist_ok=True)
    messages_path = messages_out_dir / "messages.jsonl"
    existing_ids = _read_existing_message_ids(messages_path)

    schematics_out_dir = RAW_SCHEMATICS_DIR / server_id
    schematics_out_dir.mkdir(parents=True, exist_ok=True)

    total_messages = 0
    total_schematics = 0
    downloaded_schematics = 0

    # For quick tests: export newest-first so our early stop hits quickly.
    # We do NOT rely on `--partition` to cap total messages because it splits output,
    # it doesn't stop the exporter from exporting the entire channel.
    reverse = max_messages > 0
    partition = None

    with messages_path.open("a") as out_f:
        if max_messages > 0:
            proc, export_start_monotonic = exporter.export_channel_json_process(
                token,
                channel_id,
                export_dir,
                after=export_after,
                before=export_before,
                message_filter=export_filter,
                reverse=reverse,
                partition=partition,
            )

            processed_files: set[str] = set()
            json_decode_retries: dict[str, int] = {}  # track per-file JSONDecodeError retries
            export_start_wall = time.time()
            heartbeat_last = time.monotonic()
            last_progress_monotonic = time.monotonic()  # detect stale progress
            MAX_JSON_RETRIES = 10
            MAX_EXPORT_TIMEOUT_S = 1800  # 30 min hard timeout per channel
            STALE_PROGRESS_TIMEOUT_S = 300  # 5 min with no new messages = stuck

            while True:
                json_paths = sorted([p for p in export_dir.glob("*.json") if p.is_file()])
                for jf in json_paths:
                    jf_key = str(jf)
                    if jf_key in processed_files:
                        continue
                    try:
                        if jf.stat().st_mtime < export_start_wall:
                            continue
                    except Exception:
                        continue

                    try:
                        _log(f"Parsing export file {jf.name}")
                        remaining = max_messages - total_messages
                        processed_iter = _iter_process_export_json(
                            jf,
                            token=token,
                            schematics_out_dir=schematics_out_dir,
                            rate_limiter=rate_limiter,
                            signed_url_batches=settings.signed_url_max_batches,
                            retry_on_429=settings.retry_on_429,
                            max_retries=settings.max_retries,
                            backoff_multiplier=settings.backoff_multiplier,
                            max_new_messages=remaining if remaining > 0 else 0,
                            existing_ids=existing_ids,
                        )
                        for msg in processed_iter:
                            mid = str(msg.get("message_id"))
                            if mid in existing_ids:
                                continue
                            existing_ids.add(mid)
                            out_f.write(json.dumps(msg) + "\n")
                            out_f.flush()
                            total_messages += 1
                            total_schematics += len(msg.get("schematics") or [])
                            downloaded_schematics += sum(
                                1
                                for s in (msg.get("schematics") or [])
                                if isinstance(s, dict) and s.get("status") == "success"
                            )
                            if total_messages >= max_messages:
                                break
                        # Successfully parsed — clear retry counter and mark done
                        json_decode_retries.pop(jf_key, None)
                        processed_files.add(jf_key)
                    except json.JSONDecodeError:
                        # File likely isn't fully written yet; retry with backoff.
                        retries = json_decode_retries.get(jf_key, 0) + 1
                        json_decode_retries[jf_key] = retries
                        if retries >= MAX_JSON_RETRIES:
                            _log(f"Skipping {jf.name} after {MAX_JSON_RETRIES} JSON decode failures")
                            processed_files.add(jf_key)  # don't retry again
                        else:
                            _log(f"JSON decode fail #{retries} for {jf.name}, will retry")
                        continue

                    if total_messages >= max_messages:
                        break

                if total_messages >= max_messages:
                    _log(
                        f"Reached --max-messages {max_messages}; terminating exporter "
                        f"and stopping parse/download."
                    )
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=20)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

                rc = proc.poll()
                if rc is not None:
                    _log(f"Exporter process exited with code {rc}")
                    if rc != 0 and total_messages == 0:
                        # Likely a forbidden or inaccessible channel
                        channels_status[channel_id] = {
                            "status": "forbidden" if rc != 0 else "error",
                            "total_messages_added": 0,
                            "total_schematics_found": 0,
                            "downloaded_schematics": 0,
                            "last_scraped": _utc_now_iso(),
                            "errors": [f"exporter_exit_code_{rc}"],
                        }
                        status["channels"] = channels_status
                        _save_scrape_status(server_id, status)
                        _log(f"Channel {channel_id} inaccessible (exit code {rc}), marking as forbidden")
                        return "forbidden"
                    break

                now = time.monotonic()
                elapsed = now - export_start_monotonic

                # Hard timeout: bail if export takes too long
                if elapsed > MAX_EXPORT_TIMEOUT_S:
                    _log(f"Export timeout ({MAX_EXPORT_TIMEOUT_S}s) for channel {channel_id}, terminating")
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=10)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

                # Stale progress: if no new messages for a long time, likely stuck
                if total_messages > 0 or len(processed_files) > 0:
                    last_progress_monotonic = now
                if now - last_progress_monotonic > STALE_PROGRESS_TIMEOUT_S:
                    _log(
                        f"No progress for {STALE_PROGRESS_TIMEOUT_S}s on channel {channel_id}, "
                        f"terminating exporter"
                    )
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=10)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

                if now - heartbeat_last >= 5.0:
                    _log(
                        f"Still working... added_messages={total_messages}/{max_messages} "
                        f"parsed_json_files={len(processed_files)} "
                        f"export_elapsed={elapsed:.1f}s"
                    )
                    heartbeat_last = now

                time.sleep(2)

        else:
            json_files = exporter.export_channel_json(
                token,
                channel_id,
                export_dir,
                after=export_after,
                before=export_before,
                message_filter=export_filter,
                reverse=reverse,
                partition=partition,
            )
            if not json_files:
                channels_status[channel_id] = {
                    "status": "error",
                    "error": "no_json_files_exported",
                    "last_scraped": _utc_now_iso(),
                }
                status["channels"] = channels_status
                _save_scrape_status(server_id, status)
                _log(f"Scrape failed server_id={server_id} channel_id={channel_id} reason=no_json_files_exported")
                return "error"

            for jf in json_files:
                _log(f"Parsing export file {jf.name}")
                processed = _iter_process_export_json(
                    jf,
                    token=token,
                    schematics_out_dir=schematics_out_dir,
                    rate_limiter=rate_limiter,
                    signed_url_batches=settings.signed_url_max_batches,
                    retry_on_429=settings.retry_on_429,
                    max_retries=settings.max_retries,
                    backoff_multiplier=settings.backoff_multiplier,
                    max_new_messages=0,
                    existing_ids=existing_ids,
                )
                for msg in processed:
                    mid = str(msg.get("message_id"))
                    if mid in existing_ids:
                        continue
                    existing_ids.add(mid)
                    out_f.write(json.dumps(msg) + "\n")
                    out_f.flush()
                    total_messages += 1
                    total_schematics += len(msg.get("schematics") or [])
                    downloaded_schematics += sum(
                        1
                        for s in (msg.get("schematics") or [])
                        if isinstance(s, dict) and s.get("status") == "success"
                    )

    limited = max_messages > 0 and total_messages >= max_messages
    channels_status[channel_id] = {
        "status": "partial" if limited else "complete",
        "total_messages_added": total_messages,
        "total_schematics_found": total_schematics,
        "downloaded_schematics": downloaded_schematics,
        "last_scraped": _utc_now_iso(),
        "errors": [],
    }
    status["channels"] = channels_status
    _save_scrape_status(server_id, status)
    _log(
        f"Scrape done server_id={server_id} channel_id={channel_id} "
        f"added_messages={total_messages} schematics_found={total_schematics} downloaded={downloaded_schematics}"
    )
    return "partial" if limited else "complete"


def _iter_raw_message_files(*, server_id: str | None = None) -> list[Path]:
    root = RAW_MESSAGES_DIR
    if not root.exists():
        return []
    if server_id:
        return list((root / server_id).glob("*/messages.jsonl"))
    return list(root.glob("*/*/messages.jsonl"))


def _clean_server(
    *,
    server_id: str,
    messages_only: bool,
    schematics_only: bool,
    force: bool,
) -> None:
    status = _load_cleaning_status(server_id)
    status.setdefault("messages", {})
    status.setdefault("schematics", {})

    # Schematics validation
    if not messages_only:
        raw_s_dir = RAW_SCHEMATICS_DIR / server_id
        clean_s_dir = CLEAN_SCHEMATICS_DIR / server_id
        raw_files = sorted([p for p in raw_s_dir.glob("*") if p.is_file()]) if raw_s_dir.exists() else []
        validated = 0
        errors: list[dict[str, str]] = []

        clean_s_dir.mkdir(parents=True, exist_ok=True)

        for fp in raw_files:
            out_fp = clean_s_dir / fp.name
            if out_fp.exists() and not force:
                validated += 1
                continue
            res = validate_schematic_with_minecraft(fp)
            if res.valid:
                out_fp.write_bytes(fp.read_bytes())
                validated += 1
            else:
                errors.append({"filename": fp.name, "error": res.error or "unknown"})

        status["schematics"] = {
            "total_raw": len(raw_files),
            "total_validated": validated,
            "validation_errors": errors[:200],
            "last_validated": _utc_now_iso(),
        }

    # Messages cleaning
    if not schematics_only:
        cleaned_total = 0
        filtered_total = 0
        filtered_reasons: dict[str, int] = {}

        raw_message_files = _iter_raw_message_files(server_id=server_id)
        for raw_path in raw_message_files:
            parts = raw_path.parts
            # .../raw_messages/{server_id}/{channel_id}/messages.jsonl
            channel_id = parts[-2]
            out_dir = CLEAN_MESSAGES_DIR / server_id / channel_id
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "messages.jsonl"
            if out_path.exists() and force:
                out_path.unlink()

            existing_ids = _read_existing_message_ids(out_path)
            with raw_path.open("r") as in_f, out_path.open("a") as out_f:
                for line in in_f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_msg = json.loads(line)
                    except Exception:
                        filtered_total += 1
                        filtered_reasons["invalid_json"] = filtered_reasons.get("invalid_json", 0) + 1
                        continue

                    mid = str(raw_msg.get("message_id") or "")
                    if mid and mid in existing_ids and not force:
                        continue

                    res = clean_message(raw_msg)
                    if res.cleaned is None:
                        filtered_total += 1
                        r = res.filtered_reason or "filtered"
                        filtered_reasons[r] = filtered_reasons.get(r, 0) + 1
                        continue

                    existing_ids.add(str(res.cleaned.get("message_id") or ""))
                    out_f.write(json.dumps(res.cleaned) + "\n")
                    cleaned_total += 1

        status["messages"] = {
            "total_cleaned_added": cleaned_total,
            "filtered_out": filtered_total,
            "filtered_reasons": filtered_reasons,
            "last_cleaned": _utc_now_iso(),
        }

    _save_cleaning_status(server_id, status)


def main():
    parser = argparse.ArgumentParser(description="Discord scraper pipeline (scrape + clean)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json")
    parser.add_argument("--cli-path", default=str(DEFAULT_CLI_PATH), help="Path to DiscordChatExporter.Cli binary")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scrape = sub.add_parser("scrape", help="Scrape raw messages + schematics")
    scrape.add_argument("--server", help="Only scrape this server_id")
    scrape.add_argument("--channel", help="Only scrape this channel_id")
    scrape.add_argument("--resume", action="store_true", help="Resume an interrupted scrape (default behavior)")
    scrape.add_argument("--force", action="store_true", help="Re-scrape even if marked complete")
    scrape.add_argument("--loop", action="store_true", help="Continuously iterate configured servers/channels")
    scrape.add_argument("--max-channels", type=int, default=0, help="Stop after N channels (0 = no limit)")
    scrape.add_argument("--max-messages", type=int, default=0, help="Stop after N new messages per channel (0 = no limit)")
    scrape.add_argument("--export-after", default=None, help="Pass-through to DiscordChatExporter --after (date or message ID)")
    scrape.add_argument("--export-before", default=None, help="Pass-through to DiscordChatExporter --before (date or message ID)")
    scrape.add_argument("--export-filter", default=None, help="Pass-through to DiscordChatExporter --filter (message filter syntax)")

    clean = sub.add_parser("clean", help="Validate schematics and clean messages")
    clean.add_argument("--server", help="Server ID to clean (default: all servers with raw data)")
    clean.add_argument("--force", action="store_true", help="Overwrite existing clean outputs")
    clean.add_argument("--messages-only", action="store_true", help="Only clean messages")
    clean.add_argument("--schematics-only", action="store_true", help="Only validate schematics")

    args = parser.parse_args()

    _migrate_legacy_dirs()

    config = _load_config(Path(args.config)) if args.cmd == "scrape" else {}
    rl_cfg = config.get("rate_limiting", {}) if isinstance(config.get("rate_limiting", {}), dict) else {}
    settings = ScrapeSettings(
        requests_per_second=float(rl_cfg.get("requests_per_second", 0.5)),
        signed_url_max_batches=int(rl_cfg.get("signed_url_max_batches", 200)),
        retry_on_429=bool(rl_cfg.get("retry_on_429", True)),
        max_retries=int(rl_cfg.get("max_retries", 5)),
        backoff_multiplier=float(rl_cfg.get("backoff_multiplier", 2)),
    )

    if args.cmd == "scrape":
        token = get_discord_token()
        exporter = DiscordChatExporter(Path(args.cli_path))
        exporter.verify_exists()

        servers = config.get("servers", []) if isinstance(config.get("servers", []), list) else []
        server_filter = args.server
        channel_filter = args.channel

        rate_limiter = RateLimiter(settings.requests_per_second)
        api = DiscordApi(
            token,
            rate_limiter=rate_limiter,
            retry_on_429=settings.retry_on_429,
            max_retries=settings.max_retries,
            backoff_multiplier=settings.backoff_multiplier,
        )

        _log("Scrape mode starting")
        channels_seen = 0
        while True:
            for s in servers:
                if not isinstance(s, dict):
                    continue
                if not s.get("enabled", True):
                    continue
                server_id = str(s.get("server_id") or "")
                if not server_id:
                    continue
                if server_filter and server_id != server_filter:
                    continue

                channels_cfg = s.get("channels", "all")
                channel_ids: list[str] = []
                if channel_filter:
                    channel_ids = [str(channel_filter)]
                elif channels_cfg == "all":
                    try:
                        _log(f"Listing channels for server_id={server_id}")
                        chs = api.list_guild_channels(server_id)
                        _log(f"Discovered {len(chs)} channel(s) for server_id={server_id}")
                        cats_filter = s.get("categories_filter")
                        if isinstance(cats_filter, list) and cats_filter:
                            category_id_to_name: dict[str, str] = {
                                c.id: c.name for c in chs if c.type == 4 and c.id and c.name
                            }
                            allowed_category_names = {str(x) for x in cats_filter}
                            allowed_category_ids = {
                                cid for cid, nm in category_id_to_name.items() if nm in allowed_category_names
                            }
                            channel_ids = [
                                c.id
                                for c in chs
                                if c.id and c.type in SCRAPABLE_CHANNEL_TYPES and (c.parent_id in allowed_category_ids)
                            ]
                        else:
                            # Only scrape text (0), forum (15), and thread (11) channels.
                            channel_ids = [c.id for c in chs if c.id and c.type in SCRAPABLE_CHANNEL_TYPES]
                    except Exception as e:
                        print(f"Failed to list channels for server {server_id}: {e}")
                        continue
                elif isinstance(channels_cfg, list):
                    channel_ids = [str(x) for x in channels_cfg]
                else:
                    print(f"Invalid channels config for server {server_id}. Use \"all\" or a list.")
                    continue

                for cid in channel_ids:
                    if not cid:
                        continue
                    try:
                        result = _scrape_channel(
                            exporter=exporter,
                            token=token,
                            server_id=server_id,
                            channel_id=cid,
                            settings=settings,
                            force=bool(args.force),
                            max_messages=int(args.max_messages or 0),
                            export_after=args.export_after,
                            export_before=args.export_before,
                            export_filter=args.export_filter,
                        )
                    except Exception as e:
                        err_msg = str(e)
                        if "forbidden" in err_msg.lower():
                            _log(f"Skipping channel {cid} (forbidden)")
                            continue
                        else:
                            raise
                    # Don't count skipped/forbidden channels toward max_channels
                    if result in ("complete", "partial", "error"):
                        channels_seen += 1
                    if args.max_channels and channels_seen >= args.max_channels:
                        _log(f"Reached --max-channels {args.max_channels}, stopping scrape pass")
                        break

                if args.max_channels and channels_seen >= args.max_channels:
                    break

            if args.max_channels and channels_seen >= args.max_channels:
                break

            if not args.loop:
                break
            _log("Looping enabled; sleeping 60s before next pass...")
            time.sleep(60)

    elif args.cmd == "clean":
        if args.server:
            server_ids = [str(args.server)]
        else:
            server_ids = sorted([p.name for p in RAW_MESSAGES_DIR.glob("*") if p.is_dir()]) if RAW_MESSAGES_DIR.exists() else []
            # Also include servers that only have schematics
            if RAW_SCHEMATICS_DIR.exists():
                for p in RAW_SCHEMATICS_DIR.glob("*"):
                    if p.is_dir() and p.name not in server_ids:
                        server_ids.append(p.name)

        for sid in server_ids:
            _clean_server(
                server_id=sid,
                messages_only=bool(args.messages_only),
                schematics_only=bool(args.schematics_only),
                force=bool(args.force),
            )
    else:
        raise RuntimeError(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
