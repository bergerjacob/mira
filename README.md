# MIRA — contraptions as code

Use your existing coding agent (Pi, Claude Code, Codex…) to design and edit technical Minecraft
contraptions in ordinary Python. MIRA supplies compact spatial queries, spec-driven tests and
exact-file vanilla handover checks. **The agent writes the condition; fixed Minecraft physics
is the kernel.** Finite tests remain evidence, not universal proofs.

## Try the checked path

With the repository's Python 3.12+ environment/dependencies installed:

```sh
.venv/bin/python examples/import_edit.py /tmp/mira-import-example
```

This standalone example generates a small NOT-gate input, archives the original bytes/digest,
imports its named offset selection with an empty margin, queries a compact layer, and makes one
normal Python edit. It runs the authored finite spec and a deliberately wrong negative expectation,
then exports and checks **the same file SHA256** for behaviour and paste placement when the local
Minecraft 26.2 oracle is installed. No API key, API spend or Carpet server is needed. The destination
must not exist. Oracle installation is separate: see [ORACLE.md](docs/ORACLE.md).

## Work on your own build

```sh
bin/mira init gate --from-litematic /path/input.litematic --dir /tmp/gate
bin/mira -p /tmp/gate build
bin/mira -p /tmp/gate overview
bin/mira -p /tmp/gate find 'region name'
bin/mira -p /tmp/gate view --layer 1
# Edit build.py, spec.json and NOTES.md using your agent's normal file tools.
bin/mira -p /tmp/gate test
bin/mira -p /tmp/gate spec-check
bin/mira -p /tmp/gate export
bin/mira -p /tmp/gate verify-vanilla --all
bin/mira -p /tmp/gate paste-check
bin/mira -p /tmp/gate status
```

Import is a **bounded, checked subset**, not arbitrary/lossless-file conversion. Originals and
selection metadata are retained in the project; scheduled ticks and unsupported conversion fail
explicitly. Supported entities/NBT remain editable but cannot receive today's state-only certified
export. Actual source/output/oracle DataVersions and mismatches are visible, never silently retagged.

- [Import limits, provenance and example](docs/IMPORT.md)
- [Project CLI and named memory](docs/PRODUCT.md) · [Python builder](docs/BUILDER.md)
- [Coding-agent skill](skills/mira/SKILL.md) · [Current priorities/scope](docs/FOCUS.md)
- [Measured import checkpoint](docs/audits/import_checkpoint_2026-11.md)

Run test directories separately: `.venv/bin/python -m pytest tests/project -q`, then
`.venv/bin/python -m pytest tests/unit/test_builder.py tests/unit/test_modules.py -q`.

Older Fabric/RCON, scraping, model/training and experimental-agent code is research/history,
not the product workflow. See [past attempts](docs/PAST_ATTEMPTS.md) and
[historical status](docs/STATUS_2026-11.md). No functional training improvement or general
arbitrary-contraption reliability has been demonstrated.
