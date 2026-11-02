# MCP server and CLI: the build/verify environment for external agents

Lets a frontier agent (Claude Code, any MCP client, or a subagent that only has bash) attempt MIRA tasks through the same
tools and the same scoring as the OpenRouter loop (`harness/`), so strong-agent baselines can be measured without that loop.
This is front end #2 of the three in `docs/audits/oracle_loop_2026-11.md` section 1 (the others: the OpenRouter harness and,
later, an RL environment). Nothing here changes `env/`, `harness/` or `simulation/`.

```
mira_mcp/session.py     Session: task + arm + feedback-round budget + current build + undo + transcript, persisted in data/sessions/<id>/
mira_mcp/server.py      MCP stdio server (mcp 2.x `MCPServer`), 18 tools, one thin wrapper per Session call
mira_mcp/knowledge.py   stateless lookups: explain_block, search_components (lib/*.meta.json), search_corpus (data/corpus/index.jsonl)
scripts/mira_env_cli.py the same operations as shell subcommands on the same on-disk session
tests/mira_mcp/         67 offline tests incl. real MCP stdio client runs
```
No `__init__.py` (repo convention); the scripts put the project root on `sys.path`.

## Install / run

```bash
cd /home/bergerj/main/personal/minecraft-dev/mira
.venv/bin/python -m pytest tests/mira_mcp -q -m "not slow"     # 66 tests, ~12 s
.venv/bin/python -m pytest tests/mira_mcp -q                   # + all 34 references (T0-T3) x 2 arms through sessions, ~95 s
.venv/bin/python mira_mcp/server.py                            # stdio server (waits for a client; Ctrl-D to stop)
.venv/bin/python scripts/mira_env_cli.py tasks                 # CLI
```

Needs only the repo venv (`mcp` 2.3, `nucleation`). No Minecraft server, no API key, no network.

## Register the server

Claude Code, project scope (`.mcp.json` in the repo root, or `claude mcp add`):

```json
{
  "mcpServers": {
    "mira-env": {
      "command": "/home/bergerj/main/personal/minecraft-dev/mira/.venv/bin/python",
      "args": ["/home/bergerj/main/personal/minecraft-dev/mira/mira_mcp/server.py"],
      "env": { "MIRA_SESSIONS_DIR": "/home/bergerj/main/personal/minecraft-dev/mira/data/sessions" }
    }
  }
}
```

```bash
claude mcp add mira-env -s project -- /home/bergerj/main/personal/minecraft-dev/mira/.venv/bin/python /home/bergerj/main/personal/minecraft-dev/mira/mira_mcp/server.py
```

Tools then appear as `mcp__mira-env__start_task`, `mcp__mira-env__set_blocks`, ... Any other MCP client takes the same
command/args as a stdio server. `MIRA_SESSIONS_DIR` is optional (default `data/sessions`); `server.py --resume ID` or `MIRA_SESSION=ID`
attach a server to an existing session at startup.

**pi:** I found no MCP client in the installed pi setup (no MCP entry in `~/.pi/agent/settings.json`, extensions or npm packages), so
MCP there is untested and probably unsupported: drive pi agents through the CLI below, which needs only bash. If an MCP adapter is
added, the snippet above is all it needs.

Neither path was exercised with the real Claude Code or pi binaries in this sandbox; the protocol is tested with the official Python
SDK client over stdio (`tests/mira_mcp/test_server_e2e.py`).

## Tool reference (MCP tool names = session tool names)

