# Checked import-hardening checkpoint

Scope: implement the four reproduced [import audit](oracle_import_2026-11.md) findings,
without new simulator/framework/training work or a version-target decision. No downloads/API calls.
No claims of arbitrary-format/build reliability or whole-file losslessness.

## Changes and admission policy

- `simulation/imports.py`: bounded raw NBT reader (including duplicate-key detection), litematic
  5–7 / Sponge v2/v3 admission, scheduled-tick/unknown-field refusal, strict state validation,
  packed-cell validation, raw-to-normalized typed payload/cell comparison and bounded project worker.
  Sponge source Offset is distinguished from Nucleation's local loader coordinates.
- `Project.init`: stage all inputs/writes before publication; preserve originals read-only by SHA256;
  bind `.mira/import.json` containing source format/version, origins, payload/limit disclosures,
  exact named signed-size region bounds and empty margins. Imported selections become queryable labels.
  Failed ordinary publication rolls back; unrecoverable rollback retains the original backup.
- Builder duplicate labels (including instance-qualified collisions) fail rather than overwrite.
  Direct file decompile performs raw admission; its gate is explicitly normalized-content equivalence.
- Build/evidence/export validate archived-source and sidecar integrity. Export/status record actual
  source/output/oracle DataVersions and mismatches, without changing tags. Entity/NBT editing remains
  available when raw conversion matches; certified export remains payload-free only.
- [Import guide](../IMPORT.md), current scope/README, generated standalone example and reusable
  local sample command document what is checked and what is unsupported.

## Regression commands and output

Run from repository root with the existing `.venv`; test directories remain separate because of
existing conftest-name collisions.

```
.venv/bin/python -m pytest tests/project -q
# 137 passed, 10 pre-existing multiprocessing/fork DeprecationWarnings, 28.57s
# Existing 93 + 27 generated-import regressions + 17 focused review/pilot regressions.

.venv/bin/python -m pytest tests/unit/test_builder.py tests/unit/test_modules.py -q
# 125 passed, 5.60s (106 builder + 19 modules)

.venv/bin/python examples/import_edit.py /tmp/mira-import-review-final
# EXAMPLE OK; details below

.venv/bin/python scripts/import_sample.py --report docs/audits/import_sample_2026-11.json
# attempted=24, imported_and_built=16, refused=8, build_failed=0,
# all_sources_unchanged=true
```

`tests/project/test_import.py` generates tiny NBT fixtures rather than committing binaries:
scheduled block/fluid ticks before loading; named multiple regions, positive offsets, negative sizes,
empty margins/all-air regions; unknown IDs/properties/values, invalid packed indices and overlaps;
malformed/orphan payloads; supported container/entity edit retention plus certification refusal;
missing/malformed/unsupported forced task reinit; archived-source/sidecar tampering, source changes
and project moves; output/source/oracle versions; duplicate source/authored/instanced labels;
compressed/decompressed/selection/list limits, timeout and nonregular streams; Sponge v2/v3 offsets.

Logs: `/tmp/mira-import-review-project-tests.log`, `/tmp/mira-import-review-builder-tests.log`,
`/tmp/mira-import-review-example.log`, `/tmp/mira-import-review-sample.log` (ephemeral local artifacts).

## Independent-review follow-up: boundary fixes

The independent review reproduced three real bugs in the first implementation; the following narrow
fixes and `tests/project/test_import_review.py` regressions now cover them:

1. **Unconsumed Sponge fields/layouts:** admission now uses version-specific root/block layouts,
   rejects fields outside a `Schematic` wrapper, and checks v3 payload wrappers contain exactly
   `Id`, three-component `Pos`, and compound `Data`. Extra wrapper `CustomName`, wrong-level
   `BlockEntities`, reserved position/id keys inside `Data`, mixed v2/v3 fields and extra position
   components are refused before normalization rather than discarded. A shared comparison check
   also refuses four-component litematic entity positions. The supplied reviewer fixtures
   `misplaced_block.schem`, `extra_entity.schem` and `four_pos.schem` all now explicitly refuse.
   The bounded custom parser stays: it rejects duplicate keys and truncated/count-bomb inputs that
   a generic NBT parser can accept or expand; no parser/framework replacement was added.
