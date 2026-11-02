# Non-Discord redstone / Minecraft build corpora (researched 2026-11-01)

Scope: legally downloadable sources of redstone contraptions or Minecraft builds, outside Discord.
Companion to `STATUS_2026-11.md` (we have 2,296 raw Discord `.litematic`, 242 after cleaning).

**Read this first — what was and was not verified**

- Verified by reading the repo/page or API: licence fields, file listings, stated formats, dataset cards. Quoted in the tables below.
- Verified reachable from this machine: see section 4. The bash sandbox has a domain allowlist; only `api.github.com` and `github.com` are reachable from `curl`. Other hosts were checked through the `fetch_content` tool, which is a different path and not the same network egress.
- **Not verified:** total sizes of most corpora, and whether bulk download is allowed by each host's ToS. Only robots.txt was read for a few hosts. Nothing was downloaded.
- Licence and ToS for an individual `.litematic` file are **not** the same thing as the licence of the repo or hosting site. A file uploaded by a player is usually "all rights reserved by the author" unless the author says otherwise. Treat "repo has CC0" as the licence for the repo's own contents, and check per-author for the rest.
- Search: Brave is not configured on this machine. `web_search` via DuckDuckGo failed; Exa worked. Coverage is therefore narrower than a full search sweep. Items marked "lead" were seen in search results only.

---

## 1. Candidate sources — table

Priority: P1 = fetch and evaluate next; P2 = maybe useful; P3 = skip / low value. Scores are for our purpose (redstone contraptions with a litematic-compatible format, plus licence-clean use for training).