| tool | args | notes |
|---|---|---|
| `list_tasks` | `tier=""` | id, tier, #inputs/#outputs, reference block count, block cap. `t0 t1 t2 t3` or all (34 tasks: `env/tasks/*.json`, `env/tasks/t3/*.json`) |
| `start_task` | `task_id, arm="builder", rounds=3, extras="", session_id="", label=""` | the new session becomes the active one; returns the task text. `rounds`: N feedback rounds, `0` one-shot, `-1` unlimited. `task_id` may be a path to a task JSON or a bare contract JSON |
| `resume_session` / `session_status` | `session_id` / - | reattach to a session on disk (e.g. started by the CLI) / show state |
| `task_description` | - | `Contract.describe(public=True)` + block budget + palette + feedback rules of this session |
| `guide` | - | redstone facts the simulator enforces (same text as the harness system prompt); the builder arm adds the Part API reference |
| `set_blocks` `fill` `remove` | as `env/tools.py` | **blocks arm**; refused in the builder arm |
| `build` | `source, components=None` | **builder arm**; sandboxed `simulation.builder.compile`, REPLACES the build; any error loads nothing and errors come back with line numbers. After compile the task rules are applied (build box, fixed cells, block cap, simulatable blocks). Same logic as the harness's `build` tool |
| `undo` | - | last build-changing call; 40 steps kept |
| `view` | `layer, axis="y", box, live=false` | ASCII slice; `live` needs extras `probe` |
| `probe` | `pos` (`[x,y,z]`, `"x,y,z"` or output port), `ticks=20, inputs` | extras `probe` only |
| `run_spec` | - | public vectors/sequences + invariants with waveforms; counts against the round budget; footer shows `run_spec j/R used` |
| `submit` | - | all vectors incl. hidden (counts only: `HIDDEN checks: k/n`); writes `result.json`, appends one line to `data/sessions/results.jsonl`, ends the task |
| `search_components` | `query, limit=5` | `lib/*.meta.json`: ports, footprint, timing, verification, how to place it. Extras `components` (use with `build(source, components=[...])`, which prepends `env.library.prelude`) |
| `search_corpus` | `query, max_blocks=80, limit=5, show_program=1` | `data/corpus/index.jsonl` entries that are unique, simulatable, entity-free, `<= max_blocks` and `ml_use` clean or internal-only (`permission` entries are never served). Returns metadata and the builder program text of the top hits (relative coordinates; pre-decompiled `data/corpus/decompiled/*.py`, else decompiled on demand into `data/sessions/_cache/corpus/`); `id:<hash>` fetches one entry; programs are cut at 6000 chars with a notice. Extras `corpus` |
| `explain_block` | `block_id` | valid properties/defaults from `simulation/blockstates.py`, whether Nucleation can load it, facing notes; a full state like `repeater[delay=5]` is validated |

Output sizing: every result is clipped to `MIRA_MAX_CHARS` (default 12000) with an explicit `... [truncated N of M chars; ...]` notice;
`view` allows at most 8 layers per call. Agent mistakes never raise: they come back as `error: ...` text.

**Feedback accounting (mirrors the harness).** Only `run_spec` consumes rounds. R=0: `run_spec` is refused ("one-shot"). A call refused after
the budget is not counted. `probe`, `view live`, `search_components` and `search_corpus` are OFF unless the session was started with `extras`
(`probe,components,corpus` or `all`), because they are simulator feedback outside R or knowledge the OpenRouter arms do not have; a session
with extras is a different tool tier and must be reported as such (extras are in every result line). `explain_block` and `guide` are always on
(static validation / the facts the harness prompts already contain). Without an active session the `search_*` and `explain_block` tools are
unrestricted.

**Hidden information.** The agent sees `Contract.describe(public=True)`, `run_spec` feedback on public checks only, and `HIDDEN checks: k/n`
after submit. `state.json` stores only the task *reference* and the agent's own build; the contract (hidden vectors, evaluation seed, reference
solution) is re-read from `env/tasks` on every call and is never written to the session directory or returned. Tested over MCP and on disk
(`test_hidden_sequences_never_leak`, `test_no_hidden_leak_over_mcp`). **This does not stop an agent that can read `env/tasks/*.json` with its own
file tools; see Limitations.** For combinational tasks the hidden vectors are the public inputs in another order, so there is nothing extra to leak.

## CLI (bash-only agents)

```bash
alias mira='.venv/bin/python scripts/mira_env_cli.py'
mira tasks --tier t1
mira start t1_xor --arm builder --rounds 3 --label claude-opus-5-5     # prints the task text and the session id
export MIRA_SESSION=<id>                                              # per-agent pointer (default: data/sessions/.current)
mira guide                                                            # facts (+ Part API for the builder arm)
mira build --file prog.py                                             # '--file -' reads stdin
mira set 1 1 1 minecraft:stone ; mira set --file b.json ; mira fill X1 Y1 Z1 X2 Y2 Z2 STATE ; mira remove X Y Z [X2 Y2 Z2]    # blocks arm
mira view --layer 1 ; mira run-spec ; mira undo ; mira submit
mira components "full adder" ; mira corpus "xor gate" --max-blocks 40 --program 1 ; mira block comparator     # last two need --extras
mira show ; mira sessions ; mira results
```