2. **Forced init retaining imported build:** no replacement schematic/program means keep and
   validate the existing import binding (also when changing the task/spec). Regressions verify
   binding/build bytes survive, archive corruption still blocks build/status, and forced reinit
   of a corrupted retained import fails without changing project bytes.
3. **Cleanup after publication:** directory publication is the commit point. Cleanup failure after
   publication returns successful init with `WARNING init published successfully; cleanup incomplete
   at PATH`, retaining any leftovers for safe later cleanup. It no longer raises a false failed-init
   result after replacing project files. Tests cover a real 0555 unrelated directory and a deterministic
   injected cleanup failure, preserving its contents and permissions. Before publication, original
   failures remain failures; cleanup errors are attached as notes rather than masking them.

A fresh agent pilot also exposed spaced-label NOTES parsing. Use `` `label:inverter bay` `` to name
one exact object; it is excluded from the legacy unquoted prefix/range scan. The regression checks
both a current full spaced name and a later rename becoming stale. The standalone example now uses
this syntax and reports `NOTES OK (rev 2): 3 coord, 1 label checked`. No explicit-file paste linkage
semantics were changed. No broader format support, simulator, training or framework work was added.

## Standalone example: actual measured result

Generated NOT gate, named `inverter bay` at source `(10,20,30)`, inclusive selection bounds
`(10,20,30)..(17,22,32)`, including an empty upper margin. Query returned the normalized label
`[0,0,0]..[7,2,2]`; compact layer view showed the eight-cell logic row. One ordinary Python edit
changed the conducting support from stone to calcite; typed intent memory stayed in NOTES.md.
The archived original remained byte-for-byte unchanged.

- Build: **32 world blocks / 6 authored task blocks**.
- Full env: **PASS, 4/4 vectors, reward 1.0**; deliberately wrong independent output expectation rejected.
- Spec check: **WARN, 11/12 mutants killed**. One redundant wire-to-stone mutation survived. This is
  a disclosed finite steady-state example, not exhaustive timing/spec quality evidence.
- Vanilla 26.2: **8/8 behavioural checks**, original and rotated case at two actual origins.
- Paste: **known_shape + place both exact and idle-stable for 40 ticks**, zero missing/extra/changed cells.
- Behaviour and paste receipts identify **one exact exported SHA256**:
  `17ab1b4d74cc1f8168310bb80933f2b7a45352cc65247b27af7bf4034a75b725`.
- Source original SHA256: `0fd251372c25b3f1ef73c27274b1920279e5fd50b476d057db662ff6df7288f6`.
- Actual source/litematic tags **5023**, oracle **4903**: mismatch visibly recorded, not retagged or
  claimed generally compatible. All four scoped evidence records were current at completion.

Generated input/export byte hashes vary on rerun because the existing writer emits timestamps;
the check is that both receipts within a run identify the same file, not reproducible binary bytes.
Scratch project: `/tmp/mira-import-review-final/project`. The source example is `examples/import_edit.py`.

## Diverse bounded local corpus: actual refusals retained

[Per-file machine-readable report](import_sample_2026-11.json) includes paths, input digests/sizes,
actual source versions, region/payload counts, import/build output or exact error, archive integrity
and per-file elapsed times. Deterministic convenience selection: 13 raw litematics, 3 external redstone
litematics, 2 converted logic litematics, 6 external Sponge computer/adder files. No new data fetched.

**16/24 admitted and sandbox-built**, **8 explicit refusals**, no subsequent build failures.
Successful normalized contents total **201,866 blocks**, largest **65,536**. Sources span actual
DataVersions **1343, 2584, 2975, 3953, 4325, 5023**; that is provenance, not compatibility proof.
Total summed per-file time **5.835s**, slowest **1.508s**. All original files remained unchanged.