| # | Source | URL | Formats | Size / count | Licence (repo or host) | ML use allowed? | Metadata with files | Access notes | Priority |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Scorpio-File-Downloads** (DuskScorpio) | https://github.com/DuskScorpio/Scorpio-File-Downloads | `.litematic`, `.zip` (mostly farms, some redstone-ish: Sand Duper, TNT dupers, wither switches) | ~125 MB repo; roughly 100+ `.litematic` (estimated from the tree listing, not counted) | **CC0-1.0** (repo LICENSE, GitHub API spdx) | Yes under CC0 (no restriction). Caveat: the files are the author's work; CC0 on the repo is what the author stated. | Folder names + `Rates.xlsx`/`Construction Guide` docx for a few; otherwise filenames only | Standard git clone (`git clone --depth 1`). No API limit issue for clone. | **P1** (licence clean, real builds, mostly farms → low redstone density) |
| 2 | **Redstone Circuits Library** (TRS, theredstonee.de) | https://trs-launcher.theredstonee.de/circuits | Structure file download (`.nbt`-type per the page; not confirmed) + 3D preview | Unknown (site lists "proven" circuits; count not read) | **Not stated** on the page (no licence text found in readable fetch) | Unknown — needs an explicit licence email | Yes: 3D preview, material list, per-circuit text description (NOT gate, etc.) | Site is JS-heavy; the circuit list endpoint was not probed. | **P1 for metadata/descriptions, P2 for files** — descriptions are exactly what we lack; licence must be asked |
| 3 | **rom1504/minecraft-schematics-dataset** | https://github.com/rom1504/minecraft-schematics-dataset (data on GitLab: https://gitlab.com/rom1504/minecraft-schematics-dataset) | TFRecord shards of parsed schematics + `schematicsWithFinalUrl.json` (per-file source URL) | `download.sh` fetches `schematics_0.tfrecords` … `schematics_36000.tfrecords` (~37 shards); total size **not verified** | **No licence file** in repo (GitHub API: `license: null`). Last push 2021-01-31 | **Unclear.** Redistributing a scrape of others' builds without a licence is a gray area. Ask/treat as internal-only | `schematicsWithFinalUrl.json` gives source URLs, which lets us recover the original titles/authors | Shards are on GitLab, not GitHub; **gitlab.com not reachable from this sandbox**. Format is TFRecord (needs a reader; `pyReader/` notebook in repo) | **P1 (high value if licence allows); licence blocker** |
| 4 | **Hack337/Minecraft-Schematics** (HF dataset) | https://huggingface.co/datasets/Hack337/Minecraft-Schematics | `.schem`, `.litematic`, `.schematic` raw; plus `blueprints/` JSON grid; `dataset_openai.jsonl` | 669 curated builds; 1,338 chat rows; 922,963 non-air blocks. Mostly houses/landmarks, **not redstone** | **Not read on the card** (licence tag not seen in fetched text) | Unknown until licence tag confirmed | Yes: `schematics_mapping.json` (titles + descriptions), `blueprints_mapping.jsonl` (EN/RU queries + reasoning) | HF reachable via `fetch_content`; not via sandbox | **P2** (metadata nice; not redstone; the synthetic `<think>` text is generated, not ground truth) |
| 5 | **TheAIdude303/Minecraft-Fable-Schem-final** (HF) | https://huggingface.co/datasets/TheAIdude303/Minecraft-Fable-Schem-final | Schematic files (format not confirmed) | 5.46 GB total; 225 downloads last month; no dataset card | **No licence stated** (no card) | No | None known | Fetched content incomplete | **P3** (no card, no licence, unknown content) |
| 6 | **ReigeKwitt/MinecraftWorldFiles** (HF) | https://huggingface.co/datasets/ReigeKwitt/MinecraftWorldFiles | World/schematic folders (community builds, e.g. "1500_west_georgia…", "Castlevania…") | Unknown; 10 commits | **Not read** | Unknown | Folder names only | Fetched listing only | **P3** (mixed community builds; licence unknown) |
| 7 | **unfundedResearcher/Minecraft-GLB2Schem-RepairPairs-v1** (HF) | https://huggingface.co/datasets/unfundedResearcher/Minecraft-GLB2Schem-RepairPairs-v1 | WebDataset TAR shards of `.input.schem` (noisy voxelisation) + `.target.schem` (original) | Unknown count | **Not read** | Unknown | Yes (generated input vs ground truth) | Not verified | **P3** (not redstone; the "repair" framing is interesting but about voxels, not circuits) |
| 8 | **Kermitkiller666/Minecraftbuilding2** (HF) | https://huggingface.co/datasets/Kermitkiller666/Minecraftbuilding2 | Parquet: `schematic`, `focus`, `block_count`, `dimensions`, `instruction`, `input` (large), `output` | Rows unknown; sample shows `grandwoodenmansionfor12players.schem` | **Not read** | Unknown | Yes: instruction + structure text | Not verified | **P2** (already in instruction-tuning shape; check licence first) |
| 9 | **ghosteau/minecraft-chunks** (HF) | https://huggingface.co/datasets/ghosteau/minecraft-chunks | CSV, one row per voxel (x,y,z,block) | 2,040 full chunks, ~200M voxels; **vanilla 1.21** | **MIT** (HF tag) | Yes | Schema documented | HF reachable via fetch | **P2** (natural terrain, not builds; useful for voxel model pretraining, not redstone) |
| 10 | **liguang0115/overseer-minecraft** (HF) | https://huggingface.co/datasets/liguang0115/overseer-minecraft | video + CSV (multi-view, 1.21) | 10K–100K size category | **Apache-2.0** (HF tag) | Yes | Yes (action-conditioned) | Not relevant to redstone | **P3** (gameplay video) |
| 11 | **Nininkkka/Minecraft-vision-dataset** (HF) | https://huggingface.co/datasets/Nininkkka/Minecraft-vision-dataset | Screenshots | 10K–100K images | **CC-BY-NC-4.0** | **No for commercial**; NC restricts | Class folders | — | **P3** (not relevant; NC) |
| 12 | **MinecraftU/mcu-curriculum** (GitHub) | https://github.com/MinecraftU/mcu-curriculum | Unknown (repo 616 MB) | — | NOASSERTION | Unknown | — | Not read | **P3** (curriculum, not builds) |
| 13 | **Piscescup/MC-Java-Schematics** (GitHub) | https://github.com/Piscescup/MC-Java-Schematics | `.litematic` (Chinese-named; storage, mob, "全物品" (all-items) sorters, raid farms) | ~103 MB repo; count not counted; mostly farms/storage | **No licence file** (GitHub: `license: null`) | No, without asking | Filenames carry Chinese notes, no sidecar | Clone fine | **P2** (real redstone-heavy sorters/item systems; licence blocker) |
| 14 | **Leroymilo/Litematicas** (GitHub) | https://github.com/Leroymilo/Litematicas | `.litematic` (e.g. `redstone/2w_ABtileable_redcoder.litematic`, `Smelter_V2`, `hidden_staircase`) + `description.md` per folder | Tiny (7 KB repo, so the `.litematic` files are LFS or the listing is partial — **unverified**). README: "a collection … feel free to download and use" | **No licence file**; README says "feel free to download and use" (permission, not a licence) | Permission in README covers use, but not explicitly ML. Ask | Yes: `description.md` per project | Repo cloned via `fetch_content`; files visible in tree | **P1 (small, has descriptions, explicit permission)** — but tiny |
| 15 | **Squishy6094/litematica-schematics** (GitHub) | https://github.com/Squishy6094/litematica-schematics | "Files for Redstone Projects" (per repo description) | 1 KB repo (size reported by API is metadata only; **files not confirmed**) | **No licence file**; pushed 2026-06-04 | Ask | Unknown | Repo page reachable (`github.com` 200) | **P2** (redstone-specific; needs a look at its tree) |
| 16 | **formalized-si/redstone-verify** (GitHub) | https://github.com/formalized-si/redstone-verify | Layouts + certificates (SAT, LRAT, Lean 4), Verilog specs | 13.5 MB repo | **MIT** (GitHub API) | Yes (MIT) | Verilog specs + proofs → **machine-checkable ground truth** | Repo reachable | **P1** (verified redstone layouts with specs: rare and exactly our bottleneck; check whether layouts are from a simulator or from the game) |
| 17 | **Dhanika-Botejue/rouge** (GitHub) | https://github.com/Dhanika-Botejue/rouge | Redstone build library in `src/main/resources/r…` (Fabric mod, JSON/nbt not confirmed) | 394 KB repo | **CC0-1.0** | Yes | Per-step descriptions (step-by-step build guide) | Repo reachable | **P1** (small, CC0, step-by-step; check format) |
| 18 | **Nekochu/Gemma-4-31B-RedstoneDoor** (HF model + dataset) | https://huggingface.co/Nekochu/Gemma-4-31B-RedstoneDoor | `dataset_alcapa_redstone_door.json` (216 SFT samples: 13 door commands + 203 Q&A) | 216 samples | **Not read** | Unknown | Q&A text | Not verified | **P2** (the Q&A about redstone mechanics could seed retrieval; the door commands are a 2x2 door only) |
| 19 | **Litematica-related OSS** (lead) | e.g. https://github.com/SandroHc/schematic4j (MIT), https://github.com/SmylerMC/litemapy (GPL-3.0) | Parsers, not data | — | MIT / GPL-3.0 | — | — | — | Tooling only; note for parsing, not corpus |
| 20 | **CraftAssist** (GitHub, archived) | https://github.com/facebookresearch/craftassist | Human house-building action logs (2,586 houses per the arXiv abstract); data links inside repo | 34 MB repo | **MIT** (GitHub API) | Yes (MIT) | Labels for house sub-parts (walls, roofs), natural-language commands | Repo archived 2020-11-09; data download via links inside — **not verified whether those links still work** | **P2** (non-redstone; sub-part labels are useful for a component vocabulary) |
| 21 | **3D-Craft dataset** (Chen et al., ICCV 2019) | https://openaccess.thecvf.com/content_ICCV_2019/html/Chen_Order-Aware_Generative_Modeling_Using_the_3D-Craft_Dataset_ICCV_2019_paper.html | Human house-building sequences (3D-Craft) | Not read | Not read (check the project page) | Unknown | Build order | Not verified | **P3** (houses, not redstone) |
| 22 | **Construction-Planning Models in Minecraft** (Zenodo) | https://doi.org/10.5281/zenodo.3239242 | Benchmark planning models | Not read | Zenodo record (licence not read) | Unknown | Planning models | Zenodo API reachable via fetch (200) | **P3** (planning, not builds) |
| 23 | **Minecraft Builds Dataset** (Kaggle, shauncomino) | https://www.kaggle.com/datasets/shauncomino/minecraft-builds-dataset | Self-described as "synthetically generated" | "40,000 player-created buildings" as claimed, **but labelled synthetic** | Not read (Kaggle page JS-rendered) | Unknown | README | — | **P3** (synthetic; claims conflict) |
| 24 | **Minecraft Schematics (Minecraft-Schematics.com)** | https://www.minecraft-schematics.com/ | `.schematic`/`.litematic` downloads | Large (site claim not verified) | **Terms page returned 403** to our fetch | Unknown; ToS not read | Yes (page descriptions) | Blocked by site to our fetch; robots.txt not readable | **P2 (lead only)** — scraping likely needs permission |
| 25 | **MineSchematic** (mineschematic.com) | https://mineschematic.com/c/redstone | Litematica files, 3D preview | Unknown | Not read (no terms page found: `/terms` 404) | Unknown | Yes (title/description per page) | `robots.txt` read: `User-agent: *`, `Allow: /`, disallows `/api/`, `/admin/`, `/settings`, `/setup`, `/auth/`. Sitemap at `/sitemap.xml` | **P2** — robots allows crawling, but ToS unknown; ask first |
| 26 | **PlanetMinecraft redstone schematics** | https://www.planetminecraft.com/projects/tag/redstone/?share=schematic | Schematic downloads (per project) | Large | Not read (ToS 403) | Unknown | Yes (project descriptions) | `robots.txt` returns a Cloudflare challenge page to our fetch — **not readable** | **P2 (lead only)** — ToS and robots both unverified |
| 27 | **Modrinth / CurseForge schematic categories** | e.g. https://modrinth.com/api/v2/search (API) | Mostly mod-related; schematic category sparse | — | — | — | — | Not reachable (sandbox); robots.txt 35 chars | **P3** |
| 28 | **Minecraft Wiki redstone circuits** | https://minecraft.wiki/w/Redstone_circuits | Prose + images; text under CC BY-NC-SA | Large text corpus | **CC BY-NC-SA 3.0** (Fandom wiki default; not checked per page) | **NC — not for commercial training**; attribution + share-alike apply | Yes (prose descriptions) | `robots.txt` is 42 KB and was fetched (not analysed) | **P2 for knowledge/retrieval** — NC licence matters |
| 29 | **Reddit r/redstone / r/technicalminecraft** | https://www.reddit.com/r/redstone | Posts, comments, images | Large | Reddit User Agreement; API terms restrict bulk use | **No** without a licence/API agreement | Yes | `robots.txt` on reddit.com returned a generic welcome page to our fetch — **not fully read**; treat as restricted | **P3 (legal risk)** |
| 30 | **YouTube tutorial transcripts** | https://www.youtube.com/ | Auto-captions (not bulk-downloadable under YouTube ToS) | Unknown | YouTube ToS: no bulk scraping | **No** (ToS); some creators' transcripts are fine via their own licence | Titles + captions | `robots.txt` read: `Disallow: /api/`, `/get_video_info`, `/timedtext_video`, etc. Bulk transcript download via `timedtext` is disallowed | **P3** (legal + technical) |