`set --file` takes a JSON array `[[x,y,z,state],...]` or lines `x y z state` (`#` comments). `--rounds inf` = unlimited, `--extras all` enables the
extra tools. Output is the tool text only; exit code 0 even for `error:` results, non-zero for usage errors / unknown session. The CLI and the MCP
server read and write the same files under an `flock`, so a session started in one can be continued in the other.

## Protocol for a baseline run

1. **Fix the conditions first:** task list (e.g. T0+T1 = 21 tasks, optionally T2/T3), arm (`builder`/`blocks`), R (the harness grid uses 0, 3, 8),
   extras (none, for comparability with the OpenRouter arms), one attempt per (task, agent, arm, R), a fresh agent context per attempt.
2. **Create the session yourself** so the agent cannot change R or arm:
   `mira start t1_xor --arm builder --rounds 3 --id base-opus-t1_xor-builder-R3 --label claude-opus-5-5`, then give the agent the id
   (`MIRA_SESSION`) or register the MCP server with `--resume <id>`. (An MCP agent that calls `start_task` itself creates more sessions;
   count only the first per task.)
3. **What to tell the agent** (equivalent to the harness prompts; nothing about the hidden spec or the repo internals):

   > You are an expert Minecraft redstone engineer. Attempt MIRA task `<task_id>` using ONLY the `mira-env` MCP tools (or the `mira` CLI):
   > `task_description`, `guide`, the edit tools of your arm, `view`, `run_spec` (at most R calls; each simulates your build on the PUBLIC
   > vectors), then `submit` exactly once when you are done or out of options (submit ends the task and scores hidden vectors too).
   > Do not read or search any file in the repository and do not look up solutions anywhere: no `env/`, `lib/`, `harness/`, `tests/`,
   > `docs/`, `data/`. Plan the signal path first, build a complete candidate, then iterate on the feedback. At the end reply with one line:
   > what you built and whether you believe it passes.

   For the extras tier, say which of `probe` / `search_components` / `search_corpus` are allowed.
4. **Record.** Automatic: `data/sessions/<id>/` holds `state.json`, `transcript.jsonl` (every tool call with arguments, result, timestamp, ms),
   `sources/NNN.py` (every builder program tried), `final_blocks.json`, `result.json` (score + final feedback text), and
   `data/sessions/results.jsonl` gets one line per submitted session:

   ```json
   {"session": "t0_not-blocks-R2-1102-012241-fdab", "task": "t0_not", "tier": "T0", "arm": "blocks", "R": 2, "extras": [], "rounds_used": 1, "label": "claude-opus-5-5", "reward": 1.0, "full_pass": true, "r0": 1.0, "r1": 1.0, "r2": 1.0, "r3": 1.0, "r4": 1.0, "blocks": 6, "hidden": "2/2", "builds": 0, "calls": 5, "wall_s": 0.3, "ts": "2026-11-02T01:22:42"}
   ```

   `pass = full_pass` (same definition as `harness/`); `reward` is the R0-R4 reward. `wall_s` is wall time since `start` (includes the agent's
   thinking). Not recorded automatically: the agent's reasoning, token usage and cost; save the host transcript (Claude Code session JSONL,
   subagent output) next to the session directory and put the model id in `--label`.
5. **Unsubmitted attempts** (agent ran out of turns/context): the harness scores those as they stand, so do the same with
   `mira --session <id> submit`. Note them separately; the result line cannot tell them from a deliberate submit (the transcript can).
6. **Compare.** The result lines carry the fields of the harness `final` records (`task, arm, R, reward, full_pass, r0..r4, blocks`), so they can
   go in the same task x arm x R grid. `harness/analysis.py` reads its own run files, not these lines (not adapted).

## Verification done here

