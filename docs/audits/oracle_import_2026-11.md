# Import-boundary audit (read-only)

Oracle child ac2f93e0-7ade-444a-85bd-83bd90780120, workflow 811bf907. Based on local commit 0b69aa5. No implementation changes in this audit checkpoint.

## Findings reproduced by the reviewer
1. **P1: normalized equivalence is called lossless.** `simulation/builder.py:1366-1410,1652-1714`, `mira_mcp/project.py:381`. Pending block/fluid ticks never enter round-trip comparison. A pending-block-tick fixture imported, built and exported with `readback ok`, but output had no scheduled ticks. Payloads dropped by the loader are similarly invisible. Narrow the claim; inspect raw input before normalizing.
2. **P1: failed init mutates existing files.** `mira_mcp/project.py:356-406`. With `force=True`, task t0_not and a missing schematic, spec.json was replaced before schematic loading failed; project metadata/build remained old. Preflight all inputs before touching destination; transactionally publish changes.
3. **P2: version provenance is lost.** `simulation/export.py:24,90-117,235-283`. One source was DataVersion3953, its litematic export5023, structure NBT4903, oracle4903. No stone-fixture incompatibility demonstrated, but readback does not establish conversion compatibility. Record actual source/output versions; never relabel a version without a real conversion policy.
4. **P2: named selection/memory is lost.** A named three-cell region at(10,20,30) with one central block became an unnamed one-cell export at0. Offset, empty margins, region name/bounds absent from project metadata/labels. `Part.label` also silently overwrites duplicate names.

## One next implementation checkpoint
- Preflight raw schematic metadata and payload counts. Unsupported scheduled ticks must be refused before normalization (or explicitly preserved without certification); keep the original file byte-for-byte and digest it.
- Preserve source version, normalization offset, named region bounds. Expose meaningful region labels and explicitly disclose unsupported air/selection semantics.
- Complete validation before modifying destination files; failed reinitialization must leave an existing project unchanged.
- Replace blanket 'lossless' with normalized blocks/NBT/entities equivalence, including exactly what is excluded.
- Version policy needs verified conversion rather than changing tags. If undecided, record the mismatch and refuse unsupported certification; don't silently pick a new target version.

Regression fixtures: failed forced reinit, scheduled block/fluid ticks, offset named regions with empty margins, output-version provenance, supported entity/NBT preservation plus certification refusal.

## Limits / corrections to historical docs
The reviewer tested ordinary entities and block entities surviving a positive-offset import; certified export correctly refused them. Valid negative-sized regions work in the currently installed dependency (old BUILDER.md limitation is stale). Large imports perform loading/motif discovery/round-trip checks outside the compilation subprocess limits; no scalability stress test was performed in this audit.

Do not expand simulator/training/digging scope to fix this. This is import admission, provenance, spatial memory and failure atomicity.
