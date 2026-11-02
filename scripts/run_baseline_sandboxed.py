#!/usr/bin/env python3
"""Run a strong-agent baseline so that it can reach the MIRA environment ONLY through the CLI, never the answer files.

Round-2 audit H2: an agent that can read the repo can read `env/tasks/*.json` (hidden vectors, reference solutions), `lib/`,
earlier sessions and the docs; the protocol only asked it not to, and one agent (t3_adder4) demonstrably did. This runner makes
the repo unreadable instead of forbidden.

Design (the simplest that denies the answer files; details and limits in docs/MCP.md, "Baseline protocol"):

    host                                              bubblewrap sandbox (no network, fresh tmpfs root)
    ----                                              --------------------------------------------------
    broker (thread of `exec`)      <-- unix socket -- /mira/bin/mira  (stdlib client; forwards argv + --file contents)
      runs scripts/mira_env_cli.py --session SID      /mira/docs/BUILDER.md, docs/MCP.md, evaluation/prompts/builder_system.md
      with the full repo (env/tasks, data/, lib/)     [variant=lib: /mira/lib/*.py *.meta.json, /mira/docs/LIBRARY.md]
      pins the session, whitelists subcommands        /tmp/work (writable, host dir data/sandbox/SID/work)
      logs every request to broker.jsonl              system python3, coreutils (/usr, read-only)

The env/session code never runs inside the sandbox, so the sandbox does not need `env/tasks`, `data/`, `tests/`, `harness/`,
`docs/` (except three files), the venv, nucleation, or `lib/` (except for variant=lib). The agent cannot start sessions,
list other sessions, read `results.jsonl`, change R or the arm: the broker passes `--session SID` itself and accepts only the
agent-facing subcommands. `--file X` is read INSIDE the sandbox and shipped as bytes; the broker refuses any other file path,
so it cannot be used as a read primitive on the host.

    python scripts/run_baseline_sandboxed.py start t1_xor --arm builder --rounds 15 --variant raw --label sonnet-5-5-raw
        -> creates the session, writes data/sandbox/SID/{PROMPT.md,sh,config.json}; prints the prompt and the wrapper path the agent
           must use for EVERY shell command:   data/sandbox/SID/sh -c '<command>'
           (no daemon: each wrapped command runs its own broker thread for its lifetime; all state is the session on disk)
    python scripts/run_baseline_sandboxed.py exec SID -- COMMAND ...     # any command in the sandbox (tests, debugging)
    python scripts/run_baseline_sandboxed.py status SID
    python scripts/run_baseline_sandboxed.py record-usage SID --model M --input-tokens N --output-tokens N [--transcript FILE]
    python scripts/run_baseline_sandboxed.py audit SID --transcript FILE ...    # grep a subagent transcript for forbidden reads
    python scripts/run_baseline_sandboxed.py finish SID [--submit-open]         # collect transcripts + summary.json

Host-side records (all under data/sandbox/SID/): `broker.jsonl` (every CLI request: argv, rc, sizes, ms), `shell.log` (every command
run through the wrapper), `usage.json` (token counts, from `record-usage`), `session/` (copy of data/sessions/SID incl.
transcript.jsonl and sources/), `summary.json`.

Limits: this confines whatever runs through the wrapper. A subagent launched by another harness still has its own file tools; to
keep it honest launch it with bash as its only tool (or deny read/grep/find) and run `audit` on its transcript. To confine the
whole agent process use `exec --net SID -- <agent command>` (network on, filesystem still confined; credentials must be
supplied by the caller; not exercised here).
"""
import argparse
import base64
import json
import os
import re
import shlex
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX_ROOT = Path(os.environ.get("MIRA_SANDBOX_DIR", ROOT / "data" / "sandbox"))
BWRAP = os.environ.get("BWRAP") or shutil.which("bwrap") or str(Path.home() / ".local/bin/bwrap")

# What the agent may run through the broker. No start/sessions/results/tasks, no --session.
ALLOWED_CMDS = ("desc", "guide", "show", "set", "fill", "remove", "build", "view", "probe", "run-spec", "undo", "submit",
                "components", "corpus", "block")
MAX_REQUEST = 8 << 20
MAX_REPLY = 4 << 20
CALL_TIMEOUT = 900