* 67 tests (`tests/mira_mcp`): all 34 T0-T3 reference solutions reach reward 1.0 / full pass through `Session`, both arms (the slow test);
  `t0_lamp` (blocks arm) and `t0_not` (builder arm) solved over a real MCP stdio client with `submit` reward 1.0 and both result lines;
  R budget (N, 0, unlimited, refused call not counted; over MCP and CLI); arm gating; undo; compile errors keep the previous build;
  persistence across handles and processes (MCP start -> CLI show/set -> MCP resume/submit); concurrent writers; hidden-sequence names absent
  from every response, the tool listing and every file on disk; extras gating; truncation notice; custom contract paths; T3 in a fresh process;
  explain_block, search_components, search_corpus (licence/size filters, program text, id lookup).
* Latency per call (one process, includes rebuilding the Env from disk): T0-T2 `run_spec` 0.03-0.15 s, `submit` 0.06-0.33 s; `t3_adder4`
  (493 blocks) 2.0 s / 5.1 s; `t3_adder8` (993 blocks) 5.5 s / 14.0 s; builder `build` 0.04 s. Session dir 6-13 KB for T0-T2, 0.2 MB for adder8.
  Task text 2.2-3.0 KB (T0-T2), 4.6-6.9 KB (adder4/8); a passing `run_spec` result is about 0.4-0.5 KB.

## Limitations and open problems

* **No isolation from the answer files.** `env/tasks/*.json` hold the hidden vectors, the evaluation seed and the reference solution; `lib/`,
  `harness/reference.py` and `data/` hold more. An agent with a file tool or bash can read them; the protocol only *instructs* against it.
  Not implemented: run the agent with read access to those paths denied (a sandbox `denyRead`), run the server as another user/container, or
  audit the host transcript for reads of `env/`. Until then a baseline is only as honest as its prompt and runtime.
* The corpus and library are not curated against the tasks: with extras `corpus`/`components` an agent can find a working AND gate for `t0_and`.
  That is what the extras tier is for, but those runs are not comparable with the plain arms.
* One active session per server process and one client (stdio). Parallel agents need their own server process, or the CLI with `MIRA_SESSION`
  (`data/sessions/.current` is a shared pointer; do not rely on it with parallel agents).
* Every call rebuilds the Env from `state.json` (simple, crash-safe, race-free), so `run_spec`/`submit` on big T3 tasks take seconds (adder8 submit
  ~14 s); an MCP client with a short tool timeout can give up.
* Wall time and call counts are recorded, not capped: unlike the harness there is no `max_turns`, token cap or cost ledger, so an agent can
  `view`/`set` indefinitely (R only limits `run_spec`). Cap turns in the agent runtime. Token usage and cost are not visible to the server.
* The builder sandbox is the one in `simulation/builder.py` (defence in depth, not a jail). With extras `components` the prelude shifts reported line
  numbers (the message says by how many).
* `undo` keeps 40 steps; in the builder arm it restores the previous program's blocks, not its source (all sources are in `sources/`).
* `probe` and `view live` can be enabled but are not counted against R (they do not exist in the harness arms). Decide per experiment.
* `Env.remove` (env/tools.py) answers "filled N cells" for removals; left as is (not owned here).
* Not verified with the real Claude Code / pi clients, only the SDK client. Installed `mcp` is 2.3 (`MCPServer`, not 1.x `FastMCP`); pin it.
* `AGENTS.md` should list `mira_mcp/`, `scripts/mira_env_cli.py`, `tests/mira_mcp/` (not owned by this task).

## Baseline protocol (sandboxed; round-2 audit H2)

The "Protocol for a baseline run" above only *instructs* the agent not to read the repo, and the round-2 audit found an agent that did
(the `t3_adder4` build contained `lib/full_adder.py` verbatim). Strong-agent numbers are only reportable from runs made this way
(`docs/VALIDITY.md`, checklist). It supersedes the first bullet of "Limitations" below ("No isolation from the answer files").