## 2. Dataset-level notes for HuggingFace/Kaggle/Zenodo searches

Searched via `fetch_content` on the HF datasets API (`/api/datasets?search=minecraft`) and via `web_search` (Exa). Only the entries above surfaced with clearly Minecraft-build content. No dataset surfaced with **redstone-specific, licence-clean, bulk-downloadable, with descriptions**. The closest matches in order:

1. `formalized-si/redstone-verify` (GitHub, MIT) — verified layouts with specs; the one worth examining first.
2. `DuskScorpio/Scorpio-File-Downloads` (GitHub, CC0) — real litematics, mostly farms.
3. `Dhanika-Botejue/rouge` (GitHub, CC0) — step-by-step redstone build library.
4. `Leroymilo/Litematicas` (GitHub, README grants use, no licence) — small, has descriptions.
5. `rom1504/minecraft-schematics-dataset` (GitLab shards, no licence, ~37 shards) — large, unclear licence; this is the biggest candidate in count and the one that needs a licence answer.

Items I could not check: HF dataset licence tags for most of the Minecraft datasets (the API list showed tags only for the ones above), total sizes of GitLab shards, and any Kaggle/Zenodo redstone-specific dataset (none surfaced).

## 3. Licence / ToS summary for bulk download and ML use

| Tier | Sources | Notes |
|---|---|---|
| Clean (CC0 / MIT / Apache-2.0 / MIT) | Scorpio-File-Downloads (CC0), rouge (CC0), redstone-verify (MIT), CraftAssist (MIT, archived), ghosteau/minecraft-chunks (MIT), overseer-minecraft (Apache-2.0) | Usable for training, redistribution keeps the licence |
| Permission in README, no licence | Leroymilo/Litematicas | Ask the author for an explicit ML-use line before training |
| No licence | rom1504 dataset, Piscescup/MC-Java-Schematics, Squishy6094/litematica-schematics, TheAIdude303, Kermitkiller666 (not read), ReigeKwitt (not read) | Treat as not licensed: internal evaluation at most, unless the author says otherwise |
| Non-commercial | Nininkkka vision dataset (CC-BY-NC-4.0), Minecraft Wiki (CC BY-NC-SA) | NC blocks commercial use; check our intended use |
| Site ToS restricted | PlanetMinecraft (403 to fetch), minecraft-schematics.com (403), MineSchematic (robots allows crawl, ToS not read), Reddit (API/ToS), YouTube (ToS + robots disallow timedtext) | Do not bulk-download without written permission |

