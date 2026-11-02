# Discord scrape runbook

State (2026-11-02): the token in `../.env` returns 401 (expired/revoked). Nothing here has been run against more than TMC Catalogue, which was scraped earlier (2296 raw `.litematic`).

## Before you run anything
- Discord's ToS forbids automating a user account. Use a dedicated account, not your main. The scraper is deliberately slow (about 1 request / 2 s, backoff on 429), but the risk is the account's, not the code's.
- Only scrape servers the account has already joined. Do not automate joining servers (that is the classic ban trigger); join by hand.
- Read each server's rules channel. Skip servers that forbid collection (Open Redstone Engineers does). Prefer published archives over scraping where they exist (Storage Catalog -> `Storage-Tech-2/Archive` on GitHub).

## Steps
```bash
cd discord_scraper
# 1. fresh token from the browser (Network tab -> any discord.com/api request -> Authorization header)
export DISCORD_TOKEN='...'            # or put it in ../.env as `export DISCORD_TOKEN=...`
# 2. see which servers the account is in and what channels they have (read-only, ~3 s per request)
python3 discover_guilds.py            # -> data/discovery/guilds.json
# 3. enable servers
cp config.json config.local.json      # add entries from config.candidates.json (match server_id against guilds.json), set "enabled": true
# 4. small trial first
python3 export_discord.py --config config.local.json scrape --server <id> --max-channels 3
# 5. full run, resumable and idempotent
python3 export_discord.py --config config.local.json scrape --server <id>
# 6. index everything (new corpus tooling)
cd .. && .venv/bin/python scripts/corpus_index.py
```
Per-server `channel_types` selects what is scraped: 11 threads, 15 forums (default), 0 text, 5 announcements.
