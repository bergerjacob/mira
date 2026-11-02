# Data sources (2026-11-02)

What we hold, where it came from, what we may do with it. Machine-readable twin: `data/external/MANIFEST.json`
(written by `scripts/corpus_fetch.py`: URL, commit, licence, date, file counts, ML-use status). Everything under
`data/` is gitignored. Numbers about the content are in `docs/CORPUS_REPORT.md` (`scripts/corpus_index.py`).

ML-use status: **clean** = permissive licence (CC0 / MIT / Apache-2.0) · **permission** = no licence but the
owner's text allows use, or an explicit licence with conditions (attribution / share-alike) that need a decision
before training · **internal-only** = evaluation, retrieval and module-library research only, never training data we
publish or ship.

Downloaded files are untrusted data. They were only cloned (`git clone --depth 1`, LFS not pulled), listed and
parsed with Nucleation / litemapy / nbtlib / `yaml.safe_load` in worker processes; nothing from them was executed.

## 1. Downloaded (data/external/)

| dir | upstream | licence (verified file) | ML use | commit | content |
|---|---|---|---|---|---|
| `scorpio-file-downloads` | github.com/DuskScorpio/Scorpio-File-Downloads | CC0-1.0 (`LICENSE`) | clean | c395616 | 86 `.litematic` (farms, dupers, loaders), 34 `.zip` (worlds/guides, not extracted), 2 docx, 1 xlsx |
| `rouge` | github.com/Dhanika-Botejue/rouge | CC0-1.0 (`LICENSE`) | clean | 8a6044b | 67 circuit JSONs in `src/main/resources/rouge/circuits` with step-by-step prose; **37 carry geometry, 30 are description-only**. The repo is a Fabric mod (Java), not used |
| `redstone-verify` | github.com/formalized-si/redstone-verify | MIT (`LICENSE`); `NOTICE`: third-party layouts keep their own licences (REDA MIT, Redstone-Compiler MIT, rtl2mc Apache-2.0); `paper/` not covered | clean | 0fbd001 | 43 circuits with Verilog `spec.v` + 178 `.rsl` layouts (62 ACCEPT, 116 refuted mutants), 26 REDA `.litematic`, 483 `.nbt` (5 rtl2mc, 475 Redstone-Compiler candidates/tiles), certificates, Lean proofs |
| `claudestone` | github.com/Screendead/claudestone | MIT (`LICENSE`) | clean (27 `ref_*` specs: permission) | 5844e07 | 457 YAML specs (palette + layers + truth tables / waves / delays / throughput tests) tested tick-by-tick on a vanilla Java 26.3 server; designed by Claude agents |
| `redstone-cpu` | github.com/qbradq/redstone-cpu | MIT | clean | 6954789 | 24 `.schem` of one 1.21+ redstone CPU |
| `redstone-machine-schematics` | github.com/FlyDogDaDa/Redstone-Machine_Schematics | Apache-2.0 | clean | 30cfb55 | 1 `.litematic` |
| `redstone-schematics-nacl` | github.com/NaCl-5844/redstone_schematics | MIT | clean | 257f895 | a world folder (`.mca`, not indexed) + 1 `.schem` |
| `minecraft-computers` | github.com/HashDG/minecraft-computers | CC-BY-SA-4.0 | **permission** (attribution + share-alike) | 28da925 | 2 `.schem` redstone computers |
| `litematicas-leroymilo` | github.com/Leroymilo/Litematicas | none; README: "Feel free to download and use them." | **permission** (`permission-readme`) | c3dded0 | 3 `.litematic` + `description.md` (redcoder module, hidden staircase, smelter) |
| `storage-catalog-archive` | github.com/Storage-Catalog/Archive (the repo previously at Storage-Tech-2/Archive) | none for the archive (`dictionary/` is GPL-3.0, unused); README: credit creators and link the repo | **permission** (`permission-readme`, attribution required) | d16aea6 | 327 `.litematic` + 400 JSON, 232 READMEs, 342 images; 25 `.zip` are LFS pointers (not pulled) |

Kept separate on purpose: Leroymilo and the Storage Catalog Archive (permission text, no licence, no explicit ML
clause; per-post authors are listed in `data.json`) and the CC-BY-SA repo. Ask before training on them.

### Notes per source