Per-file caveat: the Scorpio and rouge CC0 grants are the repo owner's grant. Files a player uploaded to a third-party site are not covered.

## 4. Reachability tests (this machine, 2026-11-01)

Method: `curl -I` (HEAD) with a polite User-Agent (`mira-research-bot/0.1`), one request per host, 1 s spacing. Sandbox has a domain allowlist; blocked hosts are reported as blocked, not as HTTP failures. `fetch_content` (different path) used for the rest, GET, read once.

| URL | Result | How |
|---|---|---|
| https://api.github.com/search/repositories?… | **200** (but 403/rate-limited on a later query: unauthenticated limit of 10 search/min) | curl |
| https://github.com/Squishy6094/litematica-schematics | **200** | curl |
| https://github.com/Dhanika-Botejue/rouge | **200** | curl |
| https://api.github.com/repos/… (licence, size) | **200** | curl |
| https://api.github.com/repos/rom1504/minecraft-schematics-dataset/contents/download.sh | **200** (decoded to read shard URLs) | curl |
| https://raw.githubusercontent.com/… | **blocked by sandbox allowlist** | curl (error) |
| https://gitlab.com/… | **blocked by sandbox allowlist** (fetch_content returned the GitLab page) | curl (error); fetch_content |
| https://huggingface.co/… | **blocked by sandbox allowlist** via curl; **reachable via fetch_content** (API list 200) | curl (error); fetch_content |
| https://zenodo.org/api/records?… | **blocked by sandbox allowlist** via curl; **reachable via fetch_content (200)** | curl (error); fetch_content |
| https://modrinth.com/… | **blocked by sandbox allowlist** via curl; robots.txt 35 bytes via fetch | curl (error); fetch_content |
| https://www.planetminecraft.com/… | **blocked by sandbox allowlist** via curl; **Cloudflare challenge** via fetch_content | curl (error); fetch_content |
| https://minecraft-schematics.com/ and /terms | **403** via fetch_content | fetch_content |
| https://mineschematic.com/robots.txt | **200** (Allow: /) | fetch_content |
| https://minecraft.wiki/robots.txt | **200** (42 KB, not analysed) | fetch_content |
| https://www.reddit.com/… | **blocked by sandbox allowlist** via curl; robots.txt returned a generic welcome page via fetch | curl (error); fetch_content |
| https://www.youtube.com/robots.txt | **200** (rules read, see row 30) | fetch_content |
| https://www.kaggle.com/… | Page is JS-rendered; not readable via fetch | fetch_content |

