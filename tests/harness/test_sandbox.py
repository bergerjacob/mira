"""scripts/run_baseline_sandboxed.py: an agent in the sandbox reaches the env only through the CLI, never the answer files.

Needs bubblewrap with user namespaces; skipped where it cannot start a sandbox.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load():
    spec = importlib.util.spec_from_file_location("run_baseline_sandboxed", ROOT / "scripts/run_baseline_sandboxed.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["run_baseline_sandboxed"] = m
    spec.loader.exec_module(m)
    return m


RB = _load()


def _bwrap_works():
    try:
        r = subprocess.run([RB.BWRAP, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-user", "--unshare-net", "--", "/usr/bin/true"], capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap with user namespaces unavailable")


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A started sandboxed session for t0_not (builder arm, R=2) in a temp store; returns (sid, run) with run(cmd) -> CompletedProcess."""
    monkeypatch.setattr(RB, "SANDBOX_ROOT", tmp_path / "sandbox")
    monkeypatch.setenv("MIRA_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(sys, "argv", ["x"])
    RB.main(["start", "t0_not", "--arm", "builder", "--rounds", "2", "--id", "sbx-test", "--label", "pytest"])

    def run(cmd, **kw):
        return subprocess.run([str(tmp_path / "sandbox/sbx-test/sh"), "-c", cmd], capture_output=True, text=True, timeout=120,
                              env={"PATH": "/usr/bin:/bin", "MIRA_SANDBOX_DIR": str(tmp_path / "sandbox"),
                                   "MIRA_SESSIONS_DIR": str(tmp_path / "sessions")}, **kw)
    return "sbx-test", run


def test_repo_answer_files_are_not_visible(box):
    _, run = box
    for p in ("env/tasks/t0_not.json", "env/tasks/author.py", "lib/full_adder.py", "data/sessions", "tests/env", "harness/reference.py",
              "docs/STATUS_2026-11.md", "docs/LIBRARY.md", ".env", "simulation/builder.py"):
        for base in (str(ROOT), "/mira"):
            r = run(f"cat {base}/{p} || ls {base}/{p}")
            assert r.returncode != 0 and "No such file" in r.stderr, (base, p, r.stdout[:200])
    assert run("ls /home /root /workspace 2>&1; ls /mira").stdout.split().count("env") == 0
    assert run("find / -name 't0_not*' -not -path '/proc/*' 2>/dev/null | head").stdout.strip() == ""


def test_allowed_docs_are_readable_but_read_only(box):
    _, run = box
    assert "Part" in run("head -c 3000 /mira/docs/BUILDER.md").stdout
    assert run("test -f /mira/docs/MCP.md && test -f /mira/evaluation/prompts/builder_system.md && echo ok").stdout.strip() == "ok"
    assert run("echo x > /mira/docs/BUILDER.md").returncode != 0
    assert run("echo x > /mira/docs/new.md").returncode != 0           # the tmpfs root is read-only too
    assert run("echo x > /usr/bin/evil").returncode != 0
    assert run("echo hello > /tmp/work/a.txt && cat /tmp/work/a.txt").stdout.strip() == "hello"


def test_no_network(box):
    _, run = box
    r = run("python3 -c \"import socket; s=socket.socket(); s.settimeout(3); s.connect(('1.1.1.1', 80))\"")
    assert r.returncode != 0 and ("unreachable" in r.stderr.lower() or "Errno 101" in r.stderr)
    assert "lo" in run("cat /proc/net/dev").stdout and "eth" not in run("cat /proc/net/dev").stdout


def test_cli_works_from_inside_and_scores(box, tmp_path):
    sid, run = box
    assert "TASK t0_not" in run("mira desc").stdout
    assert "Part" in run("mira guide").stdout or "builder" in run("mira guide").stdout.lower()
    from harness.reference import builder_source
    from env.task import load_task
    src = builder_source(load_task("t0_not").solution)
    r = run(f"cat > /tmp/work/prog.py <<'EOF'\n{src}\nEOF\nmira build --file /tmp/work/prog.py && mira run-spec && mira submit")
    assert r.returncode == 0, r.stderr
    assert "BUILD:" in r.stdout and "full" in r.stdout.lower() or "reward" in r.stdout.lower() or "HIDDEN" in r.stdout
    res = json.loads((tmp_path / "sessions" / sid / "result.json").read_text())
    assert res["full_pass"] is True and res["rounds_used"] == 1
    # stdin form
    assert run("echo 'bad program' | mira build --file -").returncode == 0


def test_agent_cannot_change_the_session_or_read_host_files_via_the_broker(box, tmp_path):
    _, run = box
    for cmd in ("mira start t0_and", "mira results", "mira sessions", "mira tasks", "mira --session other desc", "mira desc --session other"):
        r = run(cmd)
        assert r.returncode == 2 and "not available" in r.stderr or "fixed" in r.stderr, (cmd, r.stdout, r.stderr)
    # the host path of a task file is not honoured as a --file argument, even when the client is bypassed
    sock = "/ipc/mira.sock"
    evil = ("import socket,json,sys\n"
            "s=socket.socket(socket.AF_UNIX); s.connect('/ipc/mira.sock')\n"
            f"s.sendall(json.dumps({{'argv':['build','--file','{ROOT}/env/tasks/t0_not.json'],'files':{{}}}}).encode()); s.shutdown(1)\n"
            "print(s.recv(100000).decode())\n")
    r = run(f"python3 - <<'EOF'\n{evil}\nEOF")
    assert "must name a file inside the sandbox" in r.stdout and "contract" not in r.stdout
    assert run("mira show").stdout.count("calls") == 1


def test_blocks_arm_set_goes_through_and_run_spec_budget_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "SANDBOX_ROOT", tmp_path / "sandbox")
    monkeypatch.setenv("MIRA_SESSIONS_DIR", str(tmp_path / "sessions"))
    RB.main(["start", "t0_lamp", "--arm", "blocks", "--rounds", "1", "--id", "sbx-b", "--label", "pytest"])
    sh = str(tmp_path / "sandbox/sbx-b/sh")

    def run(cmd):
        return subprocess.run([sh, "-c", cmd], capture_output=True, text=True, timeout=120,
                              env={"PATH": "/usr/bin:/bin", "MIRA_SANDBOX_DIR": str(tmp_path / "sandbox"), "MIRA_SESSIONS_DIR": str(tmp_path / "sessions")})
    assert run("mira set 1 1 1 minecraft:stone").returncode == 0
    assert "run_spec 1/1 used" in run("mira run-spec").stdout
    second = run("mira run-spec")
    assert second.returncode == 0 and "budget" in second.stdout.lower()
    assert "run_spec used 1" in run("mira show").stdout


