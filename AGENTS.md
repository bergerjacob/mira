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

The project uses **pytest** with a comprehensive test suite (1,116 tests). Tests are organized into unit, pipeline, integration, and stability categories.

```bash
# Full suite (requires running MC server for integration tests)
python -m pytest tests/ -v

# Unit + pipeline only (no server, no API key needed, ~14s, 401 tests)
python -m pytest tests/unit tests/pipeline -v

# Skip stability tests (long-running)
python -m pytest tests/ -m "not stability" -v

# Specific test file
python -m pytest tests/integration/test_redstone.py -v
```

**Test markers:** `@pytest.mark.server` (needs MC server), `@pytest.mark.api` (needs API key), `@pytest.mark.stability` (long-running), `@pytest.mark.slow`.

**Server tests auto-skip** when the MC server is down — no failures, just skips. Run `bash start_server.sh` first for full coverage.

**Test categories:**
- `tests/unit/` — Parser, corruptor, world_slicer, circuit_builder DSL, converter, LLM client mock, bridge mock, replicator mock, teacher_client mock (401 tests, no server)
- `tests/pipeline/` — Enrichment, deconstruction, repair, format conversion, dataset generator (119 tests, no server/API, uses mocks)
- `tests/integration/` — Block placement, redstone, Scarpet API, entities, containers, comparators, observers, pistons, droppers/dispensers, hoppers, buttons/plates, circuit types, negative circuits, fake model output, replicator edge cases (378 tests, needs server)
- `tests/stability/` — High-iteration stress tests: 100x placement, 50x lever toggle, 30x build+rebuild, etc. (420 tests, needs server, ~10 min)
- `tests/fixtures/circuits/` — 14 JSON circuit definitions with machine-executable verification specs

**Key test infrastructure:**
- `tests/conftest.py` — Root fixtures, block state utils, `make_litematic` helper
- `tests/integration/conftest.py` — `VerificationContext` (the `ctx.set_block/tick/assert_block/assert_power` glue layer), `test_region` fixture (coordinate isolation, auto-cleanup, forceload), `build_circuit`/`build_and_verify` fixtures
- `tests/helpers/circuit_builder.py` — DSL for building block lists programmatically
- `tests/helpers/mock_rcon.py` — Fake RCON server for bridge unit tests

**Legacy standalone test scripts** (still in `evaluation/` and `scripts/`, not part of pytest suite):
```bash
python3 evaluation/test_api.py                              # LLM API connectivity
python3 evaluation/test_complex_circuits_strategies.py      # Generation strategy benchmark (costs API credits)
python3 evaluation/ultra_comprehensive_test.py              # Full LLM quality suite (costs API credits)
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
  world_slicer.py     Splits schematics into connected components

evaluation/          Legacy testing infrastructure (standalone scripts, not pytest)
  test_circuits/      12 JSON circuit definitions (3–96 blocks)
  schemas/            JSON schemas for LLM structured output
  prompts/            System prompt templates
  results/            Test output (gittracked)
  archive/            Deprecated approaches — do not reuse

scripts/             Utility scripts (all need sys.path hack)
  generate_training_data.py  LLM enrichment pipeline (description, verify, deconstruction)
  test_pipeline_e2e.py        E2E test of repair + deconstruction pipelines
  dev_tools/          Debug/verification helpers

tests/               Pytest test suite (1,116 tests)
  conftest.py         Root fixtures, block state utils, make_litematic helper
  unit/               Unit tests (401, no server) — parser, corruptor, world_slicer, circuit_builder, converter, llm_client, bridge, replicator, teacher_client
  pipeline/           Pipeline tests (119, no server/API) — enrichment, deconstruction, repair, format conversion, dataset generator
  integration/        Integration tests (378, needs server) — block placement, redstone, scarpet_api, entities, containers, comparators, observers, pistons, droppers/dispensers, hoppers, buttons/plates, circuit types, negative circuits, fake model output, replicator edge cases
  stability/          Stress tests (420, needs server) — 100x placement, 50x lever toggle, 30x build+rebuild, etc.
  fixtures/circuits/  14 JSON circuit definitions with machine-executable verification specs
  helpers/            circuit_builder.py (DSL), mock_rcon.py (fake RCON server)

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
- **Tests cost money** — `test_complex_circuits_strategies.py` and `ultra_comprehensive_test.py` make real API calls. `quick_test.py` and `test_api.py` are free/cheap. The pytest suite uses mocks for all LLM calls — no API cost.
- **No package structure** — don't try `pip install -e .` or `pytest` from outside the project root. Everything runs as scripts from project root with `sys.path` hacks. The pytest suite handles `sys.path` setup in `tests/conftest.py`.
- **`.litematic` and `.schem` files are gitignored** — test schematics live in `data/raw_schematics/` locally but aren't committed.
- **`data/` and `datasets/` are gitignored** — large data files stay local.
- **`discord_scraper/` has its own submodule** (`DiscordChatExporter`) — init submodules with `git submodule update --init`.
- **Scarpet auto-pauses** after ~118 tests — the `test_region` fixture calls `script resume` to handle this automatically.
- **`fill` command has a 32,768 block limit** — test regions use 31×16×31 = 15,376 blocks to stay under this.
- **Repeaters can't be powered via `setblock`** — this is a fundamental Minecraft mechanic (setblock triggers block updates, not redstone updates). Tests involving repeaters are skipped.
- **`mira_api update_region` is dangerous** — causes false positives on negative tests and server crashes. Use `mira_api update_block` (individual blocks) instead.
- **Scarpet scripts must be deployed** — copy from `simulation/scarpet_scripts/mira_api.sc` to `simulation/server/world/scripts/mira_api.sc` after changes, then reload with `script unload mira_api` + `script load mira_api`.

## Maintenance

- **Keep AGENTS.md up to date.** When you add new directories, files, conventions, dependencies, or gotchas, reflect them here. Stale docs are worse than no docs.
- **Clean as you go.** Delete temporary scripts, debug files, stale test outputs, and dead code paths when they're no longer needed. Don't let the repo accumulate cruft.
- **Archive, don't delete, for approaches.** When a strategy is superseded, move its artifacts to `evaluation/archive/` (or a similarly obvious location), not to some random subfolder. Document the failure reason in `docs/PAST_ATTEMPTS.md`.
- **Commit often, message briefly.** Make focused commits when you reach a logical checkpoint. Keep commit messages short, to the point, and descriptive of what changed.