**redstone-verify — in-game or abstract? What specs does it carry?** Both layers exist.
- `.rsl` (the 62 ACCEPT + 116 REJECT layouts) is an abstract text grid (`docs/RSL.md`). The authors' tool `rsv` places it
  in an MCHPRS world "as a player would", reads out the redpiler graph and exports `.schem`; ACCEPT layouts are proved
  equal to `circuits/<name>/spec.v` (Verilog) via Yosys SAT + LRAT certificates + a Lean theorem and co-simulated on two
  MCHPRS backends (and, per their README, a vanilla 1.21.1 oracle). **Proof covers the steady state of acyclic layouts**;
  timing is only co-simulated; sequential circuits are 3 small examples. So the layouts were never hand-built in game,
  but they are game-faithful placements checked against formal specs.
- The 26 REDA `.litematic`, 5 rtl2mc `.nbt` and 475 Redstone-Compiler `.nbt` are outputs of three third-party
  Verilog-to-Minecraft compilers (the last are mostly search candidates / tiles, many near-duplicates). `entry.json`
  beside each names the spec.
- **Use:** `scripts/corpus_convert.py redstone-verify` turns every `.rsl` into a settled `.litematic` + sidecar with ports
  (levers / lamps), the `spec.v` text and the ACCEPT/REJECT label (REJECT mutants = ready-made negative / repair
  examples). `scripts/corpus_check_rsl.py` then brute-forces single-bit specs in Nucleation: **30/31 ACCEPT layouts
  reproduce the Verilog truth table, 7/7 REJECT mutants differ** (the one miss, `c17/cand_comparators`, is a lead for a
  Nucleation-vs-MCHPRS divergence or a conversion issue; not investigated). The vector specs (adders, ALUs, multipliers)
  need a bit-vector evaluator and were not checked.

**claudestone.** The best ground truth found: each YAML spec has the layout, named input / output cells and tests
(`truth_table`, `delay`, `wave`, `steps`, `level`, `throughput`). Tests ran on an unmodified vanilla 26.3 server through
the authors' harness. Converted 457/457 to settled litematics with sidecars (`scripts/corpus_convert.py claudestone`);
tests are kept verbatim in the sidecar for a spec adapter. Caveats: AI-designed, so "correct" means "passes its own
tests"; block-entity NBT in palette strings is dropped (`nbt_dropped`), entities are not converted; the 27 `ref_*` specs
rebuild other people's community designs, so they are indexed as `permission`, not `clean`.

**rouge.** CC0, but 30 of 67 entries have no geometry (prose only) and the 37 with geometry carry no verification;
authorship (hand vs LLM) is not stated. Useful as description + block-list pairs.

**storage-catalog-archive — schema.** `Archive/<CODE>_<category>/<CODE###_Title>/{data.json, README.md, attachments/,
images/, comments.json}`. `data.json`: `id, name, code, authors[], endorsers[], tags[{Functional, Recommended, ...}],
attachments[{name, path, litematic:{size, version}, hash, url}], images[], records:{description, features[],
considerations[], notes, compatibility, instructions[]}, references[] (dictionary terms), post:{threadURL, ...},
archivedAt, updatedAt`. So it has human descriptions and design features plus MC version and dimensions per
schematic, for ~327 designs (storage, loaders, sorters, autocrafting, clocks, tricks). Top-level `embeddings.json`,
`hnsw.idx` and `dictionary/` are search infrastructure (not used). Size 277 MB cloned (137 MB of files), well under 5 GB.

**Hack337/Minecraft-Schematics (HuggingFace) — not downloaded.** Read via `fetch_content` (huggingface.co is outside the
bash network allowlist): card says `license: mit`, 669 builds (houses / landmarks, "not redstone"), only ~64 raw
`.litematic` + 4 `.schem`, the rest converted JSON grids and synthetic `<think>` chat rows. The MIT tag is the uploader's
and there is no provenance statement for the underlying third-party builds, so the licence does not clearly cover the
files, and redstone value is near zero. Revisit only if we want house-style data.

## 2. Found and skipped

