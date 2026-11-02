#!/usr/bin/python3
"""`mira` inside the baseline sandbox: forwards the command line to the host-side broker (scripts/run_baseline_sandboxed.py serve).

    mira desc | guide | show | set ... | fill ... | remove ... | build --file prog.py | view ... | run-spec | undo | submit
    mira components QUERY | corpus QUERY | block ID | probe ...          (the last three only with the matching --extras)

Same arguments and output as scripts/mira_env_cli.py; the session is fixed by the host (there is no `start`, `sessions`,
`results` or `--session`). Files named by `--file` are read here and sent along, so they can live anywhere in /tmp/work.
Standard library only (the sandbox has the system python3, not the repo venv).
"""
import base64
import json
import os
import socket
import sys

SOCK = os.environ.get("MIRA_BROKER_SOCKET", "/ipc/mira.sock")


def main(argv):
    files, out_argv, i = {}, [], 0
    stdin_b64 = None
    while i < len(argv):
        a = argv[i]
        if a == "--file" and i + 1 < len(argv):
            path = argv[i + 1]
            if path == "-":
                stdin_b64 = base64.b64encode(sys.stdin.buffer.read()).decode()
                out_argv += [a, "-"]
            else:
                try:
                    data = open(path, "rb").read()
                except OSError as e:
                    print(f"error: cannot read {path}: {e.strerror}", file=sys.stderr)
                    return 2
                key = f"f{len(files)}"
                files[key] = base64.b64encode(data).decode()
                out_argv += [a, f"@{key}"]
            i += 2
            continue
        out_argv.append(a)
        i += 1
    req = {"argv": out_argv, "stdin_b64": stdin_b64, "files": files}
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK)
    except OSError as e:
        print(f"error: cannot reach the MIRA broker at {SOCK}: {e}", file=sys.stderr)
        return 3
    s.sendall(json.dumps(req).encode())
    s.shutdown(socket.SHUT_WR)
    buf = b""
    while True:
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        buf += chunk
    try:
        r = json.loads(buf)
    except ValueError:
        print("error: bad broker reply", file=sys.stderr)
        return 3
    sys.stdout.write(r.get("stdout", ""))
    sys.stderr.write(r.get("stderr", ""))
    return int(r.get("rc", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