# Repo files an agent legitimately needs (docs for the representation; no spec, no tasks, no data).
AGENT_FILES = ["docs/BUILDER.md", "docs/MCP.md", "evaluation/prompts/builder_system.md"]
LIB_FILES = ["docs/LIBRARY.md"]
FORBIDDEN_RE = re.compile(
    r"env/tasks|(?<![\w/])lib/\w+\.(?:py|json)|data/(?:sessions|sandbox|corpus|experiments|external|oracle|raw)|"
    r"(?<![\w/])tests/|harness/|docs/(?!BUILDER\.md|MCP\.md|LIBRARY\.md)\w|author(?:_t\d)?\.py|\.env\b|results\.jsonl|state\.json|final_blocks")


# --------------------------------------------------------------------------- paths / config
def sdir(sid: str) -> Path:
    return SANDBOX_ROOT / sid


def load_cfg(sid: str) -> dict:
    p = sdir(sid) / "config.json"
    if not p.exists():
        sys.exit(f"error: no sandbox for session {sid!r} (looked in {sdir(sid)})")
    return json.loads(p.read_text())


def agent_mounts(cfg: dict):
    """[(host path, sandbox path)] read-only file binds."""
    m = [(ROOT / "scripts/mira_sandbox_client.py", "/mira/bin/mira")]
    files = list(AGENT_FILES)
    if cfg["variant"] == "lib":
        files += LIB_FILES
        m += [(p, f"/mira/lib/{p.name}") for p in sorted((ROOT / "lib").glob("*"))
              if p.suffix == ".py" or p.name.endswith(".meta.json")]
    m += [(ROOT / f, f"/mira/{f}") for f in files if (ROOT / f).exists()]
    return m


def bwrap_argv(cfg: dict, ipc: Path, net: bool = False) -> list:
    sd = sdir(cfg["session_id"])
    a = [BWRAP, "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
         "--unshare-cgroup-try", "--cap-drop", "ALL", "--clearenv"]
    if not net:
        a.append("--unshare-net")
    a += ["--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/sbin", "/sbin"]
    if Path("/lib64").exists():
        a += ["--symlink", "usr/lib64", "/lib64"]
    for e in ("/etc/alternatives", "/etc/ld.so.cache", "/etc/passwd", "/etc/group", "/etc/localtime"):
        if Path(e).exists():
            a += ["--ro-bind", e, e]
    if net:
        for e in ("/etc/resolv.conf", "/etc/ssl", "/etc/ca-certificates"):
            if Path(e).exists():
                a += ["--ro-bind", e, e]
    a += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/work"]
    a += ["--bind", str(sd / "work"), "/tmp/work", "--ro-bind", str(ipc), "/ipc"]
    for src, dst in agent_mounts(cfg):
        a += ["--ro-bind", str(src), dst]
    if cfg.get("with_sim"):   # venv + builder + env CODE (never env/tasks); lets the agent simulate its own build offline
        a += ["--ro-bind", str(ROOT / ".venv"), "/mira/.venv", "--ro-bind", str(ROOT / "simulation/builder.py"), "/mira/simulation/builder.py",
              "--ro-bind", str(ROOT / "simulation/blockstates.py"), "/mira/simulation/blockstates.py",
              "--ro-bind", str(ROOT / "env"), "/mira/env", "--tmpfs", "/mira/env/tasks"]
    a += ["--remount-ro", "/", "--chdir", "/tmp/work", "--setenv", "HOME", "/tmp/work", "--setenv", "PATH", "/mira/bin:/usr/local/bin:/usr/bin",
          "--setenv", "LANG", "C.UTF-8", "--setenv", "TERM", os.environ.get("TERM", "dumb"),
          "--setenv", "MIRA_BROKER_SOCKET", "/ipc/mira.sock", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    if cfg.get("with_sim"):
        a += ["--setenv", "PYTHONPATH", "/mira"]
    return a


def append_log(path: Path, rec) -> None:
    with open(path, "a") as f:
        f.write((rec if isinstance(rec, str) else json.dumps(rec)) + "\n")


# --------------------------------------------------------------------------- broker
class Broker:
    def __init__(self, cfg: dict):
        self.cfg, self.sid = cfg, cfg["session_id"]
        self.dir = sdir(self.sid)
        self.lock = threading.Lock()
        self.last = time.time()

    def refuse(self, msg: str):
        return {"rc": 2, "stdout": "", "stderr": f"error: {msg}\n"}

    def handle(self, req: dict) -> dict:
        self.last = time.time()
        argv = req.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            return self.refuse("bad request")
        if argv[0] not in ALLOWED_CMDS:
            return self.refuse(f"`{argv[0]}` is not available in this sandbox; commands: {' '.join(ALLOWED_CMDS)}")
        if any(x == "--session" or x.startswith("--session=") for x in argv):
            return self.refuse("the session is fixed")
        files = req.get("files") or {}
        with tempfile.TemporaryDirectory(prefix="mira_req_") as tmp:
            host_argv = list(argv)
            for i, x in enumerate(argv):
                if x == "--file" and i + 1 < len(argv):
                    v = argv[i + 1]
                    if v == "-":
                        continue
                    if not (v.startswith("@") and v[1:] in files):
                        return self.refuse("--file must name a file inside the sandbox (use `mira ... --file path`)")
                    p = Path(tmp) / v[1:]
                    p.write_bytes(base64.b64decode(files[v[1:]]))
                    host_argv[i + 1] = str(p)
                elif x.startswith("--file="):
                    return self.refuse("use `--file PATH` (two words)")
            stdin = base64.b64decode(req["stdin_b64"]) if req.get("stdin_b64") else b""
            env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "MIRA_SESSIONS_DIR", "MIRA_MAX_CHARS") if k in os.environ}
            t0 = time.time()
            with self.lock:
                try:
                    r = subprocess.run([self.cfg["python"], str(ROOT / "scripts/mira_env_cli.py"), "--session", self.sid, *host_argv],
                                       input=stdin, capture_output=True, cwd=tmp, env=env, timeout=CALL_TIMEOUT)
                    rc, out, err = r.returncode, r.stdout, r.stderr
                except subprocess.TimeoutExpired:
                    rc, out, err = 124, b"", f"error: timed out after {CALL_TIMEOUT}s\n".encode()
            ms = round((time.time() - t0) * 1000)
        out, err = out[:MAX_REPLY], err[:MAX_REPLY]
        append_log(self.dir / "broker.jsonl", {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "argv": argv, "files": {k: len(v) * 3 // 4 for k, v in files.items()},
                                               "rc": rc, "stdout_bytes": len(out), "stderr_bytes": len(err), "ms": ms})
        return {"rc": rc, "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace")}