def test_lib_variant_exposes_only_lib_sources_and_library_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "SANDBOX_ROOT", tmp_path / "sandbox")
    monkeypatch.setenv("MIRA_SESSIONS_DIR", str(tmp_path / "sessions"))
    RB.main(["start", "t3_adder2", "--arm", "builder", "--rounds", "2", "--variant", "lib", "--id", "sbx-l", "--label", "pytest"])
    sh = str(tmp_path / "sandbox/sbx-l/sh")
    env = {"PATH": "/usr/bin:/bin", "MIRA_SANDBOX_DIR": str(tmp_path / "sandbox"), "MIRA_SESSIONS_DIR": str(tmp_path / "sessions")}
    r = subprocess.run([sh, "-c", "ls /mira/lib | head -50; test -f /mira/docs/LIBRARY.md && echo LIBDOC; ls /mira/env /mira/data 2>&1 | head -3"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert "full_adder.py" in r.stdout and "full_adder.meta.json" in r.stdout and "LIBDOC" in r.stdout
    assert "No such file" in r.stdout
    assert json.loads((tmp_path / "sandbox/sbx-l/config.json").read_text())["extras"] == "components"
    # the search tool is enabled for this variant
    r = subprocess.run([sh, "-c", "mira components adder | head -5"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "error: extras" not in r.stdout.lower()


def test_with_sim_mounts_env_code_but_never_env_tasks(tmp_path, monkeypatch):
    if not (ROOT / ".venv/bin/python").exists():
        pytest.skip("no venv")
    monkeypatch.setattr(RB, "SANDBOX_ROOT", tmp_path / "sandbox")
    monkeypatch.setenv("MIRA_SESSIONS_DIR", str(tmp_path / "sessions"))
    RB.main(["start", "t0_not", "--with-sim", "--id", "sbx-s", "--label", "pytest"])
    sh = str(tmp_path / "sandbox/sbx-s/sh")
    env = {"PATH": "/usr/bin:/bin", "MIRA_SANDBOX_DIR": str(tmp_path / "sandbox"), "MIRA_SESSIONS_DIR": str(tmp_path / "sessions")}
    r = subprocess.run([sh, "-c", "ls /mira/env | head -3; ls -A /mira/env/tasks | wc -l; cat /mira/env/tasks/t0_not.json 2>&1 | head -c 80"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert "contract.py" in r.stdout or "blocks.py" in r.stdout
    assert r.stdout.split("\n")[-3].strip() == "0" or "No such file" in r.stdout


def test_audit_flags_forbidden_paths_and_ignores_allowed_ones():
    hits = RB.audit_text("cat /mira/docs/BUILDER.md\nless env/tasks/t3_adder4.json\nopen('lib/full_adder.py')\ngrep x data/sessions/a/sources/001.py\n"
                         "mira build --file /tmp/work/prog.py\nread docs/STATUS_2026-11.md\n")
    assert [h[0] for h in hits] == [2, 3, 4, 6]


def test_unknown_session_and_usage_record(box, tmp_path):
    sid, _ = box
    RB.main(["record-usage", sid, "--model", "sonnet-5-5", "--input-tokens", "1000", "--output-tokens", "200"])
    u = json.loads((tmp_path / "sandbox" / sid / "usage.json").read_text())
    assert u["input_tokens"] == 1000 and u["model"] == "sonnet-5-5"
    RB.main(["finish", sid, "--submit-open"])
    s = json.loads((tmp_path / "sandbox" / sid / "summary.json").read_text())
    assert s["submitted_by_runner"] is True and s["usage"]["output_tokens"] == 200 and (tmp_path / "sandbox" / sid / "session" / "transcript.jsonl").exists()