Practical result: **from the sandbox, only GitHub is reachable.** Anything on HF, GitLab, Zenodo, or the schematic sites needs either the fetch tool (read-only, GET of a page) or a whitelist change. For bulk download we need the HF/GitLab hosts allowlisted.

Note: the GitHub unauthenticated search limit was hit during this run (`API rate limit exceeded`). Repo-level calls still succeeded.

## 5. Recommended next steps (for the user to decide; nothing was downloaded)

1. **Licence-clean and immediately usable (P1):** `Scorpio-File-Downloads` (CC0), `rouge` (CC0), `redstone-verify` (MIT). Clone with `git clone --depth 1` into a new empty directory outside the repo; do not run any `.py` from inside.
2. **Ask for permission (P1, high value):** `rom1504/minecraft-schematics-dataset` (~37 TFRecord shards, `schematicsWithFinalUrl.json` gives source URLs), `TRS redstone circuits` (descriptions + structure files), `Leroymilo/Litematicas` (ask for an explicit ML line), `Squishy6094/litematica-schematics`.
3. **Metadata-only leads (P2):** Hack337 (669 builds with EN/RU titles and descriptions), Kermitkiller666 (instruction + structure pairs), Nekochu Q&A (216 rows).
4. **Need the sandbox allowlist extended** (HF, GitLab, Zenodo, raw.githubusercontent) before any bulk pull. This is a change to our own setup, so it is up to the user; I did not modify anything.
5. **Do not pursue without permission:** PlanetMinecraft, minecraft-schematics.com (403), Reddit, YouTube transcripts.

