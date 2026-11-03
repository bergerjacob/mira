# Architecture decision: wrap a coding agent, own only the environment (2026-11-03)

Status: exploratory; nothing here locks us in. Supersedes the "own agent loop" framing in `HARNESS.md` for product use.

## Layers
| layer | who owns it | notes |
|---|---|---|
| Agent loop (read/edit/write/bash, context handling, git) | **a coding agent we do not write**: Pi, Claude Code, Codex, opencode, ... | Any of them works because the interface is files + a CLI. |
| Agent-facing pack | us, thin | `skills/mira/SKILL.md` (portable skill), `mira init` writes a project `AGENTS.md`, `bin/mira` CLI shim, MCP server `mira_mcp/server.py` (same commands as tools) |
| Project (the unit of work) | us | a directory: `spec.json`, `build.py` + `modules/`, `NOTES.md`, `.mira/` (revisions, reports, exports). The agent edits files; diffs are git diffs. |
| Build language | us (small) | Python Part API (`simulation/builder.py`), library cells, 3D router, module Design |
| Environment ("Minecraft as compiler") | us | env contract/runner/score (Nucleation tick sim), vanilla 26.2 oracle (`tools/oracle`), paste-check/export, `verify-vanilla`, `spec-check` |
| Own agent loop `harness/agent.py` | us, **kept only** for benchmarking cheap models and for training rollouts (fixed tool schema, budget ledger) | not the product |
| Training/data | us | `training/*`, synthetic engine, decontaminated shards |

## What the model has to do (today)
Write `spec.json`, write Python against the Part API (a few dozen lines for most things, library cells + `b.route_all` for wiring), and use ~10 CLI commands. Evidence: three unprimed coding agents did flush door / counter / 4-bit ALU in 6-21 minutes (`docs/EXPERIMENT_04.md`), the independent hidden-spec check passed 2/3 (`docs/EXPERIMENT_05.md`).

## Modes on the same substrate
- **Build mode** (this repo's product): fixed or agent-authored spec, universal predicate = contract v2, goal = a contraption that pastes exactly.
- **Dig mode** (not started): same oracle/builder/trace, but an existential predicate, novelty/dedupe, a local decompiled-source index (never committed, `data/external/src_<ver>/`) exposed as tools (`src_search`, `src_read`, `callers`, `src_diff`), certificates and a disclosure policy (`docs/audits/oracle_digging_2026-11.md`). It would be a second skill/profile (`skills/mira-dig/`) on the same CLI substrate, split into its own repo only when it needs Mojang-code-adjacent artifacts. Pilot D (`docs/DIFF_FUZZ.md`) is its first measurable instance.

## Rules for staying open
- Everything crosses layers as text/files (spec JSON, Python programs, `.litematic`, JSONL traces). No component imports another's internals except `env` <- `simulation`.
- No agent-framework dependency in core code; Pi/Claude Code integration lives in `skills/` and docs only.
- Version pin is undecided (1.21.0 server, 26.2 oracle, Nucleation 26.3 label); keep `oracle_versions.json`-style records in every certificate/receipt.
