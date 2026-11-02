"""mira: project-mode CLI (build program + modules + spec + notes + views + tests + export). See docs/PRODUCT.md.

    .venv/bin/python scripts/mira.py init NAME [--from-task ID] [--from-litematic FILE] [--program FILE] [--dir PATH]
    .venv/bin/python scripts/mira.py -p NAME build | overview | view --layer Y | find PAT | query X Y Z | diff | test
                                              | trace PORT... | notes-check | export | paste-check | log | init-module NAME

The project is chosen by -p/--project NAME|PATH, else $MIRA_PROJECT, else the project containing the current directory.
Output is the command's text; exit code 1 when it starts with 'error:' / FAILED / FAIL, 0 otherwise.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mira_mcp import project as PJ  # noqa: E402


def parser():
    ap = argparse.ArgumentParser(prog="mira", description=__doc__.splitlines()[0])
    ap.add_argument("-p", "--project", help="project name (data/projects/NAME) or path")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="create a project")
    s.add_argument("name")
    s.add_argument("--from-task", help="copy a task's contract into spec.json (e.g. t4_addsub8)")
    s.add_argument("--from-litematic", help="decompile a .litematic/.schem into build.py")
    s.add_argument("--program", help="copy this Part API program into build.py")
    s.add_argument("--dir", help="project directory (default data/projects/NAME)")
    s.add_argument("--force", action="store_true", help="re-initialise an existing project (files are kept)")
    s.add_argument("--lenient", action="store_true", help="accept block ids/states unknown to the 1.21 validator (implied by --from-litematic)")
    s = sub.add_parser("build", help="compile build.py (+ imports) and store a revision")
    s.add_argument("-m", "--message", default="")
    s = sub.add_parser("overview", help="multi-resolution summary (< 80 lines for any size)")
    s.add_argument("--rev")
    s = sub.add_parser("view", help="ASCII slice of one layer")
    s.add_argument("--layer", "-y", type=int, required=True)
    s.add_argument("--box", type=int, nargs=4, metavar=("X0", "Z0", "X1", "Z1"))
    s.add_argument("--rev")
    s = sub.add_parser("find", help="ports, labels, instances, files and block ids matching a pattern (substring or glob)")
    s.add_argument("pattern")
    s.add_argument("--rev")
    s = sub.add_parser("query", help="block states (and which source line wrote them) around a point")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)
    s.add_argument("z", type=int)
    s.add_argument("--radius", "-r", type=int, default=0)
    s.add_argument("--rev")
    s = sub.add_parser("diff", help="block-level diff between revisions (default: previous -> latest)")
    s.add_argument("a", nargs="?")
    s.add_argument("b", nargs="?")
    s.add_argument("--examples", type=int, default=10)
    s = sub.add_parser("test", help="run spec.json (or a module spec) in the simulator")
    s.add_argument("--module")
    s.add_argument("--public-only", action="store_true")
    s.add_argument("--fast", action="store_true", help="skip the determinism re-run")
    s = sub.add_parser("trace", help="ASCII timing diagram of ports / blocks under the test stimulus")
    s.add_argument("signals", nargs="*", help="spec port, build port or x,y,z (default: spec outputs)")
    s.add_argument("--ticks", type=int, help="idle run of N ticks instead of the spec stimulus")
    s.add_argument("--seq", help="a named spec sequence")
    s.add_argument("--set", dest="sets", help='custom vectors: "A=1,B=0;A=0"')
    s.add_argument("--vector", type=int, help="show only test vector K (state from the preceding vectors is kept)")
    s.add_argument("--all", action="store_true", help="all vectors, not only public ones")
    s.add_argument("--module")
    s.add_argument("--width", type=int, default=100)
    s = sub.add_parser("notes-check", help="lint NOTES.md references against the latest build")
    s.add_argument("--rev")
    s = sub.add_parser("export", help="settled at-rest .litematic (+ vanilla structure .nbt)")
    s.add_argument("--litematic", help="output path (default .mira/exports/NAME-rREV.litematic)")
    s.add_argument("--nbt", help="also write a vanilla structure .nbt here")
    s.add_argument("--rev")
    s.add_argument("--no-fixed", action="store_true", help="leave out the spec's fixed scenery (floor, levers, lamps)")
    s.add_argument("--force", action="store_true", help="export even if the build never comes to rest")
    s = sub.add_parser("paste-check", help="paste the export into vanilla 26.2 (oracle), idle, read back, diff -> receipt")
    s.add_argument("--file", help="check this .litematic instead of the latest export")
    s.add_argument("--ticks", type=int, default=40)
    s.add_argument("--modes", default="known_shape,place")
    s = sub.add_parser("log", help="revisions")
    s.add_argument("-n", type=int, default=20)
    s = sub.add_parser("init-module", help="modules/NAME.py template + modules/NAME.spec.json skeleton from its ports")
    s.add_argument("name")
    s.add_argument("--part")
    return ap


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    if a.cmd == "init":
        out = PJ.run(a.name, "init", name=a.name, path=a.dir, from_task=a.from_task, from_litematic=a.from_litematic,
                     program=a.program, force=a.force, lenient=a.lenient)
        print(out)
        return 1 if out.startswith("error:") else 0
    try:
        proj = PJ.resolve(a.project) if a.project else (PJ.resolve(os.environ["MIRA_PROJECT"]) if os.environ.get("MIRA_PROJECT")
                                                         else PJ.find_project())
    except PJ.ProjectError as e:
        print(f"error: {e}")
        return 1
    kw = {k: v for k, v in vars(a).items() if k not in ("cmd", "project") and v is not None}
    if a.cmd == "trace":
        kw["public_only"] = None if not a.all else False
        kw.pop("all", None)
    if a.cmd == "export":
        kw["include_fixed"] = not kw.pop("no_fixed", False)
    if a.cmd == "paste-check":
        kw["modes"] = tuple(m.strip() for m in a.modes.split(",") if m.strip())
    if a.cmd == "init-module":
        kw["name"] = a.name
    out = PJ.run(proj, a.cmd, **kw)
    print(out)
    head = out.split("\n", 1)[0]
    return 1 if head.startswith("error:") or "FAILED" in head or " FAIL" in head or "REFUSED" in head else 0


if __name__ == "__main__":
    sys.exit(main())