| source | reason |
|---|---|
| desquerteur/schematic-library (MIT repo, 100 litematic) | imports third-party builds that "remain with their original authors and licences" (THIRD_PARTY_ATTRIBUTION.md); mostly Scorpio duplicates |
| rom1504/minecraft-schematics-dataset, Piscescup/MC-Java-Schematics, Squishy6094/litematica-schematics, IfNullThenVoid, taetwomp | no licence (scraped or personal collections) |
| RedstoneTools/redstone-library (GPL-3.0), BEGINWITHF/Minecraft-schematics (GPL-2.0), zsoltfrks/schematics (GPL-3.0) | copyleft on data: unclear for training; skipped |
| Radomir-Aksenenko/code_to_redstone (MIT), AlexMelanFromRingo/RedstoneBuilder, saucefishman/MineRTL, pishleback/RedstoneSchematicGenerator, SomeKitten/RedstoneSchematic | compilers / tools with no schematic data; useful as references for HDL-to-redstone ideas |
| luanlai1/Minecraft-Redstone-Archive, boyninja1555/Minecraft-PCs (MIT) | websites; no schematics in the repo |
| TheAIdude303, Kermitkiller666, ReigeKwitt (HF) | no licence read / no card |
| nucleation_src/computational_schematics (inside the Nucleation checkout, another agent's) | 9 community computational-redstone `.schem` (8-bit adders, register file, divider...) with unknown provenance / licence; not indexed; worth asking the Nucleation authors |
| PlanetMinecraft, minecraft-schematics.com, Reddit, YouTube, ORE / Schemati, any Discord | restricted per `docs/research/corpora.md`; not accessed |

GitHub discovery was bounded: unauthenticated search over topics `litematica`, `redstone`, `schematic`,
`worldedit`, `minecraft-redstone`, `technical-minecraft` and free-text queries; only repos with a LICENSE file whose
content we read (via the API `/license` endpoint) were cloned. Coverage is not exhaustive (search results are capped,
queries short).

## 3. Network re-test (2026-11-02) — the research claims are only partly stale

- Shell tool egress is an allowlist: **github.com / api.github.com work** (clone and REST, polite delays);
  `raw.githubusercontent.com`, `gitlab.com`, `huggingface.co`, `zenodo.org` are refused by the shell tool (the call is
  blocked before it starts). `fetch_content` (read-only GET) still reaches HuggingFace and storagecatalog.org. So bulk
  downloads from HF / GitLab are still not possible from this machine without extending the allowlist, and anything
  under `rom1504` (GitLab shards, unlicensed anyway) stays out of reach.
- Corrections to `docs/research/corpora.md`: `Leroymilo/Litematicas` does hold 3 `.litematic` files (224 KB; no LFS);
  `Storage-Tech-2/Archive` is now `Storage-Catalog/Archive`; `redstone-verify` is 123 MB of mostly JSON/proof data,
  `rouge` is a mod plus 67 circuit JSONs (not 70 builds), `Dhanika-Botejue/rouge` licence CC0 confirmed.
- Discord was not touched; the scraped data in `discord_scraper/data` is the only Discord material and is
  `internal-only` (users' uploads, no licence).

## 4. Existing internal data

| source | files | ML use |
|---|---|---|
| `discord_scraper/data/raw_schematics` (TMC Catalogue) | 2296 `.litematic`, linked to `raw_messages` by filename (`<message_id>_<n>_<name>`) | internal-only |
| `discord_scraper/data/clean_schematics` | 242 (byte-identical subset of raw: all 242 sha256 appear in raw) | internal-only |
| `data/raw_schematics`, `simple_schematics`, `sliced_components` | 16 local test files | internal-only |

The Discord files are users' work posted in a community archive: no licence, so they stay internal evaluation /
retrieval material. For raw-vs-clean message keying: `raw_messages/<server>/<forum>/messages.jsonl`, one JSON per message,
`schematics[].filename` equals the file name on disk; the schematic message often has empty text and the title is the
thread name (`channel_name`), the description sits in the other messages of the same `channel_id` (the index stores
both).

## 5. Reproduce

```bash
.venv/bin/python scripts/corpus_fetch.py            # clone missing sources (throttled), rewrite MANIFEST.json
.venv/bin/python scripts/corpus_convert.py          # rouge / claudestone / redstone-verify .rsl -> data/corpus/converted
.venv/bin/python scripts/corpus_index.py            # data/corpus/index.jsonl, index_summary.json, docs/CORPUS_REPORT.md
.venv/bin/python scripts/corpus_check_rsl.py        # Nucleation vs Verilog on converted redstone-verify layouts
```

`pyyaml` is not in the repo venv; `corpus_convert.py` falls back to the system `python3` (`yaml.safe_load`) to read
the claudestone specs. Adding `pyyaml` to `requirements.txt` would remove that detour.