class BrokerServer:
    """Unix-socket server in a thread: `with BrokerServer(cfg, ipc_dir): run the sandbox` (socket = ipc_dir/mira.sock)."""

    def __init__(self, cfg: dict, ipc: Path):
        self.broker, self.path = Broker(cfg), ipc / "mira.sock"
        broker = self.broker

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                data = b""
                while len(data) <= MAX_REQUEST:
                    chunk = self.request.recv(1 << 16)
                    if not chunk:
                        break
                    data += chunk
                try:
                    resp = broker.handle(json.loads(data)) if len(data) <= MAX_REQUEST else broker.refuse("request too large")
                except Exception as e:  # never kill the broker on a bad request
                    resp = broker.refuse(f"broker error: {type(e).__name__}")
                self.request.sendall(json.dumps(resp).encode())

        class Srv(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self.srv = Srv(str(self.path), H)
        os.chmod(self.path, 0o600)

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()


# --------------------------------------------------------------------------- commands
def prompt_text(cfg: dict) -> str:
    sh = sdir(cfg["session_id"]) / "sh"
    rounds = "unlimited" if cfg["rounds"] is None else cfg["rounds"]
    ex = ", ".join(x for x in cfg["extras"].split(",") if x) or "none"
    arm = ("`mira build --file prog.py` (a Python program against the Part API; read /mira/docs/BUILDER.md and `mira guide`)"
           if cfg["arm"] == "builder" else "`mira set X Y Z STATE`, `mira set --file blocks.json`, `mira fill ...`, `mira remove ...`")
    lib = ("\nThe component library is available: `mira components QUERY` searches it, `mira build --file prog.py --components name1,name2` "
           "prepends the chosen components, and /mira/lib and /mira/docs/LIBRARY.md document them.\n" if cfg["variant"] == "lib" else "")
    return f"""You are an expert Minecraft redstone engineer. Attempt MIRA task `{cfg['task']}`.

Your shell is sandboxed. Run EVERY shell command as
    {sh} -c '<command>'
Inside it the only interface to the simulator is the `mira` command; /tmp/work is your writable directory. The sandbox has no network
and no copy of the repository: do not look for one, and do not use any other tool to read files outside /tmp/work.

Use only: `mira desc` (task text), `mira guide`, {arm}, `mira view [--layer N]`, `mira run-spec` (at most {rounds} calls; each simulates
your build on the PUBLIC vectors), `mira undo`, and `mira submit` exactly once when you are done or out of options (submit ends the task
and scores hidden checks too). Extras enabled: {ex}.{lib}
Plan the signal path first, build a complete candidate, then iterate on the feedback. At the end reply with one line: what you built and
whether you believe it passes.
"""


def cmd_start(a):
    sys.path.insert(0, str(ROOT))
    from mira_mcp import session as S
    extras = ",".join(x for x in ["components" if a.variant == "lib" else "", a.extras] if x)
    rounds = None if str(a.rounds).lower() in ("inf", "unlimited", "-1") else int(a.rounds)
    try:
        s = S.start(a.task, a.arm, rounds, extras, a.id, a.seed, a.label)
    except (S.SessionError, ValueError) as e:
        sys.exit(f"error: {e}")
    cfg = {"session_id": s.id, "task": a.task, "arm": a.arm, "rounds": rounds, "extras": extras, "variant": a.variant,
           "label": a.label, "with_sim": a.with_sim, "python": sys.executable, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "sessions_dir": str(S.sessions_dir())}
    d = sdir(s.id)
    (d / "work").mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg, indent=1))
    sh = d / "sh"
    sh.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 'scripts/run_baseline_sandboxed.py'))} exec {shlex.quote(s.id)} -- \"$@\"\n")
    sh.chmod(0o755)
    (d / "PROMPT.md").write_text(prompt_text(cfg))
    print(f"session {s.id}\nsandbox dir {d}\nwrapper     {sh}\n\n----- agent prompt (also in {d / 'PROMPT.md'}) -----\n{prompt_text(cfg)}")


