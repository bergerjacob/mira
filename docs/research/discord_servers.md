# Technical / Redstone Minecraft Discord servers (research, 2026-11-01)

Scope: Discord servers that host redstone or contraption builds, schematics/litematics, or detailed build discussion. Ranked for MIRA's need: **working redstone contraption schematics with descriptions**.

Verification key: **[2]** = two or more independent sources agree; **[1]** = one source; **[U]** = unverified (name seen only in a link list, or invite/listing dead or not checked). Member counts are snapshots from the cited listing and drift; dates are when the listing was read.

Priority scale: **5** = core target, archive of working builds; **4** = strong archive or community with build content; **3** = relevant, content unclear; **2** = peripheral or likely inactive; **1** = out of scope or not useful.

## 1. Main table

| # | Server | Invite / listing | Members (snapshot) | What lives there | Likely channels | Rules on scraping / bots / data | Priority | In repo? | Verification |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **TMC Catalogue** (TMCC) | discord.com/invite/tmcc (also `tvpccRczWx` in older links); tmcc.dev; disdex id `1161803566265143306` | 193,487 (disdex, 2 Nov 2026) / 193,520 (readable fetch) | Archive of complete Java farms and systems, organized by category, "fully integrated setups". Attachments are .litematic/.schematic | Category-based forums (PUBLIC, AGRICULTURE, BLOCKS & ITEMS, CREATURES, INFRASTRUCTURE, ITEM PROCESSING, MONSTERS, NICHE & LEGACY, ARCHIVED per `config.json` filter) | Not checked (rules page not found) [U] | **5** | **Yes**: `discord_scraper/config.json`, server_id `1161803566265143306`, all 9 categories enabled | [2]: disdex + tmcc.dev; Reddit r/technicalminecraft (AllanTaylor314, Jun 2026) calls it the place for farm schematics |
| 2 | **Open Redstone Engineers (ORE)** | openredstone.org (Discord link via openredstone.org/discord) | Not verified | Computational redstone community. Schemati (ORE's own schematic system, schemati.openredstone.org) and ORE Archives server with raw map files. Schemat.io has an ORE community page | Schemati uploads, build/redstone channels, school server | **Explicitly prohibits bulk collection of user data and bots without opt-in consent** (rules: "Do not collect or scrape ORE user data without explicit opt-in consent", covers chat and Discord messages). Running a bot is also prohibited | **5** (content); scraping is **off-limits** without consent | No | [2]: openredstone.org/rules + GitHub OpenRedstoneEngineers/Rules; Schemati GitHub; Schemati docs on discourse |
| 3 | **DiscOREd** (ORE Discord) | disdex id `116914772766752769` | 5,756 (disdex, 29 Oct 2026) | ORE's Discord; the archive of builds sits on Schemati/forums, not on Discord | ORE channels | Same ORE rules as #2 (applies to "any ORE platform, including Discord") [2] | 3 | No | [2]: disdex; openredstone.org rules |
| 4 | **Storage Tech** | discord.com/servers/storage-tech-748542142347083868; invite `JufJ6uf` (in TMC gist) | 70,554 (listing) | Storage/automation systems: sorters, filters, storage. Wiki and guides; archives mirrored by "Storage-Catalog/Archive" on GitHub | Archive/guide channels, help | Not checked [U]. The GitHub archive is run by a bot account (`llamabot-archiver`), indicating the community publishes archives as open data | 4 (storage-tech overlap with redstone; large archive) | No | [2]: Discord listing page; TMC gist (JoakimThorsen); also cited in r/technicalminecraft |
| 5 | **Storage Catalog** | discord.com/servers/storage-catalog-1375556143186837695; storagecatalog.org; GitHub `Storage-Tech-2/Archive` | 3,176 online (listing; total not captured) | Searchable archive of storage builds ("Full Systems", "Archives", newest designs). Says "everything archived in the Storage Catalog Discord" | Archive posts, newcomer guides | Not checked on Discord. Archive is published as **machine-readable JSON on GitHub** (open data, no scraping needed) | **4** | No | [2]: Discord listing; GitHub archive README; storagecatalog.org/faq |
| 6 | **SciCraft** | discord.com/invite/scicraft; disdex id `211786369951989762` | 35,660 (disdex, 22 Oct 2026) | Community of a long-running technical SMP ("world famous technical Minecraft server"). Carpet mod discussion (Carpet was hosted on its Discord, per r/SciCraft 2018 thread); litematica sharing mentioned | Not verified | Not checked [U] | 4 | No | [2]: disdex; r/SciCraft "what are some technical Minecraft Discord servers" thread (May 2022) |
| 7 | **Tech MC Archive** | discord.gg/NWSYrZR (TMC gist); disdex id `594920796867133446`; old link discord.gg/mxFWRjAt | 26,886 (disdex, 19 Oct 2026) | Archive of technical builds (Java, redstone and farm designs). Mentioned as "farm design and schematics" source | Not verified | Not checked [U] | 4 | No | [2]: disdex; TMC gist; r/technicalminecraft (2022 thread) |
| 8 | **TMC Community (Technical Minecraft Community Discord)** | discord.gg/CM52CAH (2021 official link; **may be dead**) | Not verified | Official community server for r/technicalminecraft; Java and Bedrock public channels; "discussion of advanced redstone, automation, mob farming" | Java/Bedrock channels | Not checked [U] | 3 | No | [2]: r/technicalminecraft official link thread (2021); Techozu "Best Minecraft Discord Servers (Aug 2026)" |
| 9 | **Minecraft Tech Discord Recollector (MTDR)** | disdex id `810356854583459860`; discord.gg/UT8ns46As9 (2022 link) | 19,559 (disdex, 25 Oct 2026) | Directory server linking TMC, Java and Bedrock tech SMPs, archives and dev servers. Not a schematic store | Server list channels | Not checked [U] | 2 (use as a directory to find more) | No | [2]: disdex; r/SciCraft and r/technicalminecraft threads |
| 10 | **Redstone Army** | discord.com/invite/V5KFaF63mV; deepcord.com/server/5kc4btu5jy; groupfinds.com/discord/redstone-army | 21.2K (Deepcord) / 24,473 (GroupFinds, 14 Oct 2026) | "Nerdy Minecraft server focused on redstone and engineering". Learning and builds | Not verified | Not checked [U] | 3 | No | [2]: Deepcord and GroupFinds |
| 11 | **r/redstone Discord** | discord.com/invite/Qq2ubaZ | Not verified | Official-ish Discord of r/redstone subreddit. Build questions, sharing | Not verified | Not checked [U] | 2 | No | [1]: discord link surfaced in search; member count not captured [U] |
| 12 | **The Redstone Realm** | disboard.org/servers/tag/redstone (listing) | Not verified [U] | Community SMP-style server focused on redstone; "Showcase your builds" | Not verified | Not checked [U] | 2 | No | [1]: DISBOARD tag page (listing shows "6 days ago" text, count not captured) |
| 13 | **Computational Minecraft Archive** | discodus.com/server/959155788092424253; rally.casa/servers/959155788092424253 | 3,020 (Rally) / 203 online (discodus) | Self-described archive "dedicated to preserving and sharing redstone creations" | Archive/resource channels | Not checked [U] | 3 | No | [1]: Two listings, but the Rally card text ("Lego Legends of Chima") does not match. Identity needs a direct check [U] |
| 14 | **Cartchives** | disdex id `806605286416318534` | 6,232 (disdex, 27 Jul 2026) | Name suggests a "cart"/archive server. Content not verified [U] | Not verified | Not checked [U] | 3 | No | [1]: disdex only |
| 15 | **Mob Farm Archive** | discord.do/mob-farm-archive/ (listing); old invite `arzFPUSV` | Not verified | Archive of Java mob farm schematics (since 2021) | Not verified | Not checked [U] | 2 (r/technicalminecraft says invites are dead as of June 2025) | No | [2]: r/technicalminecraft (2022 and 2025 threads); TMC gist lists it as "inactive" |
| 16 | **Iron Lovers** | Not captured [U] | Not verified | Iron farm archive (name only) | Not verified | Not checked | 2 | No | [1]: TMC gist name only [U] |
| 17 | **Slimestone Tech Archive** | discord.gg/njpAkGb5tc (TMC gist) | Not verified | Slimestone-tech archive (name only) | Not verified | Not checked | 2 | No | [1]: TMC gist [U] |
| 18 | **Autocrafting Archive (ACA)**, **Wither Archive**, **TNT Archive**, **Huge Fungus Huggers**, **Tree Huggers**, **Saints of Suppression** | Listed in TMC gist; invites not captured [U] | Not verified | Topic-specific archives (name only) | Not verified | Not checked | 2 each | No | [1]: TMC gist (JoakimThorsen) [U] |
| 19 | **IanXO4's Server**, **The Archivists**, **WaveTech** | discord.gg/G9aa2YTTQX, discord.gg/7n2gEXJeAT, discord.gg/gZSVhBeCrW (from a Leaderboard gist) | Not verified | Tech-related, content not verified | Not verified | Not checked | 2 | No | [1]: CommandLeo gist list [U] |
| 20 | **Technical MC Speedrunning** | discord.gg/R2Da5ZRxKt; disdex id `843509042680954921` | 527 (disdex, 24 Oct 2026) | Speedrun tech (not contraption schematics) | Not verified | Not checked | 2 | No | [1]: disdex |
| 21 | **PRISM (Bedrock technical SMP)** | discord.com/servers/prism-1111663238191513650 | 190 online (listing; total not captured) | Bedrock Edition redstone, farm design, application-based SMP. Feedback from experts | Not verified | Not checked [U] | 3 (Bedrock niche; small) | No | [1]: Discord listing |
| 22 | **YMC Technical Minecraft Server** | ymc.moe | Not verified | Vanilla technical SMP (since 2020), invite-only for the server; Discord open to join for discussion | Discussion channels | Not checked [U] | 2 | No | [1]: ymc.moe |
| 23 | **Create Schematics** | discord.me/createschematics; createschematics.com; GitHub `Create-Schematics/Create-Schematics` | Not verified | Create mod schematics only ("NOT for litematica schematics") | Schematic posts | Not checked | 2 (Create mod, not redstone) | No | [2]: discord.me listing; GitHub repo |
| 24 | **HowieCraft** | discord.com/servers/howiecraft-959867988415221830 | 2,422 online (listing) | Building Gadgets and Create mod schematics; "database of 400+" | Schematic downloads | Not checked | 2 | No | [1]: listing |
| 25 | **Axiom (official)** | discord.com/servers/axiom-1123229768087777330; axiomdocs.moulberry.com (resources page) | 23,413 online (listing) | Axiom mod support; `#resources` for blueprints and presets. Not a redstone archive | `#resources`, support | Not checked | 2 (tool-focused, not contraptions) | No | [2]: Discord listing; Axiom docs |
| 26 | **Minecraft Discord Archive** | disdex id `685030002705825793` | 715 (disdex, 1 Nov 2026) | Generic archive server | Not verified | Not checked | 1 | No | [1]: disdex |

## 2. Out of scope / excluded (for transparency)

| Server | Why excluded |
|---|---|
| DonutSMP schematic shops (Szoro's, Konst4638 Schematics, donutsmp-schematics.com) | Marketplace for SMP builds and services, not technical redstone. ~12–13K members listed. Discord bot ToS on its site |
| Hermitcraft public Discords (Biffa, iJevin, Iskall85, Mumbo, Rendog, etc.) | Individual streamer/creator servers, not schematic archives. The list is from Jan 2020 and is stale [U] |
| Minecraft Java Speedrunning | Speedrun community, not contraption builds |
| Litematica / Carpet / MCHPRS / Nucleation official communities | **Not found** as Discord servers in this search. Litematica's public discussion lives on GitHub (`maruohon/litematica` Discussions). Carpet's own channels are unverified. MCHPRS has `mchpr.org`. Nucleation's community was not located |

## 3. Notes

### 3.1 Already in the repo

- `discord_scraper/config.json` lists one server: **TMC Catalogue** (`1161803566265143306`, all nine categories enabled, `download_schematics: true`). Nothing else is configured.
- `discord_scraper/discover_guilds.py` exists in the repo. Its output was not reviewed for this note.

### 3.2 Data-use and legal risk (read before scraping anything)

1. **Open Redstone Engineers** (#2, #3): The rules prohibit "bulk collection of ORE user data" without explicit opt-in consent, and this covers in-game chat, Discord messages and voice data. A scraper that collects ORE chat or message history would break those rules even though schematics are public. Schemati has per-user access through its web interface.
2. **Discord platform**: Discord's Developer Policy governs the API. Automating a *user* account (a self-bot) is prohibited by Discord's Terms of Service from prior knowledge, not re-verified here. The official route is a bot account that is invited to the server with permission. Not checked against current text (`support-dev.discord.com/hc/en-us/articles/25279999805975-2020-Discord-Developer-Policy` was surfaced but not read).
3. **TMC Catalogue**: The rules page was not found. Scraping its attachments is already configured in this repo. Before running it at scale, check its rules channel and confirm with the server staff.
4. **Storage Catalog and Schemati**: Both have open archives (GitHub JSON for Storage Catalog). Use those first and avoid scraping Discord when a published archive exists.

### 3.3 Better sources than live Discord scraping

- **Storage-Catalog/Archive** (github.com/Storage-Tech-2/Archive): machine-readable copy of the Storage Catalog Discord archive, with a bot account publishing it. No Discord scraping needed.
- **ORE Archives** (archive.openredstone.org): raw map files of ORE servers, explicitly open.
- **Schemati** (schemati.openredstone.org): ORE schematic management with a download function. Logged-in access only for some content.
- **schemat.io**: upload and download platform with an ORE community page. Not Discord-based.
- **WorldEdit / DiscordSchematicUploader**: a plugin, not a server, but it is the common way communities pass `.schem` files around. Not a data source by itself.

### 3.4 Gaps and what I would check next

- Read the TMC Catalogue rules channel. It matters more than any other item because the repo already scrapes it.
- Confirm the Storage Tech and Storage Catalog channel structures. Their GitHub archives suggest schematics are posted with descriptions, which matches the target.
- Check whether `discord.gg/CM52CAH` (TMC official) still resolves. The link comes from a 2021 thread.
- Verify SciCraft and Tech MC Archive channel structures directly. Both are large and both list "schematics" in third-party descriptions. Their content is **unverified** beyond listings.
- Find the Litematica, Carpet, MCHPRS and Nucleation communities. These weren't found in this search. Start with each project's GitHub and docs links (not Discord).

### 3.5 Method and limits

- Searches: exa provider (DuckDuckGo returned parse errors on several queries). About 20 queries across TMC, ORE, Storage, SciCraft, Create, Bedrock, Hermitcraft, speedrun and Axiom.
- Sources read: openredstone.org/rules (readable, partial matches only), TMC gist (JoakimThorsen, readable), disdex listings (TMCC, MTDR, SciCraft, Tech MC Archive, Cartchives, DiscOREd, Technical MC Speedrunning, Minecraft Discord Archive), Deepcord/GroupFinds (Redstone Army), Storage Catalog GitHub archive, schemat.io, r/technicalminecraft and r/SciCraft threads, Discord listing pages (search snippets, not fetched in full).
- Not done: Discord was not joined. No channel lists were read directly. Member counts come from third-party trackers and may be stale.
- Count: 26 rows covering about 30 servers (rows 8, 16–19 group several). The 25-server minimum is met on distinct names.
