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

## User-endorsed mental model
**The agent writes the condition like a theorem; Minecraft's fixed physics is the kernel.** Pi is the editor/reasoner, MIRA compiles the Python build and runs checks against the selected game version. Passing finite tests is still not a universal proof. The kernel boundary must explicitly state supported mechanics and preserve imported data rather than silently dropping unsupported content.

For large contraptions, use codebase-style access: bounded overview -> named regions/ports -> requested slices/queries. Persistent spatial/intent memory lives in each project's `NOTES.md`, Python labels/ports and `spec.json`. Do not depend on custom metadata inside `.litematic` surviving third-party tools; a portable project sidecar can accompany an export. No embedded Pi framework or new agent harness is needed for this.

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

## Import-hardening checkpoint (implemented)

[Import guide](IMPORT.md), [measured report](audits/import_checkpoint_2026-11.md). Raw input admission now precedes normalization: scheduled block/fluid ticks, unsupported fields/conversion, malformed/unknown states and ambiguous overlaps fail explicitly. Project imports have file/decompression/selection limits and a time/memory-bounded worker. Original bytes/SHA256, actual source DataVersion, normalization origin and named signed-size region bounds (including empty margins) are archived in portable project sidecars and checked for tampering. Region labels are queryable; duplicate labels cannot silently overwrite. Failed forced init is staged and leaves existing project bytes unchanged.

Independent-review fixes now reject unconsumed/mixed Sponge layouts and payload wrapper fields, retain/validate import provenance when forced init keeps build.py, and report post-publication cleanup problems as successful-init warnings with leftover paths rather than false failures. Exact spaced NOTES names use backticked qualified references such as `label:inverter bay` (the full reference enclosed in markdown backticks); rename/deletion checks use the whole name.

The supported subset is litematic 5–7 / Sponge v2/v3 when raw-to-normalized equivalence succeeds, using the existing state registry. Ordinary supported entities/NBT remain editable, but certified export still refuses payloads. Re-export is **not** original-file losslessness: air/selection topology, region layout, ticks, metadata and version-conversion compatibility are not certified. Actual source/output/oracle DataVersions and mismatches are visible; no target was selected or retagged.

`examples/import_edit.py` is the reproducible generated-input golden path (ordinary Python edit, retained named region, negative expectation, exact-file handover). It is a steady-state NOT gate, **not** a pulse/repeat-press envelope example. No API/model/framework/simulator/training expansion was needed. The measured local corpus sample contains explicit refusals, not an arbitrary-import success claim.

## Next narrow work
1. Correct pulse-event coverage (attempted presses, minimum spacing, cross-input offsets); then repair the known slow counter and verify it against independent sequences with an explicit timing envelope. Do not turn import admission into a new converter or registry/version-selection project without a separate decision.
2. Run one honest edit/repair pilot with an existing coding agent. Measure request fidelity, edits, tokens and wall time, not just reward.

Stop for now: more scraping, broad task families, new MCP abstractions, simulator expansion, training runs and open-ended digging. Existing research stays available; it is not the default product workflow.
