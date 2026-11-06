# Import, edit, retain the original

Use Pi (or another coding agent) normally: edit Python and `spec.json`, request compact views,
and keep named spatial/intent memory in `NOTES.md`. Minecraft physics stays fixed; the agent
writes the condition to check. Finite passing tests are not a universal proof.

```sh
bin/mira init my-build --from-litematic /path/input.litematic --dir /tmp/my-build
bin/mira -p /tmp/my-build build
bin/mira -p /tmp/my-build overview
bin/mira -p /tmp/my-build find 'region name'
bin/mira -p /tmp/my-build view --layer 1
# Edit /tmp/my-build/build.py and write /tmp/my-build/spec.json with normal file tools.
bin/mira -p /tmp/my-build test
bin/mira -p /tmp/my-build spec-check
bin/mira -p /tmp/my-build export
bin/mira -p /tmp/my-build verify-vanilla --all
bin/mira -p /tmp/my-build paste-check
bin/mira -p /tmp/my-build status
```

`--from-litematic` also admits the checked Sponge `.schem` subset; its name is retained for CLI
compatibility. No API call, download, new framework or Carpet server is involved. Local checks
use repository dependencies; vanilla checks require the already installed oracle.

## What is retained and checked

- Original bytes: read-only `.mira/imports/<SHA256>.<extension>`, never rewritten or version-retagged.
  Changing/deleting the external input later cannot change the archive. Retain the **whole project**
  when transferring provenance: an exported schematic alone does not contain this sidecar/memory.
- `.mira/import.json`: digest, original name/format/actual DataVersion, source normalization offset,
  loader normalization offset, exact named region position/signed size/inclusive bounds (including
  empty margins and all-air regions), normalized bounds, payload counts and admission limits.
  `project.json` binds this metadata digest. Build/evidence creation refuse altered/missing archives
  or sidecars; `status` reports tampering. These are local integrity checks, not signed attestations
  against someone maliciously rewriting both metadata and its digest.
- Region names become `b.label(...)` calls, queryable by `find`, `overview`, and revision labels.
  Duplicate authored/instanced labels and duplicate raw NBT keys/region names fail, never overwrite.
  In `NOTES.md`, use a backticked qualified reference such as `` `label:inverter bay` `` for one exact
  name containing spaces; `notes-check` checks the whole name and detects later renames/deletions.
  Unquoted `label:NAME` references/ranges retain their existing syntax.
- Raw packed cells and typed block/entity NBT are compared with loader output **before** the
  generated program's normalized-content round trip. Unsupported conversion fails explicitly.
  Ordinary litematic entities/container NBT can be edited and retained; they are **not certifiable**
  by the current state-only export/oracle path.
- Coordinates normalize the minimum non-air corner to zero. Source offsets are retained, not
  reapplied to exported coordinates. Sponge's source `Offset` and the loader's local origin are
  distinguished. Selection air/empty margins are labels/sidecar only: re-export does **not** preserve
  selection topology, explicit air-clearing semantics, original metadata, file bytes or region layout.
- All init inputs and writes are staged before publishing, including `--force --from-task`.
  Missing/malformed/unsupported inputs leave existing project bytes unchanged. Successful forced
  reinit keeps unrelated existing files. If `build.py` is retained (no replacement schematic/program),
  its existing import binding is also retained and validated, even when `--from-task` replaces the spec.
  Publication is the commit point: a subsequent staging/backup cleanup error returns **successful init
  with a warning and the leftover path**, not a false failure after replacing the project. Keep that
  path until any leftovers can be removed safely; the published tree contains the retained user data.
  Pre-publication failures remain failures; cleanup problems are attached without hiding the original
  error. Directory publication rolls back ordinary rename failures; it is not a power-loss-safe
  transaction or a concurrent-writer lock.

## Supported boundary (not arbitrary or lossless-file conversion)

Litematic versions 5–7 and Sponge schematic v2/v3 with known, valid states in the existing 1.21
registry are admitted when raw-to-normalized comparison succeeds. Negative-sized litematic regions
are supported by the installed dependency and covered by generated fixtures. Litematic palettes
using either `Name/Properties` or the dependency's `id/properties` encoding are checked.
Sponge field layouts are version-specific: v2 uses flat block fields, v3 uses `Blocks`; wrong-level
payloads and siblings outside a `Schematic` wrapper are refused. V3 payload wrappers must contain
only `Id`, three-component `Pos`, and compound `Data`; extra wrapper fields and conflicting reserved
position/id fields inside `Data` are refused before normalization, never silently discarded.

Explicit refusals include scheduled block/fluid ticks, unknown payload fields, biomes, unknown or
illegal states/properties, malformed arrays/NBT, duplicate keys, overlapping non-air regions,
unsupported payload conversion, and other formats (including legacy MCEdit and vanilla structure
NBT input). Some real entity/block-entity files fail raw payload equivalence and are refused rather
than silently stripped. Do not bypass a refusal by removing the payload without the user's consent.

Limits: **16 MiB input, 64 MiB decompressed NBT, 250,000 declared selection cells** (sum across
regions, including air), 1,024 regions, 256-character region names, NBT depth 64 / 2M nodes,
64 MiB generated Python. Project import uses a **45-second CPU / 48-second wall, 2 GiB** worker;
non-regular input streams are refused. These are practical resource limits, not an OS security jail.
The lower-level `builder.decompile` retains raw checks but runs in-process (including optional motif
search); use project import for the bounded untrusted-file boundary. In-memory `Schematic` objects
have no recoverable original bytes and cannot provide raw-file provenance.

Export/status record **actual source, litematic output, optional structure output and installed
oracle DataVersions**, and warn on mismatches. The dependency currently emits litematic **5023**,
structure writer/oracle **4903**. Tags are not relabelled. State readback and selected vanilla checks
do not certify general version-conversion compatibility. Certified export still refuses entities/NBT.

## Reproduce the checks

```sh
.venv/bin/python examples/import_edit.py /tmp/mira-import-example  # destination must not exist
.venv/bin/python -m pytest tests/project -q
.venv/bin/python -m pytest tests/unit/test_builder.py tests/unit/test_modules.py -q
.venv/bin/python scripts/import_sample.py --report /tmp/import-sample.json  # local corpus required
```

The generated-input example retains a named inverter selection with offset/empty margin, queries
it, prints one compact layer, edits stone → calcite in normal Python, rejects a deliberately wrong
expectation, then runs existing exact-file behaviour/paste checks if the oracle is available.
Measured results and actual sample refusals: [checkpoint report](audits/import_checkpoint_2026-11.md),
[per-file corpus results](audits/import_sample_2026-11.json). No arbitrary-build reliability claim.