def cmd_exec(a):
    cfg = load_cfg(a.sid)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        cmd = ["/bin/bash"]
    elif cmd[0].startswith("-"):
        cmd = ["/bin/bash", *cmd]          # `sh -c '...'` like a shell
    append_log(sdir(a.sid) / "shell.log", f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{shlex.join(cmd)}")
    if not Path(BWRAP).exists():
        sys.exit("error: bubblewrap not found (set BWRAP)")
    os.environ.setdefault("MIRA_SESSIONS_DIR", cfg["sessions_dir"])
    ipc = Path(tempfile.mkdtemp(prefix="mipc-", dir="/tmp"))      # one socket per wrapped command (short path: sun_path is 108 bytes)
    try:
        with BrokerServer(cfg, ipc):
            return subprocess.call([*bwrap_argv(cfg, ipc, net=a.net), "--", *cmd])
    finally:
        shutil.rmtree(ipc, ignore_errors=True)


def cmd_status(a):
    cfg = load_cfg(a.sid)
    d = sdir(a.sid)
    n = lambda f: sum(1 for _ in open(d / f)) if (d / f).exists() else 0
    print(json.dumps({"session": a.sid, "task": cfg["task"], "variant": cfg["variant"],
                      "cli_calls": n("broker.jsonl"), "shell_commands": n("shell.log")}))


def cmd_record_usage(a):
    d = sdir(a.sid)
    p = d / "usage.json"
    u = json.loads(p.read_text()) if p.exists() else {}
    for k in ("model", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "turns", "notes"):
        v = getattr(a, k)
        if v is not None:
            u[k] = v
    if a.transcript:
        dst = d / ("agent_transcript" + Path(a.transcript).suffix)
        shutil.copyfile(a.transcript, dst)
        u["transcript"] = dst.name
    p.write_text(json.dumps(u, indent=1))
    print(json.dumps(u))


def audit_text(text: str):
    """Lines of `text` that mention a path the agent must not read (allowed docs and /mira/... excluded)."""
    hits = []
    for i, ln in enumerate(text.splitlines(), 1):
        m = FORBIDDEN_RE.search(ln.replace("/mira/docs/", "").replace("/mira/lib/", ""))
        if m:
            hits.append((i, m.group(0), ln.strip()[:200]))
    return hits


def cmd_audit(a):
    total = 0
    srcs = [Path(t) for t in a.transcript] + ([sdir(a.sid) / "shell.log"] if (sdir(a.sid) / "shell.log").exists() else [])
    for p in srcs:
        for i, what, ln in audit_text(p.read_text(errors="replace")):
            print(f"{p.name}:{i}: [{what}] {ln}")
            total += 1
    print(f"{total} suspicious lines in {len(srcs)} files (heuristic: path mentions; the sandbox itself is the enforcement)")
    return 1 if total else 0


def cmd_finish(a):
    sys.path.insert(0, str(ROOT))
    from mira_mcp import session as S
    cfg = load_cfg(a.sid)
    d = sdir(a.sid)
    os.environ["MIRA_SESSIONS_DIR"] = cfg["sessions_dir"]
    s = S.Session.load(a.sid)
    st = s.state()
    submitted_by_agent = bool(st.get("done"))
    if not submitted_by_agent and a.submit_open:
        s.call("submit")
        st = s.state()
    src = Path(cfg["sessions_dir"]) / a.sid
    shutil.rmtree(d / "session", ignore_errors=True)
    shutil.copytree(src, d / "session")
    res = json.loads((src / "result.json").read_text()) if (src / "result.json").exists() else None
    n = lambda f: sum(1 for _ in open(d / f)) if (d / f).exists() else 0
    audit = []
    for p in [d / "shell.log", d / "agent_transcript.jsonl", d / "agent_transcript.txt", d / "agent_transcript.md"]:
        if p.exists():
            audit += [f"{p.name}:{i}: {ln}" for i, _w, ln in audit_text(p.read_text(errors="replace"))]
    summary = {"session": a.sid, "task": cfg["task"], "variant": cfg["variant"], "arm": cfg["arm"], "rounds": cfg["rounds"], "label": cfg["label"],
               "submitted_by_agent": submitted_by_agent, "submitted_by_runner": bool(st.get("done")) and not submitted_by_agent,
               "result": {k: res[k] for k in ("reward", "full_pass", "r3", "r4", "blocks", "hidden", "rounds_used", "wall_s") if res and k in res} or None,
               "cli_calls": n("broker.jsonl"), "shell_commands": n("shell.log"),
               "usage": json.loads((d / "usage.json").read_text()) if (d / "usage.json").exists() else None,
               "suspicious_path_mentions": audit[:20]}
    (d / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start")
    p.add_argument("task"); p.add_argument("--arm", default="builder", choices=("builder", "blocks"))
    p.add_argument("--rounds", default="15", help="run_spec budget: N or 'inf'"); p.add_argument("--variant", default="raw", choices=("raw", "lib"))
    p.add_argument("--extras", default="", help="extra tools beyond the variant's (probe,corpus)"); p.add_argument("--label", default="")
    p.add_argument("--id", default=None); p.add_argument("--seed", type=int, default=0)
    p.add_argument("--with-sim", action="store_true", help="also mount venv + builder + env code (never env/tasks) for offline experiments")
    p = sub.add_parser("exec"); p.add_argument("sid"); p.add_argument("--net", action="store_true"); p.add_argument("command", nargs=argparse.REMAINDER)
    p = sub.add_parser("status"); p.add_argument("sid")
    p = sub.add_parser("record-usage"); p.add_argument("sid"); p.add_argument("--model")
    p.add_argument("--input-tokens", dest="input_tokens", type=int); p.add_argument("--output-tokens", dest="output_tokens", type=int)
    p.add_argument("--cache-read-tokens", dest="cache_read_tokens", type=int); p.add_argument("--cost-usd", dest="cost_usd", type=float)
    p.add_argument("--turns", type=int); p.add_argument("--notes"); p.add_argument("--transcript", help="the agent's own transcript to archive")
    p = sub.add_parser("audit"); p.add_argument("sid"); p.add_argument("--transcript", action="append", default=[])
    p = sub.add_parser("finish"); p.add_argument("sid"); p.add_argument("--submit-open", action="store_true", help="score an unsubmitted attempt as it stands")
    a = ap.parse_args(argv)
    if a.cmd == "start":
        return cmd_start(a)
    a.sid = getattr(a, "sid", None)
    return {"exec": cmd_exec, "status": cmd_status, "record-usage": cmd_record_usage,
            "audit": cmd_audit, "finish": cmd_finish}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main() or 0)
