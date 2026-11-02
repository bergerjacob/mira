#!/bin/sh
# Run a command inside the baseline sandbox of a session (see scripts/run_baseline_sandboxed.py).
#   scripts/sandboxed_cli.sh SESSION_ID -c 'mira desc'         # like sh -c
#   scripts/sandboxed_cli.sh SESSION_ID python3 /tmp/work/x.py
# `run_baseline_sandboxed.py start` also writes data/sandbox/SESSION_ID/sh, which is the same wrapper with the id baked in.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
sid=$1
shift
py=${MIRA_PYTHON:-$here/../.venv/bin/python}
[ -x "$py" ] || py=python3
exec "$py" "$here/run_baseline_sandboxed.py" exec "$sid" -- "$@"