Refusals:
- Six unsupported payload files: raw typed payload comparison refused dispenser factory,
  boat-heightmap, Fibonacci computer and Polybius UI; stricter preflight refused conflicting wrapper
  `Data` position/id fields in ADD005 and ADD007. Original files are not modified; nothing is silently
  stripped. A dedicated conversion investigation would be separate work, not a bypass.
- Smelter_V2: overlapping non-air regions (ambiguous overwrite).
- 32-bit Sklansky adder: declared selection **5,801,334 cells**, exceeding the **250,000** limit.

This only exercises raw admission, normalized-content round trip and Python build. No corpus file
received functional, placement or version-conversion certification from this sample. Convenience
sampling and the imposed subset make an arbitrary-import success-rate claim unjustified.

## Parent validation and fresh-agent edit pilot

After the review fixes, the parent reran `tests/project` (**137 passed**) and builder/module tests (**125 passed**), with exit codes0. Before the narrow review follow-up, the parent also ran `tests/oracle -m 'not slow'`: **101 passed, 7 expected failures** (known simulator divergences). This is targeted validation, not a full-repository test claim.

A fresh coding-agent worker used only the skill/docs/CLI and an existing imported inverter, with no benchmark/reference access. In `/tmp/mira-import-agent` it preserved **all32 authored blocks**, added **one wire and one lamp**, named the input/two outputs and recorded assumptions/places in NOTES. Its own tests and exact-file vanilla/paste checks passed. Its friction report exposed the spaced-label NOTES reference bug; that is now fixed and regression-tested (`label:inverter bay` inside backticks). Explicit-file paste receipts remain deliberately detached from project linkage unless the current export path is used.

The parent then checked the actual delivered file **without using its spec.json behaviour**: 40 independent seeded lever toggles, gaps12/16/20/40/70/100ticks, **1764ticks** per capture, both outputs checked at every tick at least8ticks after input changes (**2888 scalar checks per capture**). Two fresh vanilla JVM captures passed and had identical full block-state traces; a batched capture matched the isolated trace. Removing the added output lamp was rejected by this independent checker. The archived/source authored geometry was unchanged. Artifact SHA256 `d4ca4cfd18cd383c1f194bbda5f8cb09bd532284c35e7be385495270b27f7cfa`; logs `/tmp/mira-import-parent-final.log`, `/tmp/mira-import-independent.json`. This confirms one small edit workflow at one fixed origin and stated timing envelope—not arbitrary contraptions, rewind, universal compatibility or all possible timings.

After all code-review fixes, the parent reran the pilot's full test, exported a new file and repeated vanilla8/8 at two origins plus both paste modes. All evidence was current and the original archive intact. New artifact `/tmp/mira-import-agent/checked-after-review.litematic`, SHA256 `87949242eeeb2a0f5117062d8daadaeb0c797ba474324a6734776bbe2f6bfe53`, has exactly the same normalized block states as the independently checked earlier artifact. Log `/tmp/mira-import-post-review.log`.

Tick-control dependency audit: [TICK_DEBUGGING.md](../TICK_DEBUGGING.md). No new mod is justified; use existing vanilla GameTest stepping and reset/replay, with simulator checkpoints only for feedback.

## Remaining limits

Scheduled ticks, biomes, unknown states/fields, some real payload conversions, legacy formats,
ambiguous overlaps and over-budget files are explicitly unsupported. Air-clearing/selection topology,
original metadata and named-region layout are retained as original/sidecar, not preserved by export.
The worker is not an OS jail; direct in-process `builder.decompile` has no whole-operation timeout.
Directory publication is not power-loss-safe or concurrent-writer-safe; post-commit cleanup can
leave a warned-about staging/backup directory requiring later safe cleanup. Integrity receipts are local,
not signed against a malicious editor of every binding. State-only oracle receipts do not certify
entities/NBT, all invariants, request fidelity, general timing or universal version compatibility.
