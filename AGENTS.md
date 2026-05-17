# AGENTS.md — MIRA Project Guide

## Project Summary

MIRA (Minecraft Iterative Reasoning Agent) generates, builds, and debugs Minecraft Redstone circuits from text descriptions. Currently in Phase 4 (complete) → Phase 5 (training) transition. The one-shot generation pipeline is validated; the iterative verification loop is built but not yet wired into training.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup.py          # Downloads Fabric MC server + mods (Java 21 required)
bash start_server.sh     # Starts headless MC server, waits for RCON on port 25575
```

**Required env var:** `OPENROUTER_API_KEY` — all LLM calls go through OpenRouter. Scripts crash immediately without it.

**Minecraft server:** RCON on `localhost:25575`, password `mira`. The server dir (`simulation/server/`) is gitignored — run `setup.py` to populate it.

## Running Tests

No test runner (no pytest, no `__init__.py` packages). All tests are standalone scripts that must be run from the project root with the venv activated:

```bash
# No server needed — LLM-only validation
python3 evaluation/test_api.py
python3 evaluation/quick_test.py

# Requires OPENROUTER_API_KEY, costs API credits
python3 evaluation/test_complex_circuits_strategies.py
python3 evaluation/ultra_comprehensive_test.py

# Requires running MC server
python3 simulation/tests/test_integration.py
python3 simulation/tests/test_connection.py
```

## Import Convention

**No `__init__.py` files.** Every script manually adds the project root to `sys.path` before importing:

```python
sys.path.append(str(Path(__file__).parent.parent))          # from evaluation/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))  # from scripts/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))  # from simulation/tests/
```

All cross-package imports use absolute paths: `from simulation.llm_client import ...`, `from data_mining.parser import ...`. Do not add `__init__.py` or switch to relative imports without updating every call site.

## Architecture

```
simulation/          Core runtime — LLM client, MC bridge, replicator, Scarpet API
  llm_client.py      OpenRouter API wrapper (MODELS dict maps short names → full IDs)
  bridge.py           RCON ↔ Minecraft (host=localhost, port=25575, password=mira)
  replicator.py       Block list → Minecraft via RCON
  teacher_client.py   LLM prompt templates for verification contracts & deconstruction
  deconstructor.py    Reverse deconstruction pipeline (iterative block removal)
  dataset_generator.py Orchestrates parser → deconstructor → training data
  scarpet_scripts/mira_api.sc  In-game verification API (check_block, check_inv, check_entity)
  server/             Headless Fabric MC 1.21 server (gitignored, created by setup.py)

data_mining/         Schematic parsing & fault injection
  parser.py           .litematic → Python block lists
  converter.py        Schematic format conversion
  corruptor.py         Fault injection for repair training data

evaluation/          Testing infrastructure (no pytest — standalone scripts)
  test_circuits/      12 JSON circuit definitions (3–96 blocks)
  schemas/            JSON schemas for LLM structured output
  prompts/            System prompt templates
  results/            Test output (gittracked)
  archive/            Deprecated approaches — do not reuse

scripts/             Utility scripts (all need sys.path hack)
  dev_tools/          Debug/verification helpers

discord_scraper/     Discord data scraping (submodule: DiscordChatExporter)
  Has its own config.json and requirements — semi-independent

training/            Empty — Phase 5 target directory
agent/               Empty — Phase 6 target directory
```

## Key Conventions

- **LLM model short names:** Use keys from `OpenRouterClient.MODELS` dict (e.g. `"gemini-flash-lite"`, `"glm-5"`) — the client resolves them to full OpenRouter IDs.
- **Plan+Constraint prompt:** The validated generation strategy. Always include "EXACTLY N blocks" constraint. See `evaluation/test_complex_circuits_strategies.py` for the canonical prompt template.
- **Recommended model:** `google/gemini-3.1-flash-lite-preview` (short name: `"gemini-flash-lite"`), temperature 0.5, max_tokens 8192.
- **Scarpet verification:** All in-game checks go through `mira_api.sc` commands (`check_block`, `check_inv`, `check_entity`). Deploy via `scripts/deploy_scarpet.py`.
- **`evaluation/archive/` contains rejected approaches** (iterative generation, reasoning traces from complete circuits, server-based benchmarking). Do not reuse these patterns — see `docs/PAST_ATTEMPTS.md` for why they failed.

## Gotchas

- **`simulation/server/` is gitignored** — you must run `python setup.py` to download the Fabric server, mods, and create `server.properties`. Without it, any server-dependent test will fail.
- **Tests cost money** — `test_complex_circuits_strategies.py` and `ultra_comprehensive_test.py` make real API calls. `quick_test.py` and `test_api.py` are free/cheap.
- **No package structure** — don't try `pip install -e .` or `pytest`. Everything runs as scripts from project root with `sys.path` hacks.
- **`.litematic` and `.schem` files are gitignored** — test schematics live in `data/raw_schematics/` locally but aren't committed.
- **`data/` and `datasets/` are gitignored** — large data files stay local.
- **`discord_scraper/` has its own submodule** (`DiscordChatExporter`) — init submodules with `git submodule update --init`.