## 6. Priority score summary

Scale: 1 = do not pursue, 5 = fetch now. Weighted for: redstone density, licence cleanliness, descriptions present, scale.

| Source | Redstone density | Licence | Descriptions | Scale | Score |
|---|---|---|---|---|---|
| formalized-si/redstone-verify | 5 (verified layouts + specs) | 5 (MIT) | 4 (specs) | 2 | **5** |
| Scorpio-File-Downloads | 3 (farms, some redstone) | 5 (CC0) | 2 | 3 | **4** |
| rouge | 4 (step-by-step builds) | 5 (CC0) | 5 | 1 | **4** |
| TRS redstone circuits | 5 | 2 (not stated) | 5 | 2 | **4** (if licence obtained) |
| rom1504 dataset | 2 (mixed) | 1 (none) | 3 (source URLs) | 5 (~37 shards) | **3** (licence-gated) |
| Leroymilo/Litematicas | 4 | 3 (permission) | 4 | 1 | **3** |
| Piscescup/MC-Java-Schematics | 3 (sorters, storage) | 1 (none) | 1 (Chinese filenames) | 3 | **2** |
| Hack337 HF | 1 (houses) | ? | 4 | 3 | **2** |
| CraftAssist | 1 | 5 (MIT) | 3 (labels) | 2 | **2** |
| Minecraft Wiki | 3 (prose) | 2 (NC) | 4 | 4 | **2** (retrieval only) |
| MineSchematic / PlanetMinecraft / minecraft-schematics.com | 5 | 1 (ToS unread / 403) | 4 | 5 | **2** (lead, not corpus) |
| Reddit / YouTube | 3 | 1 | 3 | 5 | **1** |

## 7. Residual risks

- Licence reading is from the repo or card only; per-file author rights are not checked (see section 3).
- Several sizes and file counts are estimates from listings, not measured. The GitLab shard sizes are unknown.
- Search was limited (no Brave; DuckDuckGo failed; Exa only). Some relevant HF datasets may be missing.
- The `formalized-si/redstone-verify` layouts were not inspected; whether they come from a real game oracle or a simulator is unknown and matters for our use.
- `Leroymilo/Litematicas` shows 7 KB total, which suggests the `.litematic` files may be stored elsewhere (LFS or a different repo). Not verified.
- `TRS` circuit file format and count not checked; the page is JS-heavy.