```bash
.venv/bin/python scripts/run_baseline_sandboxed.py start t2_door_2x2 --arm builder --rounds 15 --variant raw --label sonnet-5-5-raw
#   --variant raw: no library; --variant lib: extras=components and /mira/lib/*.py, *.meta.json, docs/LIBRARY.md are readable
#   prints the session id, the wrapper path data/sandbox/<id>/sh and the agent prompt (also in data/sandbox/<id>/PROMPT.md)
.venv/bin/python scripts/run_baseline_sandboxed.py record-usage <id> --model sonnet-5-5 --input-tokens N --output-tokens N --transcript agent.jsonl
.venv/bin/python scripts/run_baseline_sandboxed.py audit <id> --transcript agent.jsonl     # forbidden-path mentions in the agent's own transcript
.venv/bin/python scripts/run_baseline_sandboxed.py finish <id> [--submit-open]              # data/sandbox/<id>/{session/,summary.json}
.venv/bin/python scripts/run_baseline_sandboxed.py exec <id> -- python3 -c 'print(1)'      # any command in the sandbox (tests, debugging)
scripts/sandboxed_cli.sh <id> -c 'mira desc'                                                # same, shell-style
```

**What the agent gets.** One shell wrapper, `data/sandbox/<id>/sh`; every command the agent runs must go through it (`sh -c '<command>'`).
Inside (bubblewrap, no network, empty read-only root, `--cap-drop ALL`, own pid/ipc/uts/user namespaces): system python3 and coreutils,
`/tmp/work` (writable, host dir `data/sandbox/<id>/work`), `/mira/docs/BUILDER.md`, `/mira/docs/MCP.md`,
`/mira/evaluation/prompts/builder_system.md`, and the `mira` command. Not present: `env/` (so no `env/tasks`), `lib/` (unless variant lib: only `*.py`,
`*.meta.json`, `docs/LIBRARY.md`), `data/`, `tests/`, `harness/`, the other docs, `.env`, the venv, nucleation, other sessions' files.
`--with-sim` additionally mounts the venv, `simulation/builder.py`, `blockstates.py` and the `env/` code with `env/tasks` masked by an empty tmpfs.

**How the CLI still works.** The session code never runs in the sandbox. `mira` is a stdlib client that sends its argument list and the
contents of any `--file` over a unix socket to a *broker* thread that the wrapper runs for the lifetime of the wrapped command
(no daemon; background processes do not survive the tool call that started them in the agent sandboxes used so far). The broker runs the
real `scripts/mira_env_cli.py --session <id> ...` on the host with full repo access, so the contract, hidden vectors and reference
solution are read host-side only. The broker pins the session, accepts only `desc guide show set fill remove build view probe run-spec
undo submit components corpus block` (no `start`, `sessions`, `results`, `tasks`, no `--session`), and refuses any `--file` that was not
read inside the sandbox (so it is not a host file-read primitive). Every request is logged to `data/sandbox/<id>/broker.jsonl`, every wrapped
command to `shell.log`.

**Tested** (`tests/harness/test_sandbox.py`): `env/tasks`, `lib`, `data`, `tests`, `harness`, other docs and `.env` are unreadable (absolute host
paths and `/mira/...`); the docs are readable but read-only; no network; `desc/guide/build/run-spec/submit` work and a reference scores
full pass; `start/results/sessions/tasks/--session` are refused; a hand-rolled broker request naming a host file is refused; the R budget
holds; the lib variant exposes exactly the library; `--with-sim` masks `env/tasks`.

**Records per run** (host side, `data/sandbox/<id>/`): `broker.jsonl`, `shell.log`, `PROMPT.md`, `config.json`, `session/` (copy of
`data/sessions/<id>`: `transcript.jsonl`, `sources/NNN.py`, `final_blocks.json`, `result.json`), `usage.json` (token counts and cost, entered with
`record-usage` from the launching harness, which is the only place that knows them), `agent_transcript.*` (copy of the agent's own transcript), `summary.json`.

**Limits.** The sandbox confines whatever runs through the wrapper. If the agent is launched by another tool (e.g. the `subagent` tool),
that agent keeps its own file tools: launch it with bash as the only tool if the harness allows it, put the wrapper path and prompt in
its task, and run `audit` on its transcript afterwards (a heuristic grep for paths; the sandbox is the enforcement, the audit the check
that the agent used it). To confine the whole agent process run it under `exec --net <id> -- <command>` (network on; filesystem confined; credentials
for the model API must be passed by the caller; not exercised here). The sandbox does not stop the agent from using `mira` as intended,
including `run-spec` up to R and the extras the session enables; `explain_block`/`guide` text is by design visible.
