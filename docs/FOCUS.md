# Working brief: contraptions first

## What the user keeps asking for
- Best end product: an agent that can **design useful technical contraptions**, not play survival or merely emit blocks.
- Work like a code repository: ordinary file edits, small diffs, local queries, compact overview, tests, iterate. Do not dump a whole schematic into context.
- Reuse mature coding agents (Pi, Claude Code, Codex, etc.) and familiar Python. No new model-specific syntax or agent framework to train users/models on.
- A schematic should be as trustworthy to paste as a human's. Distinguish placement from functional correctness; never call an untested artifact verified.
- Prefer a narrow excellent tool over a large research framework. Keep alternatives open, backtrack when evidence contradicts a choice, and do not mistake more infrastructure for progress.
- Training is worthwhile only if it improves functional results. HPC is available in principle; no details yet. More scraping is not the bottleneck.
- Code digging is interesting and may share Minecraft execution/trace infrastructure, but contraption engineering comes first. No premature repo split or grand unified framework.
- Originally prefer 1.21.0, later leave version decisions open. Do not silently equate the 1.21 server, 26.2 vanilla oracle and simulator's version label.

## This checkpoint
Independent audits of the current product found a smaller, higher-value target than more tasks or integrations: **tie test/export/verification evidence to one source/spec/engine and one actual schematic**.

Implemented:
- Tests record source, resolved library dependencies, spec, implementation/registry/metadata and engine identities. Old unbound receipts are stale, not silently trusted.
- Export fails closed on invalid specs and illegal fixed-cell conflicts. Exact certified export currently rejects authored entities/NBT instead of pretending to simulate them.
- Automatic verification reuses only a current, untampered, full-scenery export. File identities inside projects are relative, so moving a project still works.
- `verify-vanilla` consumes the exported file's blocks, with recorded origin mapping. `--file` supports another artifact; unknown origins must be explicit.
- Vanilla output explicitly covers behavioural expectations, not all environment invariants. Environment full-pass uses the actual score.
- Paste and behaviour receipts name the artifact SHA-256. Unsupported entity/block-entity payloads cannot receive a state-only “exact” certificate.
- `mira status` shows evidence freshness/scope; it is informational, not a proof or an exit-code gate. Sampled checks and public-only tests remain limited evidence.

Use this order (don't re-export between the two checks unless you recheck the new file):
```
mira build
mira test
mira spec-check
mira export
mira verify-vanilla --all
mira paste-check
mira status
```
`verify-vanilla` and `paste-check` auto-export if necessary. The explicit order makes the handover identity easy to inspect. These local receipts are provenance records, not signed attestations or protection against a malicious agent editing `.mira/`.

### Validation
`python -m pytest tests/project -q`: **93 passed** (23 new evidence tests), with the installed vanilla oracle enabled. A copied 14-block gated-lamp project completed full env testing, exported 105 blocks including scenery, replayed **14/14 checks at two actual world origins** in vanilla 26.2, and passed both paste modes on the **same SHA-256**. Final `status` marked all four records current. Logs: `/tmp/mira-project-tests.log`, `/tmp/mira-handover-smoke.log`; scratch project `/tmp/mira-handover-BHWi` (not a permanent example). This is a targeted regression/smoke check, not a full-suite or independent request-fidelity claim. A fresh read-only reviewer identified origin and fast-scope issues; both were fixed and covered by regression tests.

## Next work, not started in this checkpoint
1. Preserve a small standalone example in `examples/`, with an explicit repeat-press timing envelope and independent negative checks. Replace the stale root README with that golden path; keep old server/research setup clearly historical.
2. Correct pulse-event coverage (attempted presses, minimum spacing, cross-input offsets); then repair the known slow counter and verify the fix against independent sequences, rather than build more benchmarks.
3. Run one honest edit/repair pilot with an existing coding agent. Measure request fidelity, edits, tokens and wall time, not just reward.

Stop for now: more scraping, broad task families, new MCP abstractions, simulator expansion, training runs and open-ended digging. Existing research stays available; it is not the default product workflow.
