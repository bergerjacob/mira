"""Project mode: a contraption as a code project (program + modules + spec + notes + views + tests + history).

A project is a directory (default data/projects/<name>/, any path works):

    project.json        name, origin (task id / litematic), created
    build.py            the Part API program (defines build(b)); may import project modules and library parts:
                            from modules.alu import alu_slice     -> modules/alu.py
                            from lib.full_adder import full_adder -> lib/full_adder.py (project lib/ first, then repo lib/)
    modules/*.py        project modules; modules/NAME.spec.json = a module-level spec (contract v2 + part to place)
    spec.json           contract v2 of the whole build (env/contract.py), written by the agent or copied from a task
    NOTES.md            agent-maintained facts (named places, intent); `notes-check` lints its references
    .mira/              revisions (blocks + source snapshot + hash), last test report, exports, paste receipt, traces

Imports are resolved by pre-processing (simulation/builder.py's sandbox allows no imports besides the builder):
every imported file is concatenated, dependencies first, into one program with its import lines replaced by `pass`;
a source map turns compile errors and block provenance lines back into file:line. All files share one namespace,
so a top-level name defined in two files is an error. The concatenated program then runs in the usual sandbox.

Every public method returns compact plain text (clipped to MIRA_MAX_CHARS); `scripts/mira.py` and the project_*
MCP tools in mira_mcp/server.py are thin wrappers around them.
"""
import ast
import gzip
import json
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation import views as V  # noqa: E402

REPO_LIB = ROOT / "lib"
SOURCE_GLOBS = ("build.py", "modules/**/*.py", "lib/**/*.py", "modules/**/*.spec.json", "spec.json", "NOTES.md")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
HEADER = "from simulation.builder import *\n"
DEFAULT_TIMEOUT = 120.0     # build sandbox limit in seconds (`build --timeout N`)


class ProjectError(Exception):
    pass


def projects_dir() -> Path:
    return Path(os.environ.get("MIRA_PROJECTS_DIR", ROOT / "data/projects"))


def max_chars() -> int:
    try:
        return int(os.environ.get("MIRA_MAX_CHARS", "12000"))
    except ValueError:
        return 12000


def clip(text: str, n: Optional[int] = None) -> str:
    n = n or max_chars()
    if len(text) <= n:
        return text
    return text[:n] + f"\n... [truncated {len(text) - n} of {len(text)} chars; narrow the request (box, pattern, signals)]"


def resolve(ref) -> Path:
    """Project name (-> data/projects/<name>) or a path (anything containing a slash or starting with '.')."""
    ref = str(ref)
    if "/" in ref or ref.startswith(".") or os.sep in ref:
        return Path(ref).expanduser().resolve()
    if not NAME_RE.match(ref):
        raise ProjectError(f"bad project name {ref!r} (letters, digits, _ . - only; or give a path)")
    return (projects_dir() / ref).resolve()


def find_project(start: Optional[str] = None) -> Path:
    """The project containing `start` (default cwd): walk up to a directory with project.json."""
    p = Path(start or os.getcwd()).resolve()
    for d in [p, *p.parents]:
        if (d / "project.json").exists():
            return d
    raise ProjectError("not inside a project (no project.json found); pass --project NAME|PATH or run `init`")


BUILD_TEMPLATE = '''"""{name}: build program (Part API, docs/BUILDER.md). Defines build(b).
Imports: `from modules.NAME import part_or_helper` (modules/NAME.py), `from lib.NAME import NAME` (library part)."""
from simulation.builder import *
{task_note}

def build(b):
    pass
'''

NOTES_TEMPLATE = """# {name}: notes

Agent-maintained facts about this build. Keep them true: `mira notes-check` reports references that no longer exist.

## Intent
- (what the contraption does, inputs/outputs, timing assumptions)

## Named places
<!-- Reference things so the linter can check them: port:NAME, label:NAME, module:NAME (a modules/*.py file or an
instance group), inst:PATH, or a coordinate written as (x, y, z). Plain `backticked` names are checked too when they
were ever a port/label/instance/module in this project (renamed or deleted -> reported as stale). -->
{places}

## Decisions / gotchas
-

## Open problems
-
"""

AGENTS_TEMPLATE = """# {name}: MIRA project

This directory is a Minecraft contraption as a code project. Work here with your normal file tools plus the `mira` CLI.
Files: `spec.json` (what "works" means; write it first), `build.py` + `modules/` (Part API program), `NOTES.md` (intent, assumptions, named places), `.mira/` (generated: revisions, reports, exports - do not edit).
Loop: `mira build` -> `mira overview` -> `mira test` -> `mira trace PORT --seq NAME` -> fix -> `mira spec-check` -> `mira verify-vanilla` -> `mira export --litematic OUT` -> `mira paste-check`.
Full guide: the `mira` skill (skills/mira/SKILL.md) and docs/PRODUCT.md in the MIRA repo.
Hand over the exported .litematic, not a raw block list. Spec the timing real users produce; write assumptions in NOTES.md.
"""

MODULE_TEMPLATE = '''"""{name}: project module. Import from build.py with `from modules.{name} import {name}`.
Ports registered with p.port(...) show up as `<instance>.<port>` in the build and in module specs."""
from simulation.builder import *


@part
def {name}(p, floor=True):
    pass
'''


# --------------------------------------------------------------------------- source assembly (imports -> one program)
class Segment:
    __slots__ = ("file", "start", "n")

    def __init__(self, file: str, start: int, n: int):
        self.file, self.start, self.n = file, start, n   # combined lines start .. start+n-1 are file lines 1..n


def _top_names(tree: ast.Module) -> Dict[str, int]:
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node.lineno
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        out[n.id] = node.lineno
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            out[node.target.id] = node.lineno
    return out


class Assembly:
    """build.py (or a synthetic entry) + everything it imports, concatenated, with a source map."""

    def __init__(self, pdir: Path, entry: str = "build.py", entry_text: Optional[str] = None):
        self.pdir, self.errors, self.segments, self.files = pdir, [], [], []
        self.lib_from_repo: Dict[str, bool] = {}
        self._texts: Dict[str, str] = {}
        if entry_text is not None:
            self._texts[entry] = entry_text
        parts = [HEADER]
        line = 2
        for rel, text in self._order(entry):
            parts.append(f"# --- {rel}\n")
            line += 1
            body = text if text.endswith("\n") else text + "\n"
            n = body.count("\n")
            self.segments.append(Segment(rel, line, n))
            self.files.append(rel)
            parts.append(body)
            line += n
        self.source = "".join(parts)
        self._dupes()

    # file access, confined to the project dir and the repo library
    def _path(self, rel: str) -> Path:
        if rel.startswith("lib/"):
            p = self.pdir / rel
            if p.exists():
                self.lib_from_repo[rel] = False
                return p
            self.lib_from_repo[rel] = True
            return REPO_LIB / rel[4:]
        return self.pdir / rel

    def read(self, rel: str) -> Optional[str]:
        if rel in self._texts:
            return self._texts[rel]
        p = self._path(rel)
        try:
            self._texts[rel] = p.read_text()
        except OSError:
            return None
        return self._texts[rel]

    def _resolve(self, module: str) -> Optional[str]:
        segs = module.split(".")
        if segs[0] not in ("modules", "lib") or len(segs) < 2 or not all(s.isidentifier() for s in segs):
            return None
        return "/".join(segs) + ".py"

    def _order(self, entry: str) -> List[Tuple[str, str]]:
        out, done = [], set()
        trees: Dict[str, ast.Module] = {}

        def visit(rel: str, chain: List[str], where: str):
            if rel in done:
                return
            if rel in chain:
                self.errors.append(f"{where}: import cycle {' -> '.join(chain + [rel])}")
                return
            text = self.read(rel)
            if text is None:
                self.errors.append(f"{where}: cannot find {rel}" + (" (project lib/ or repo lib/)" if rel.startswith("lib/") else ""))
                return
            try:
                tree = ast.parse(text, rel)
            except SyntaxError as e:
                self.errors.append(f"{rel}:{e.lineno}: syntax: {e.msg}")
                return
            trees[rel] = tree
            lines = text.split("\n")
            deps = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(a.name.split(".")[0] in ("modules", "lib") for a in node.names):
                    self.errors.append(f"{rel}:{node.lineno}: write `from {node.names[0].name} import NAME` (plain `import` of project modules is not supported)")
                elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in ("modules", "lib") and not node.level:
                    dep = self._resolve(node.module)
                    if dep is None:
                        self.errors.append(f"{rel}:{node.lineno}: bad module path {node.module!r} (use modules.NAME or lib.NAME)")
                        continue
                    deps.append((dep, node))
                    indent = lines[node.lineno - 1][:node.col_offset]
                    lines[node.lineno - 1] = indent + "pass  # " + lines[node.lineno - 1].strip()[:60]
                    for k in range(node.lineno, node.end_lineno or node.lineno):
                        lines[k] = ""
            for dep, node in deps:
                if dep.startswith("lib/") and self._path(dep) and self.lib_from_repo.get(dep):
                    try:   # repo library parts may require others (lib/<name>.meta.json "requires")
                        from env import library
                        for r in library.closure(dep[4:-3])[:-1]:
                            visit(f"lib/{r}.py", chain + [rel], f"{rel}:{node.lineno}")
                    except Exception:   # noqa: BLE001 - no meta: no extra requirements
                        pass
                visit(dep, chain + [rel], f"{rel}:{node.lineno}")
                if dep in trees and not any(a.name == "*" for a in node.names):
                    names = _top_names(trees[dep])
                    for a in node.names:
                        if a.name not in names:
                            self.errors.append(f"{rel}:{node.lineno}: {dep} has no top-level name {a.name!r}")
                        elif a.asname and a.asname != a.name:
                            self.errors.append(f"{rel}:{node.lineno}: `import ... as` is not supported (all files share one namespace)")
            done.add(rel)
            out.append((rel, "\n".join(lines)))
        visit(entry, [], entry)
        self._trees = trees
        self._entry = entry
        return out

    def _dupes(self):
        seen: Dict[str, str] = {}
        for rel in self.files:
            for name, line in _top_names(self._trees[rel]).items():
                if name == "build" and rel != self._entry:
                    self.errors.append(f"{rel}:{line}: only {self._entry} may define build()")
                elif name in seen and name != "build":
                    self.errors.append(f"{rel}:{line}: {name!r} is already defined in {seen[name]} (all project files share one namespace)")
                else:
                    seen.setdefault(name, f"{rel}:{line}")

    def where(self, line: Optional[int]) -> Tuple[str, Optional[int]]:
        """combined line -> (file, line in that file)."""
        if line is None:
            return "?", None
        for s in self.segments:
            if s.start <= line < s.start + s.n:
                return s.file, line - s.start + 1
        return "<prelude>", line

    def to_dict(self):
        return [[s.file, s.start, s.n] for s in self.segments]


def where_from_map(segments, line):
    if line is None:
        return "?", None
    for f, start, n in segments:
        if start <= line < start + n:
            return f, line - start + 1
    return "<prelude>", line


# --------------------------------------------------------------------------- stored revisions
def _gz_write(path: Path, obj):
    with gzip.open(path, "wt") as fh:
        json.dump(obj, fh, separators=(",", ":"))


def _gz_read(path: Path):
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


class Rev:
    """A stored successful build: blocks, provenance, ports, labels, instances, source map."""

    def __init__(self, pdir: Path, n: int):
        self.n, self.dir = n, pdir / ".mira" / "revs" / f"{n:04d}"
        if not self.dir.exists():
            raise ProjectError(f"no revision {n}")
        self.meta = json.loads((self.dir / "meta.json").read_text())
        self._data = None

    @property
    def data(self) -> dict:
        if self._data is None:
            self._data = _gz_read(self.dir / "build.json.gz")
        return self._data

    @property
    def cells(self) -> Dict[tuple, str]:
        return {(b[0], b[1], b[2]): b[3] for b in self.data["blocks"]}

    def file_of(self, line):
        return where_from_map(self.data["segments"], line)

    def groups(self) -> Dict[tuple, str]:
        """pos -> module group: instance group (first path segment) or the source file of root-level blocks."""
        out = {}
        segs = self.data["segments"]
        for b, (path, line) in zip(self.data["blocks"], self.data["prov"]):
            out[(b[0], b[1], b[2])] = V.group_key(path) if path else where_from_map(segs, line)[0]
        return out


# --------------------------------------------------------------------------- the project
class Project:
    def __init__(self, path):
        self.dir = Path(path).resolve()
        if not (self.dir / "project.json").exists():
            raise ProjectError(f"{self.dir} is not a project (no project.json); create it with `init`")
        self.meta = json.loads((self.dir / "project.json").read_text())
        self.mira = self.dir / ".mira"
        (self.mira / "revs").mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- init
    @classmethod
    def init(cls, name: str, path=None, from_task: Optional[str] = None, from_litematic: Optional[str] = None,
             program: Optional[str] = None, force: bool = False, lenient: bool = False) -> Tuple["Project", str]:
        pdir = Path(path).expanduser().resolve() if path else resolve(name)
        if (pdir / "project.json").exists() and not force:
            raise ProjectError(f"{pdir} already is a project (use --force to re-initialise; files are kept)")
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "modules").mkdir(exist_ok=True)
        meta = {"name": name, "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": None, "origin": "new",
                "lenient": bool(lenient or from_litematic)}
        out = [f"initialised project {name!r} at {pdir}"]
        contract = None
        if from_task:
            from env.task import load_task
            from env import library  # noqa: F401  (registers the T3/T4 reference models)
            t = load_task(from_task)
            contract = t.contract
            src = Path(t.path) if t.path else None
            d = json.loads(src.read_text())["contract"] if src else contract.to_dict()
            (pdir / "spec.json").write_text(json.dumps(d, indent=1))
            meta["task"], meta["origin"] = t.id, f"task:{t.id}"
            out.append(f"spec.json <- task {t.id} ({len(contract.inputs)} inputs, {len(contract.outputs)} outputs, "
                       f"build box {contract.build_box}); fixed blocks are part of the world, not of build.py")
        if from_litematic:
            from simulation import builder as B
            text = B.decompile(from_litematic)
            (pdir / "build.py").write_text(f"# decompiled from {Path(from_litematic).name} (lossless round trip verified)\n" + text)
            meta["origin"] = f"litematic:{Path(from_litematic).name}"
            out.append(f"build.py <- decompiled {from_litematic} ({text.count(chr(10))} lines)")
        elif program:
            (pdir / "build.py").write_text(Path(program).read_text())
            meta["origin"] = (meta["origin"] + "+" if from_task else "") + f"program:{Path(program).name}"
            out.append(f"build.py <- {program}")
        elif not (pdir / "build.py").exists():
            note = ""
            if contract is not None:
                lines = [f"# task {contract.id}: place blocks only inside build_box {contract.build_box}"]
                for p in contract.ports:
                    lines.append(f"#   {p.name}: {'in ' if p.is_input else 'out'} {p.kind} at {list(p.pos)}" + (f" - {p.doc}" if p.doc else ""))
                note = "\n".join(lines[:40]) + "\n"
            (pdir / "build.py").write_text(BUILD_TEMPLATE.format(name=name, task_note=note))
            out.append("build.py <- template")
        if not (pdir / "AGENTS.md").exists():
            (pdir / "AGENTS.md").write_text(AGENTS_TEMPLATE.format(name=name))
            out.append("AGENTS.md <- template")
        if not (pdir / "NOTES.md").exists():
            places = "\n".join(f"- port:{p.name} at ({p.pos[0]}, {p.pos[1]}, {p.pos[2]}): {p.doc or p.kind}"
                               for p in (contract.ports if contract else [])[:40]) or "- "
            (pdir / "NOTES.md").write_text(NOTES_TEMPLATE.format(name=name, places=places))
            out.append("NOTES.md <- template")
        (pdir / "project.json").write_text(json.dumps(meta, indent=1))
        prj = cls(pdir)
        out.append("next: edit build.py / modules/*.py, then `build`, `overview`, `test`")
        return prj, "\n".join(out)

    # ---------------------------------------------------------------- helpers
    @property
    def name(self) -> str:
        return self.meta.get("name", self.dir.name)

    def contract(self, module: Optional[str] = None):
        """(Contract, extra) of spec.json or modules/<module>.spec.json; None when absent. Raises on invalid specs."""
        from env.contract import Contract
        from env import library  # noqa: F401  (reference models)
        p = self.dir / ("spec.json" if module is None else f"modules/{module}.spec.json")
        if not p.exists():
            return None, {}
        try:
            d = json.loads(p.read_text())
        except ValueError as e:
            raise ProjectError(f"{p.name}: invalid JSON: {e}") from None
        extra = {}
        if module is not None:
            extra = {k: d.get(k) for k in ("part", "params", "at", "rot", "mirror")}
            d = d.get("contract", d)
        try:
            c = Contract.from_dict(d)
        except (TypeError, KeyError, ValueError) as e:
            raise ProjectError(f"{p.name}: not a contract v2 ({type(e).__name__}: {e}); see docs/ENV.md") from None
        errs = c.validate()
        if errs:
            raise ProjectError(f"{p.name} does not validate: " + "; ".join(errs[:6]))
        return c, extra

    def fixed(self, contract=None) -> Dict[tuple, str]:
        if contract is None:
            try:
                contract, _ = self.contract()
            except ProjectError:
                return {}
        if contract is None:
            return {}
        from env.runner import fixed_cells
        return {p: s for p, s in fixed_cells(contract).items() if not s.endswith(":air")}

    def revs(self) -> List[int]:
        return sorted(int(p.name) for p in (self.mira / "revs").glob("[0-9]*") if (p / "meta.json").exists())

    def rev(self, n=None) -> Rev:
        rs = self.revs()
        if not rs:
            raise ProjectError("no successful build yet; run `build` first")
        if n is None or n == "latest":
            return Rev(self.dir, rs[-1])
        n = int(n)
        if n < 0:
            if -n > len(rs):
                raise ProjectError(f"only {len(rs)} revisions")
            return Rev(self.dir, rs[n])
        return Rev(self.dir, n)

    def source_files(self) -> List[str]:
        out = []
        for g in SOURCE_GLOBS:
            for p in sorted(self.dir.glob(g)):
                if p.is_file() and ".mira" not in p.parts:
                    out.append(str(p.relative_to(self.dir)))
        return sorted(set(out))

    def source_hash(self) -> str:
        import hashlib
        h = hashlib.sha256()
        for rel in self.source_files():
            if rel.endswith(".py"):
                h.update(rel.encode() + b"\0" + (self.dir / rel).read_bytes() + b"\0")
        return h.hexdigest()

    def world(self, rev: Rev, with_fixed: bool = True) -> Dict[tuple, str]:
        cells = dict(self.fixed()) if with_fixed else {}
        cells.update(rev.cells)
        return cells

    def _log(self, rec: dict):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
        with open(self.mira / "history.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    # ---------------------------------------------------------------- compile
    def compile(self, entry: str = "build.py", entry_text: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        from simulation import builder as B
        asm = Assembly(self.dir, entry, entry_text)
        if asm.errors:
            return asm, None
        build = B.compile(asm.source, timeout=timeout, lib_root=str(REPO_LIB), validate=not self.meta.get("lenient"))
        for e in build.errors:
            if e.kind == "timeout" or "exit -24" in e.message:   # -24 = SIGXCPU: the sandbox's CPU-time limit
                e.kind, e.message = "timeout", (f"build exceeded its {timeout:g}s limit (the sandbox stops runaway programs). "
                                                f"Raise it with `build --timeout N` (now {timeout:g}); routing many nets, big arrays and "
                                                "loops over thousands of blocks are the usual cost - route_all and arrays of parts are cheaper")
            elif "exit -9" in e.message or "exit -11" in e.message:
                e.message = f"build process was killed (memory limit 6 GiB or a crash): {e.message}"
        return asm, build

    def _issue_lines(self, asm: Assembly, issues, limit=12) -> List[str]:
        out = []
        for e in issues[:limit]:
            f, ln = asm.where(e.line)
            loc = f"{f}:{ln}" if ln else f
            msg = e.message if len(e.message) <= 1800 else e.message[:1800] + " ..."
            out.append(f"  {loc}: {e.kind}: {msg}".replace("\n", "\n    ") + (f" [in {e.path}]" if e.path else ""))
        if len(issues) > limit:
            out.append(f"  ... (+{len(issues) - limit} more)")
        return out

    def _log_lines(self, log, limit: Optional[int] = None) -> List[str]:
        """print() output of the build program (captured by the sandbox: 200 lines of 300 chars at most)."""
        if not log:
            return []
        limit = limit or int(os.environ.get("MIRA_BUILD_LOG_LINES", "40"))
        shown = log[:limit]
        L = [f"build log ({len(log)} line{'s' if len(log) != 1 else ''} printed by the program"
             + (f", first {limit}" if len(log) > limit else "") + (", sandbox cap of 200 reached" if len(log) >= 200 else "") + "):"]
        L += [f"  | {x[:200]}" for x in shown]
        return L

    def _warning_lines(self, asm: Assembly, warnings, other_limit: int = 6, route_limit: int = 12) -> List[str]:
        route = [w for w in warnings if w.kind == "route"]
        other = [w for w in warnings if w.kind != "route"]
        L = [f"  warning {x.strip()}" for x in self._issue_lines(asm, other, other_limit)]
        for w in route[:route_limit]:
            f, ln = asm.where(w.line)
            L.append(f"  WARNING route {f}:{ln}: {w.message}" if ln else f"  WARNING route: {w.message}")
        if len(route) > route_limit:
            L.append(f"  ... (+{len(route) - route_limit} more route warnings; `nets --warnings` lists every net with one)")
        return L

    def build(self, message: str = "", timeout: Optional[float] = None) -> str:
        t0 = time.time()
        timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
        asm, b = self.compile(timeout=timeout)
        rs = self.revs()
        last = f"latest good rev {rs[-1]}" if rs else "no good revision yet"
        if asm.errors or b is None or b.errors:
            lines = [f"BUILD FAILED - nothing stored ({last})"]
            lines += [f"  {e}" for e in asm.errors[:12]] if asm.errors else self._issue_lines(asm, b.errors)
            if b is not None:
                lines += self._log_lines(b.log, 20)
                lines += self._warning_lines(asm, b.warnings, 3, 6)
            (self.mira / "last_build.json").write_text(json.dumps({"ok": False, "ts": time.time(), "errors": lines[1:]}))
            return "\n".join(lines)
        h, sh = b.hash(), self.source_hash()
        prev = Rev(self.dir, rs[-1]) if rs else None
        if prev and prev.meta["hash"] == h and prev.meta.get("source_hash") == sh:
            return f"BUILD OK, unchanged: rev {prev.n} (hash {h[:12]}), {len(b.blocks)} blocks"
        n = (rs[-1] + 1) if rs else 1
        d = self.mira / "revs" / f"{n:04d}"
        (d / "src").mkdir(parents=True, exist_ok=True)
        for rel in self.source_files():
            dst = d / "src" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.dir / rel, dst)
        for rel in asm.files:   # repo library sources used, so the revision is self-contained
            if asm.lib_from_repo.get(rel):
                dst = d / "src" / "_repo" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(asm.read(rel))
        nets = b.stats.get("nets", [])
        data = {"blocks": b.blocks, "prov": b.prov, "entities": b.entities, "ports": b.ports, "labels": b.labels,
                "instances": b.instances, "warnings": [w.to_dict() for w in b.warnings], "segments": asm.to_dict(),
                "program": asm.source, "log": b.log, "nets": nets}
        _gz_write(d / "build.json.gz", data)
        bb = b.bounds()
        meta = {"rev": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hash": h, "source_hash": sh, "message": message,
                "blocks": len(b.blocks), "entities": len(b.entities), "bbox": [list(bb[0]), list(bb[1])] if bb else None,
                "files": asm.files, "ports": sorted(b.ports), "labels": sorted(b.labels),
                "instances": sorted({i["name"] for i in b.instances}), "parts": sorted({i["part"] for i in b.instances})}
        (d / "meta.json").write_text(json.dumps(meta, indent=1))
        (self.mira / "last_build.json").write_text(json.dumps({"ok": True, "ts": time.time(), "rev": n}))
        self._log({"event": "build", "rev": n, "hash": h[:12], "blocks": len(b.blocks), "message": message})
        rev = Rev(self.dir, n)
        L = [f"BUILD OK rev {n} hash {h[:12]}  {len(b.blocks)} blocks" + (f", {len(b.entities)} entities" if b.entities else "")
             + (f"  bbox {list(bb[0])}..{list(bb[1])} ({bb[1][0] - bb[0][0] + 1}x{bb[1][1] - bb[0][1] + 1}x{bb[1][2] - bb[0][2] + 1})" if bb else "")
             + f"  {time.time() - t0:.1f}s"]
        L.append("palette: " + V.palette_line(rev.cells, 8))
        by_file = Counter(asm.where(line)[0] for _, line in b.prov)
        L.append("blocks by source file: " + ", ".join(f"{f} {n_}" for f, n_ in by_file.most_common(10)))
        by_grp = Counter(V.group_key(p) if p else "(root)" for p, _ in b.prov)
        inst_n = Counter(V.group_key(i["name"]) for i in b.instances)
        L.append("blocks by instance group: " + ", ".join(f"{g}{f' x{inst_n[g]}' if inst_n.get(g, 0) > 1 else ''} {n_}"
                                                         for g, n_ in by_grp.most_common(10)))
        if b.ports:
            L.append(f"ports ({len(b.ports)}): " + ", ".join(V.compress_names(list(b.ports), 10)))
        if nets:
            ok_n = sum(1 for n in nets if n["ok"])
            L.append(f"nets: {ok_n}/{len(nets)} routed, {sum(n['route_cells'] for n in nets)} route cells, "
                     f"{sum(n['repeaters'] for n in nets)} repeaters, {sum(1 for n in nets if n['warnings'])} with warnings (`nets` lists them)")
            bad = [n["name"] for n in nets if not n["ok"]]
            if bad:
                L.append(f"  WARNING route: {len(bad)} net(s) UNROUTED (strict=False): {', '.join(bad[:8])}"
                         + (" ..." if len(bad) > 8 else "") + " - `nets --warnings` shows why")
        L += self._warning_lines(asm, b.warnings)
        L += self._log_lines(b.log)
        if prev:
            a, r, c = V.diff_cells(prev.cells, rev.cells)
            L.append(f"vs rev {prev.n}: +{len(a)} -{len(r)} ~{len(c)}  (details: diff)")
        L += self._task_rule_lines(b)
        return "\n".join(L)

    def _task_rule_lines(self, b) -> List[str]:
        try:
            c, _ = self.contract()
        except ProjectError as e:
            return [f"spec: {e}"]
        if c is None:
            return ["spec: none (write spec.json to enable `test`)"]
        from env.runner import prepare
        pr = prepare(c, self._for_contract(c, b.blocks))
        if pr.errors:
            return [f"task rules: {len(pr.errors)} problem(s) (test will fail R0):"] + [f"  {e}" for e in pr.errors[:6]]
        return [f"task rules: ok ({len(pr.build)} blocks inside build box {c.build_box})"]

    def _for_contract(self, contract, blocks):
        """Build blocks minus cells identical to the spec's fixed blocks (as the session tools do)."""
        from env import blocks as EB
        from env.runner import fixed_cells
        fixed = fixed_cells(contract)
        out = []
        for x, y, z, s, nbt in blocks:
            try:
                if fixed.get((x, y, z)) == EB.canonical(s):
                    continue
            except EB.BlockError:
                pass
            out.append((x, y, z, s, nbt) if nbt else (x, y, z, s))
        return out

    def _current(self) -> Rev:
        """Latest revision, rebuilding first when the sources changed since it was stored."""
        rs = self.revs()
        if not rs or Rev(self.dir, rs[-1]).meta.get("source_hash") != self.source_hash():
            out = self.build()
            if out.startswith("BUILD FAILED"):
                raise ProjectError(out)
        return self.rev()

    # ---------------------------------------------------------------- overview
    def overview(self, rev=None, max_lines: int = 80) -> str:
        r = self.rev(rev)
        d, cells = r.data, r.cells
        try:
            contract, _ = self.contract()
        except ProjectError:
            contract = None
        fixed = self.fixed(contract)
        bb = r.meta["bbox"]
        L = [f"PROJECT {self.name}  rev {r.n}/{self.revs()[-1]}  hash {r.meta['hash'][:12]}  {len(cells)} blocks"
             + (f"  {r.meta['entities']} entities" if r.meta.get("entities") else "")
             + (f"  bbox {bb[0]}..{bb[1]} = {bb[1][0] - bb[0][0] + 1}x{bb[1][1] - bb[0][1] + 1}x{bb[1][2] - bb[0][2] + 1}" if bb else "")]
        lt = self._last_test()
        spec = (f"spec: {contract.id} ({len(contract.inputs)} in / {len(contract.outputs)} out), fixed scenery {len(fixed)} blocks"
                if contract else "spec: none")
        L.append(spec + (f"; last test rev {lt['rev']}: {lt['verdict']}" if lt else "; not tested yet"))
        L.append("palette: " + V.palette_line(cells, 8))
        # module tree
        prov = d["prov"]
        per_inst = defaultdict(list)
        root_files = Counter()
        per_net = Counter()
        for b, (path, line) in zip(d["blocks"], prov):
            if path.startswith("net:"):
                per_net[path] += 1
            elif path:
                per_inst[path.split("/", 1)[0]].append((b[0], b[1], b[2], path))
            else:
                root_files[r.file_of(line)[0]] += 1
        part_of = {i["name"]: i["part"] for i in d["instances"]}
        groups = defaultdict(list)
        for name in per_inst:
            groups[V.group_key(name)].append(name)
        L.append(f"MODULES ({len(per_inst)} instances in {len(groups)} groups; root blocks by file; router wiring grouped as nets)")
        rows = []
        for g, names in sorted(groups.items(), key=lambda kv: -sum(len(per_inst[n]) for n in kv[1])):
            pts = [p for n in names for p in per_inst[n]]
            lo = [min(p[i] for p in pts) for i in range(3)]
            hi = [max(p[i] for p in pts) for i in range(3)]
            parts = sorted({part_of.get(n, "?") for n in names})
            each = Counter(len(per_inst[n]) for n in names)
            each_s = f"{each.most_common(1)[0][0]} each" if len(names) > 1 and len(each) == 1 else f"{len(pts)} total"
            sub = Counter(V.group_key(p[3].split("/", 1)[1]) for p in pts if "/" in p[3])
            rows.append(f"  {g:<14} {'/'.join(parts)[:24]:<24} x{len(names):<3} {each_s:<12} {lo}..{hi}"
                        + (f"  sub: {', '.join(f'{k} {v}' for k, v in sub.most_common(3))}" if sub else ""))
        if len(rows) > 10:
            rows = rows[:10] + [f"  ... (+{len(rows) - 10} groups; `find NAME` lists them)"]
        L += rows
        if root_files:
            L.append("  (root)  " + ", ".join(f"{f} {n}" for f, n in root_files.most_common(6)))
        if per_net:
            nets = {n["name"]: n for n in d.get("nets", [])}
            top = ", ".join(f"{k[4:]} {v}" for k, v in per_net.most_common(3))
            rep = sum(n["repeaters"] for n in nets.values())
            warn = sum(1 for n in nets.values() if n["warnings"])
            L.append(f"  (nets)  {len(per_net)} routed nets, {sum(per_net.values())} blocks"
                     + (f", {rep} repeaters" if nets else "") + (f", {warn} with warnings" if warn else "")
                     + f"; biggest: {top}; `nets` lists them")
        # ports / labels
        if contract:
            ins = [p for p in contract.ports if p.is_input]
            outs = [p for p in contract.ports if p.is_output]
            for title, ps in (("spec inputs", ins), ("spec outputs", outs)):
                L.append(f"{title} ({len(ps)}): " + self._port_groups([(p.name, tuple(p.pos), p.kind) for p in ps]))
        if d["ports"]:
            L.append(f"build ports ({len(d['ports'])}): " + self._port_groups([(k, tuple(v["pos"]), v["kind"]) for k, v in d["ports"].items()]))
        if d["labels"]:
            L.append(f"labels ({len(d['labels'])}): " + ", ".join(
                f"{k} {v['min']}..{v['max']}" for k, v in list(d["labels"].items())[:6]) + (" ..." if len(d["labels"]) > 6 else ""))
        # maps (budget the rows so the whole view stays under max_lines)
        world = dict(fixed)
        world.update(cells)
        grp = r.groups()
        grp.update({p: "(fixed)" for p in fixed if p not in cells})
        names = [g for g, _ in Counter(grp.values()).most_common()]
        letters = {g: (chr(65 + i) if i < 26 else chr(97 + i - 26) if i < 52 else "?") for i, g in enumerate(names)}
        if "(fixed)" in letters:
            letters["(fixed)"] = ","
        used = len(L) + 9
        side_rows = 8
        top_rows = max(4, min(18, max_lines - used - side_rows - 6))
        multi = len(letters) > 1
        tm, tnote, used_cls = V.top_map(world, grp if multi else None, max_cols=60, max_rows=top_rows, letters=letters if multi else None)
        L.append(f"TOP VIEW (x -> columns, z -> rows; {tnote}; dominant class, redstone parts win{' | module letters' if multi else ''})")
        L += tm
        L.append("legend: " + V.legend(used_cls))
        if multi:
            L.append("modules: " + "  ".join(f"{l}={g}" for g, l in list(letters.items())[:14]) + (" ..." if len(letters) > 14 else ""))
        sm, snote = V.side_map(world, max_cols=60, max_rows=side_rows)
        L.append(f"SIDE VIEW (x -> columns, y -> rows, top = highest; {snote}; all z collapsed)")
        L += sm
        y0, y1 = (bb[0][1], bb[1][1]) if bb else (0, 0)
        L.append(f"next: view --layer Y (y {y0}..{y1}) [--box x0 z0 x1 z1] | find NAME | query X Y Z | test | trace PORT | diff | log")
        return clip("\n".join(L))

    @staticmethod
    def _port_groups(items) -> str:
        pat = defaultdict(list)
        order = []
        for name, pos, kind in items:
            m = list(re.finditer(r"\d+", name))
            key = (name[:m[-1].start()], name[m[-1].end():], kind) if m else (name, "", kind)
            if key not in pat:
                order.append(key)
            pat[key].append((int(m[-1].group()) if m else None, name, pos))
        out = []
        for key in order:
            vals = pat[key]
            if len(vals) == 1:
                _, n, p = vals[0]
                out.append(f"{n} {key[2]} {list(p)}")
                continue
            nums = sorted(v[0] for v in vals)
            rng = [f"{min(v[2][i] for v in vals)}..{max(v[2][i] for v in vals)}" if len({v[2][i] for v in vals}) > 1
                   else str(vals[0][2][i]) for i in range(3)]
            out.append(f"{key[0]}{{{nums[0]}..{nums[-1]}}}{key[1]} {key[2]} x{len(vals)} @ x {rng[0]} y {rng[1]} z {rng[2]}")
        s = "; ".join(out[:8])
        return s + (f"; ... (+{len(out) - 8})" if len(out) > 8 else "")

    # ---------------------------------------------------------------- nets
    def nets(self, rev=None, name: Optional[str] = None, warnings_only: bool = False, limit: int = 60) -> str:
        """Every routed net of the build (Part.route / route_all): source, sinks, length, repeaters, strength per sink."""
        import fnmatch
        r = self.rev(rev)
        nets = r.data.get("nets", [])
        if not nets:
            return (f"no routed nets in rev {r.n} (nets come from b.route / b.route_all calls; "
                    "wiring placed with wire_path or set() is not listed)")
        if name:
            pat = name.lower()
            wild = any(c in pat for c in "*?[")
            m = (lambda x: fnmatch.fnmatch(x.lower(), pat)) if wild else (lambda x: pat in x.lower())
            nets = [n for n in nets if m(n["name"]) or m(n["src"]) or any(m(k) for k in n["sinks"])]
            if not nets:
                return f"no net matches {name!r}"
        if warnings_only:
            nets = [n for n in nets if n["warnings"] or n["note"] or not n["ok"]]
        tot = Counter()
        for n in nets:
            tot["cells"] += n["route_cells"]
            tot["rep"] += n["repeaters"]
            tot["warn"] += bool(n["warnings"])
            tot["bad"] += not n["ok"]
        L = [f"NETS rev {r.n}: {len(nets)} net(s), {tot['cells']} route cells, {tot['rep']} repeaters, {tot['warn']} with warnings"
             + (f", {tot['bad']} UNROUTED" if tot["bad"] else "")]
        detail = len(nets) == 1
        L.append(f"{'name':<14}{'src -> sinks':<46}{'len':>4}{'rep':>4}  strength at sinks (need)   source line")
        for n in nets[:limit]:
            sinks = n["sinks"]
            st = []
            for k in sinks[:5]:
                lv, nd = n["levels"].get(k), n["needs"].get(k, 1)
                st.append(f"{k}={lv if lv is not None else '-'}" + (f"({nd})" if nd > 1 else ""))
            f, ln = r.file_of(n["line"])
            head = f"{n['src']} -> {', '.join(sinks[:3])}{f' +{len(sinks) - 3}' if len(sinks) > 3 else ''}"
            L.append(f"{n['name'][:13]:<14}{head[:45]:<46}{n['route_cells']:>4}{n['repeaters']:>4}  "
                     f"{' '.join(st)}{f' +{len(sinks) - 5}' if len(sinks) > 5 else ''}   {f}:{ln}" + ("  UNROUTED" if not n["ok"] else ""))
            for w in n["warnings"]:
                L.append(f"    ! {w}")
            if n["note"] and not n["warnings"]:
                L.append(f"    note: {n['note']}")
            if not n["ok"]:
                L.append("    " + n["reason"].replace("\n", "\n    ")[:900])
            if detail:
                L.append(f"    delay (repeater ticks) per sink: {n['delay']}; blocks written {n['blocks']}; sink rules: {n['why'] or '-'}")
        if len(nets) > limit:
            L.append(f"... (+{len(nets) - limit} more; filter with --name PATTERN or --warnings)")
        return clip("\n".join(L))

    # ---------------------------------------------------------------- view / find / query
    def settled(self, rev: Rev) -> Tuple[Dict[tuple, str], dict]:
        """The at-rest world of a revision (spec scenery included): placement + running to rest, as the env bakes it
        (simulation.export.settle). Cached per revision/spec in .mira/cache."""
        import hashlib
        from simulation import export as X
        contract = None
        try:
            contract, _ = self.contract()
        except ProjectError:
            pass
        spec = (self.dir / "spec.json").read_bytes() if (self.dir / "spec.json").exists() else b""
        key = hashlib.sha256(rev.meta["hash"].encode() + spec).hexdigest()[:16]
        cache = self.mira / "cache" / f"settled-{rev.n}-{key}.json.gz"
        if cache.exists():
            d = _gz_read(cache)
            return {tuple(map(int, k.split(","))): v for k, v in d["cells"].items()}, d["info"]
        cells, info = X.settle(self.world(rev), contract.budgets.rest if contract else 400)
        cache.parent.mkdir(exist_ok=True)
        for old in cache.parent.glob(f"settled-{rev.n}-*.json.gz"):
            old.unlink()
        _gz_write(cache, {"cells": {",".join(map(str, k)): v for k, v in cells.items()}, "info": info})
        return cells, info

    def _settled_note(self, info: dict, raw: Dict[tuple, str], cells: Dict[tuple, str]) -> str:
        n = sum(1 for p, st in cells.items() if raw.get(p) != st)
        return (f"SETTLED world (placement + {info['ticks']} ticks to rest{'' if info['at_rest'] else ', NOT at rest'}; "
                f"{n} cells differ from the raw program, see `diff --raw-vs-settled`)")

    def view(self, layer: Optional[int] = None, box: Optional[Sequence[int]] = None, rev=None, max_cols: int = 100,
             max_rows: int = 60, axis: Optional[str] = None, at: Optional[int] = None, settled: bool = False) -> str:
        r = self.rev(rev)
        axis = (axis or "y").lower()
        if axis not in ("x", "y", "z"):
            return f"error: --axis must be x, y or z, got {axis!r}"
        if at is None:
            at = layer
        if at is None:
            return "error: give --layer Y (horizontal slice) or --axis x|y|z --at N"
        at = int(at)
        raw = self.world(r)
        head = ""
        if settled:
            world, info = self.settled(r)
            head = self._settled_note(info, raw, world) + "\n"
        else:
            world = raw
        try:
            contract, _ = self.contract()
        except ProjectError:
            contract = None
        out = V.axis_view(world, axis, at, box, max_cols, max_rows)
        ai = "xyz".index(axis)
        ports = []

        def inside(q):
            if q[ai] != at:
                return False
            if box is None or axis != "y":
                return True
            return min(box[0], box[2]) <= q[0] <= max(box[0], box[2]) and min(box[1], box[3]) <= q[2] <= max(box[1], box[3])
        for p in (contract.ports if contract else []):
            if p.pos and inside(p.pos):
                ports.append(f"{p.name}{list(p.pos)}")
        for k, v in r.data["ports"].items():
            if inside(v["pos"]):
                ports.append(f"{k}{v['pos']}")
        if ports:
            out += f"\nports on this {'layer' if axis == 'y' else 'slice'}: " + ", ".join(ports[:30]) + (" ..." if len(ports) > 30 else "")
        return clip(head + out)

    def find(self, pattern: str, rev=None, limit: int = 40) -> str:
        import fnmatch
        r = self.rev(rev)
        d = r.data
        pat = pattern.lower()
        wild = any(c in pat for c in "*?[")
        match = (lambda s: fnmatch.fnmatch(s.lower(), pat)) if wild else (lambda s: pat in s.lower())
        L = []
        try:
            contract, _ = self.contract()
        except ProjectError:
            contract = None
        for p in (contract.ports if contract else []):
            if match(p.name) or (p.doc and match(p.doc)):
                L.append(f"spec port {p.name} ({'in' if p.is_input else 'out'} {p.kind}) at {list(p.pos)}" + (f" - {p.doc}" if p.doc else ""))
        for k, v in d["ports"].items():
            if match(k):
                L.append(f"build port {k} ({v['kind']}{', face ' + v['face'] if v.get('face') else ''}) at {v['pos']}")
        for k, v in d["labels"].items():
            if match(k):
                L.append(f"label {k} {v['min']}..{v['max']}")
        pts = defaultdict(list)
        for b, (path, _) in zip(d["blocks"], d["prov"]):
            if path:
                pts[path.split("/", 1)[0]].append(b)
        for i in d["instances"]:
            if match(i["name"]) or match(i["part"]):
                bs = pts.get(i["name"].split("/", 1)[0], [])
                lo = [min(b[k] for b in bs) for k in range(3)] if bs else "-"
                hi = [max(b[k] for b in bs) for k in range(3)] if bs else "-"
                f, ln = r.file_of(i["line"])
                L.append(f"instance {i['name']} = {i['part']} at {i['at']} rot {i['rot']}{' mirror ' + i['mirror'] if i['mirror'] else ''}"
                         f", {i['blocks']} blocks {lo}..{hi} (placed at {f}:{ln})")
        for n in d.get("nets", []):
            if match(n["name"]) or match(n["src"]) or any(match(k) for k in n["sinks"]):
                L.append(f"net {n['name']}: {n['src']} -> {', '.join(n['sinks'][:6])}{' ...' if len(n['sinks']) > 6 else ''}  "
                         f"{n['route_cells']} cells, {n['repeaters']} repeaters (routed at line {r.file_of(n['line'])[0]}:{r.file_of(n['line'])[1]})")
        for f in r.meta.get("files", []):
            if match(f):
                n = sum(1 for _, line in d["prov"] if r.file_of(line)[0] == f)
                L.append(f"source file {f}: {n} blocks written by its code")
        kinds = defaultdict(list)
        for b in d["blocks"]:
            bid = V.split(b[3])[0]
            if match(bid):
                kinds[bid].append(b)
        for bid, bs in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
            ex = ", ".join(str([b[0], b[1], b[2]]) for b in bs[:6])
            L.append(f"block {bid}: {len(bs)} e.g. {ex}" + (" ..." if len(bs) > 6 else ""))
        if not L:
            return f"no match for {pattern!r} (searched spec ports, build ports, labels, instances, parts, files, block ids)"
        more = len(L) - limit
        return clip("\n".join(L[:limit]) + (f"\n... (+{more} more matches; narrow the pattern)" if more > 0 else ""))

    def query(self, x: int, y: int, z: int, radius: int = 0, rev=None, limit: int = 60, settled: bool = False) -> str:
        r = self.rev(rev)
        d = r.data
        fixed = self.fixed()
        sw, note = {}, ""
        if settled:
            sw, info = self.settled(r)
            note = self._settled_note(info, self.world(r), sw)
        prov = {(b[0], b[1], b[2]): (b, p) for b, p in zip(d["blocks"], d["prov"])}
        L = []
        rad = max(0, min(int(radius), 4))
        for yy in range(y + rad, y - rad - 1, -1):
            for zz in range(z - rad, z + rad + 1):
                for xx in range(x - rad, x + rad + 1):
                    p = (xx, yy, zz)
                    now = lambda raw: sw.get(p, "minecraft:air") if settled else raw      # noqa: E731
                    if p in prov:
                        b, (path, line) = prov[p]
                        f, ln = r.file_of(line)
                        st = now(b[3])
                        was = _raw_note(b[3], st) if settled else ""
                        L.append(f"{list(p)} {V.short(st)}{_flow(st)}{was}  <- {f}:{ln}" + (f" [{path}]" if path else "") + (" +nbt" if b[4] else ""))
                    elif p in fixed:
                        st = now(fixed[p])
                        L.append(f"{list(p)} {V.short(st)}" + (_raw_note(fixed[p], st) if settled else "") + "  <- spec fixed")
                    elif settled and p in sw:
                        L.append(f"{list(p)} {V.short(sw[p])}  (settling created it)")
                    elif rad == 0:
                        L.append(f"{list(p)} air")
        head = f"query {[x, y, z]} radius {rad}: {len(L)} non-air cells" if rad else f"query {[x, y, z]}"
        if len(L) > limit:
            L = L[:limit] + [f"... (+{len(L) - limit}; use a smaller radius)"]
        if rad == 0 and (x, y, z) in prov and prov[(x, y, z)][0][4]:
            L.append(f"nbt: {prov[(x, y, z)][0][4][:300]}")
        return clip("\n".join(([note] if note else []) + [head] + L))

    # ---------------------------------------------------------------- diff / log
    def diff(self, a=None, b=None, examples: int = 10, raw_vs_settled: bool = False) -> str:
        if raw_vs_settled:
            r = self.rev(b if b is not None else a)
            sw, info = self.settled(r)
            return clip(f"rev {r.n}: raw program vs settled world (placement + {info['ticks']} ticks to rest"
                        f"{'' if info['at_rest'] else ', NOT at rest'})\n" + V.raw_vs_settled(self.world(r), sw, min(examples, 5)))
        rs = self.revs()
        if not rs:
            raise ProjectError("no revisions")
        rb = self.rev(b) if b is not None else self.rev()
        if a is None:
            prev = [n for n in rs if n < rb.n]
            if not prev:
                return f"rev {rb.n} is the first revision; nothing to diff"
            ra = Rev(self.dir, prev[-1])
        else:
            ra = self.rev(a)
        txt = V.diff_text(ra.cells, rb.cells, ra.groups(), rb.groups(), examples)
        src = []
        for f in sorted(set(ra.meta.get("files", [])) | set(rb.meta.get("files", []))):
            pa, pb = ra.dir / "src" / f, rb.dir / "src" / f
            if not pa.exists() and (ra.dir / "src/_repo" / f).exists():
                pa = ra.dir / "src/_repo" / f
            if not pb.exists() and (rb.dir / "src/_repo" / f).exists():
                pb = rb.dir / "src/_repo" / f
            ta = pa.read_text().splitlines() if pa.exists() else []
            tb = pb.read_text().splitlines() if pb.exists() else []
            if ta != tb:
                import difflib
                ch = sum(1 for l_ in difflib.unified_diff(ta, tb, lineterm="", n=0) if l_[:1] in "+-" and not l_.startswith(("+++", "---")))
                src.append(f"{f} ({ch} lines)")
        return clip(f"rev {ra.n} -> rev {rb.n}\n" + txt + ("\nsource files changed: " + ", ".join(src) if src else "\nsource files: unchanged"))

    def log(self, n: int = 20) -> str:
        rs = self.revs()
        if not rs:
            return "no revisions yet"
        tests = defaultdict(list)
        p = self.mira / "tests.jsonl"
        if p.exists():
            for line in p.read_text().splitlines():
                t = json.loads(line)
                tests[t["rev"]].append(t)
        L = [f"{len(rs)} revisions (newest last)"]
        prev = None
        for k in rs[-n:]:
            m = Rev(self.dir, k).meta
            delta = f"{m['blocks'] - prev:+d}" if prev is not None else ""
            prev = m["blocks"]
            ts = "; ".join(f"{'module ' + t['module'] + ' ' if t.get('module') else ''}{t['verdict']}" for t in tests.get(k, [])[-2:])
            L.append(f"rev {k:>3}  {m['ts'][5:16]}  {m['hash'][:10]}  {m['blocks']:>6} blocks {delta:>6}  "
                     + (f"test: {ts}  " if ts else "") + (m.get("message") or ""))
        return "\n".join(L)

    # ---------------------------------------------------------------- test
    def _last_test(self) -> Optional[dict]:
        p = self.mira / "last_test.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _module_program(self, module: str, extra: dict) -> Tuple[str, str]:
        part = extra.get("part") or module
        if not part.isidentifier():
            raise ProjectError(f"module spec part {part!r} is not a function name")
        params = extra.get("params") or {}
        at = tuple(extra.get("at") or (0, 0, 0))
        args = ", ".join([f"at={at!r}", f"rot={int(extra.get('rot') or 0)}", f"mirror={extra.get('mirror')!r}", f"name={module!r}"]
                         + [f"{k}={v!r}" for k, v in params.items() if str(k).isidentifier()])
        entry = f"<module {module}>"
        text = (f"from modules.{module} import {part}\n\n\ndef build(b):\n    b.place({part}, {args})\n")
        return entry, text

    def test(self, module: Optional[str] = None, public_only: bool = False, fast: bool = False) -> str:
        from env.runner import run
        from env.score import feedback, score
        t0 = time.time()
        contract, extra = self.contract(module)
        if contract is None:
            return ("error: no spec.json; write a contract v2 (docs/ENV.md, or init --from-task) first" if module is None
                    else f"error: no modules/{module}.spec.json (see docs/PRODUCT.md, module specs)")
        if module is None:
            r = self._current()
            blocks = [tuple(b) for b in r.data["blocks"]]
            rev_n = r.n
        else:
            entry, text = self._module_program(module, extra)
            asm, b = self.compile(entry, text)
            if asm.errors or b is None or b.errors:
                return "MODULE BUILD FAILED\n" + "\n".join(asm.errors[:10] if asm.errors else self._issue_lines(asm, b.errors))
            blocks = b.blocks
            rev_n = self.revs()[-1] if self.revs() else 0
        blocks = self._for_contract(contract, blocks)
        res = run(contract, blocks, public_only=public_only, fast=fast)
        sc = score(contract, res)
        pub = [v for v in res.vectors if v.public] + [q for q in res.sequences if q.public]
        hid = [v for v in res.vectors if not v.public] + [q for q in res.sequences if not q.public]
        ok_pub, ok_hid = sum(x.ok for x in pub), sum(x.ok for x in hid)
        verdict = "PASS" if sc.full_pass else "FAIL"
        head = (f"TEST {'module ' + module if module else 'rev ' + str(rev_n)}: {verdict}  public {ok_pub}/{len(pub)}"
                + ("" if public_only else f"  other {ok_hid}/{len(hid)}") + f"  reward {sc.reward:.3f}  "
                f"({len(blocks)} blocks, {time.time() - t0:.1f}s{', fast' if fast else ''}{', public only' if public_only else ''}"
                f"{', R4 n/a: spec has no reference size' if sc.r4_na else ''})")
        body = feedback(contract, res, sc, final=False)
        if not public_only:   # the score line of feedback() only speaks about public checks
            body = "\n".join(f"SCORE (all checks): reward={sc.reward:.3f}  R0={sc.r0:.2f} R1={sc.r1:.2f} R2={sc.r2:.2f} R3={sc.r3:.2f} "
                              f"R4={sc.r4_text}  {'FULL PASS' if sc.full_pass else 'not a full pass'}" if ln.startswith("SCORE (public") else ln
                              for ln in body.splitlines())
        extra_fail = []
        for v in res.vectors:
            if not v.public and not v.ok and len(extra_fail) < 3:
                exp = {k: e for k, e in v.expect.items() if e is not None}
                bad = {k: v.got[k] for k, e in exp.items() if v.got.get(k) != e}
                extra_fail.append(f"  FAIL (non-public) vector #{v.index}: set {v.inputs} -> wrong {bad} (expected {({k: exp[k] for k in bad})})")
        for q in res.sequences:
            if not q.public and not q.ok and len(extra_fail) < 5:
                w = next(w for w in q.windows if not w.ok)
                extra_fail.append(f"  FAIL (non-public) sequence '{q.name}': {w.port} should be {w.value} t={w.t1}..{w.t2}, first bad {w.first_bad}")
        rec = {"rev": rev_n, "module": module, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "verdict": verdict,
               "public_only": public_only, "fast": fast, "reward": round(sc.reward, 4), "full_pass": sc.full_pass,
               "public": f"{ok_pub}/{len(pub)}", "other": f"{ok_hid}/{len(hid)}",
               "r": [round(x, 4) for x in (sc.r0, sc.r1, sc.r2, sc.r3, sc.r4)], "r4_na": sc.r4_na,
               "failing_vectors": [v.index for v in res.vectors if not v.ok][:50],
               "failing_sequences": [q.name for q in res.sequences if not q.ok],
               "invariant_failures": {k: v["detail"][:200] for k, v in res.invariant_failures().items()}}
        (self.mira / ("last_test.json" if module is None else f"last_test_{module}.json")).write_text(json.dumps(rec, indent=1))
        with open(self.mira / "tests.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        return clip("\n".join([head, body] + extra_fail + ["next: trace PORT [--vector K] to see timing; view/query around a failing output"]))

    # ---------------------------------------------------------------- trace
    def trace(self, signals: Sequence[str], ticks: Optional[int] = None, seq: Optional[str] = None,
              sets: Optional[str] = None, vector: Optional[int] = None, module: Optional[str] = None,
              public_only: Optional[bool] = None, width: int = 100, from_tick: Optional[int] = None,
              to_tick: Optional[int] = None) -> str:
        import random
        from env.runner import Driver, prepare, read_port
        from env.sim import Sim, compile_cells
        contract, extra = self.contract(module)
        if module is None:
            r = self._current()
            blocks = [tuple(b) for b in r.data["blocks"]]
            bports = r.data["ports"]
        else:
            entry, text = self._module_program(module, extra)
            asm, b = self.compile(entry, text)
            if asm.errors or b is None or b.errors:
                return "MODULE BUILD FAILED"
            blocks, bports = b.blocks, b.ports
        if contract is not None:
            pr = prepare(contract, self._for_contract(contract, blocks))
            if pr.errors:
                return "error: build does not satisfy the spec's rules: " + "; ".join(pr.errors[:4])
            world, rest = pr.world, contract.budgets.rest
        else:
            world, rest = {(b_[0], b_[1], b_[2]): b_[3] for b_ in blocks}, 400
        sc, origin, _ = compile_cells(world)
        sim = Sim(sc, origin, "inworld")
        sim.max_steps = 200000
        t_rest, ok = sim.settle(rest)
        ports = {p.name: p for p in contract.ports} if contract else {}
        drv = Driver(sim, contract) if contract else None
        readers, notes = {}, []
        sigs = list(signals) or ([p.name for p in contract.outputs][:8] if contract else [])
        for s in sigs:
            m = re.fullmatch(r"\s*(-?\d+)[, ](-?\d+)[, ](-?\d+)\s*", s)
            if s in ports:
                p = ports[s]
                readers[s] = (lambda p=p: str(read_port(sim, p))) if p.is_output else (lambda p=p: str(drv.level(p)))
            elif s in bports:
                pos = tuple(bports[s]["pos"])
                readers[s] = lambda pos=pos: _cell_value(sim.get(pos))
            elif m:
                pos = tuple(int(v) for v in m.groups())
                readers[f"({','.join(m.groups())})"] = lambda pos=pos: _cell_value(sim.get(pos))
            else:
                notes.append(f"unknown signal {s!r} (spec port, build port or x,y,z)")
        if not readers:
            return "error: nothing to trace. " + " ".join(notes) + (" Spec ports: " + ", ".join(ports) if ports else "")
        rows = {k: [] for k in readers}
        marks, events = {}, []

        def sample():
            for k, fn in readers.items():
                rows[k].append(fn())
        # stimulus
        steps = []
        if sets:
            for i, part_ in enumerate(x for x in sets.split(";") if x.strip()):
                kv = dict(a.split("=", 1) for a in re.split(r"[,\s]+", part_.strip()) if "=" in a)
                steps.append((f"s{i}", {k: int(v) for k, v in kv.items()}))
            mode = "custom vectors"
        elif seq or (contract and not contract.vectors() and contract.behaviour.sequences):
            if contract is None:
                return "error: --seq needs a spec"
            cand = [q for q in contract.behaviour.sequences if (seq is None and q.public) or q.name == seq]
            if not cand:
                return f"error: no sequence {seq!r}; sequences: " + ", ".join(q.name for q in contract.behaviour.sequences)
            mode = f"sequence {cand[0].name}"
        elif contract and contract.vectors() and ticks is None:
            lt = self._last_test()
            po = public_only if public_only is not None else (lt["public_only"] if lt else True)
            vecs = [v for v in contract.vectors() if v.public or not po]
            order = list(range(len(vecs)))
            budget, extra_t, shuffle, _reset, _oi = contract.vector_settings()
            if shuffle:
                random.Random(contract.eval_seed).shuffle(order)
            steps = [(f"v{i}", vecs[i].inputs, budget, extra_t, vecs[i].expect) for i in order]
            mode = f"{'public' if po else 'all'} vectors in the test's order (seed {contract.eval_seed})"
        else:
            mode = f"idle {ticks or 40} ticks"
        sample()   # tick 0 = at rest
        if mode.startswith("sequence"):
            q = cand[0]
            evs = sorted(q.events, key=lambda e: e.t)
            ei = 0
            rows = {k: [] for k in readers}
            horizon = q.duration
            if ticks is not None:
                horizon = min(horizon, int(ticks))
            if to_tick is not None:
                horizon = min(horizon, int(to_tick))
            for t in range(horizon + 1):
                while ei < len(evs) and evs[ei].t <= t:
                    for k, val in evs[ei].set.items():
                        drv.set(ports[k], val)
                    marks[t] = "+".join(f"{k}{val}" for k, val in evs[ei].set.items())[:6]
                    events.append(f"t={t} set {evs[ei].set}")
                    ei += 1
                sample()
                if t < horizon:
                    sim.step()
            for e in q.expect:
                events.append(f"expect {e.port}={e.value} {'all' if e.mode == 'all' else 'any'} t={e.t1}..{e.t2}")
        elif steps and mode == "custom vectors":
            for lab, inputs in steps:
                marks[len(rows[next(iter(rows))])] = lab
                for k, val in inputs.items():
                    if k not in ports:
                        return f"error: unknown input {k!r}"
                    drv.set(ports[k], val)
                n, settled = sim.settle(rest, lambda _n: sample())
                for _ in range(2):
                    sim.step()
                    sample()
                events.append(f"{lab}: set {inputs} -> settled in {n} ticks" + ("" if settled else " (NOT settled)"))
        elif steps:
            spans = []
            for lab, inputs, budget, extra_t, expect in steps:
                start = len(rows[next(iter(rows))])
                marks[start] = lab
                for k, val in inputs.items():
                    drv.set(ports[k], val)
                n, settled = sim.settle(budget, lambda _n: sample())
                for _ in range(extra_t):
                    sim.step()
                    sample()
                got = {k: read_port(sim, ports[k]) for k, e in expect.items() if e is not None}
                bad = {k: v for k, v in got.items() if v != expect[k]}
                spans.append((lab, start, len(rows[next(iter(rows))])))
                events.append(f"{lab} @t{start}: set {_short_map(inputs)} -> settle {n}t" + ("" if settled else " NOT settled")
                              + (f"  WRONG {bad}" if bad else "  ok"))
            if vector is not None:
                hit = [s for s in spans if s[0] == f"v{vector}"]
                if not hit:
                    return f"error: vector {vector} is not in this stimulus (v0..v{len(steps) - 1})"
                lab, a, b_ = hit[0]
                a = max(0, a - 2)
                rows = {k: v[a:b_] for k, v in rows.items()}
                marks = {2: lab}
                events = [e for e in events if e.startswith(lab + " ")]
                mode += f"; showing only {lab} (ticks {a}..{b_ - 1} of the run)"
        else:
            for _ in range(ticks or 40):
                sim.step()
                sample()
        if from_tick is not None or (to_tick is not None and not mode.startswith("sequence")):
            n_all = max((len(v) for v in rows.values()), default=0)
            lo = max(0, int(from_tick or 0))
            hi = min(n_all - 1, int(to_tick)) if to_tick is not None else n_all - 1
            if lo > hi:
                return f"error: --from {from_tick} is past the end of the trace ({n_all} ticks, 0..{n_all - 1})"
            rows = {k: v[lo:hi + 1] for k, v in rows.items()}
            marks = {t - lo: m for t, m in marks.items() if lo <= t <= hi}
            mode += f"; window ticks {lo}..{hi} of {n_all}"
        elif mode.startswith("sequence") and (ticks is not None or to_tick is not None):
            mode += f"; first {horizon} of {q.duration} ticks"
        L = [f"TRACE {', '.join(readers)}  stimulus: {mode}; at rest after {t_rest} ticks" + ("" if ok else " (NOT at rest)")]
        L.append(V.timing_diagram(rows, marks, width))
        L.append("edges (value@tick): " + " | ".join(f"{k}: {V.edges(v, 8)}" for k, v in rows.items()))
        if events:
            L.append("stimulus:" if len(events) <= 24 else f"stimulus ({len(events)} steps, first 24):")
            L += [f"  {e}" for e in events[:24]]
        L += notes
        text = "\n".join(L)
        (self.mira / "traces").mkdir(exist_ok=True)
        (self.mira / "traces" / "last.txt").write_text(text)
        return clip(text)

    # ---------------------------------------------------------------- spec-check / verify-vanilla
    def _spec_blocks(self, contract, module: Optional[str], extra: dict):
        """(blocks for the contract, rev number) of the build the spec is checked against, or (None, reason)."""
        if module is None:
            try:
                r = self._current()
            except ProjectError as e:
                return None, str(e).splitlines()[0]
            return self._for_contract(contract, [tuple(b) for b in r.data["blocks"]]), r.n
        entry, text = self._module_program(module, extra)
        asm, b = self.compile(entry, text)
        if asm.errors or b is None or b.errors:
            return None, "module does not build"
        return self._for_contract(contract, b.blocks), 0

    def spec_check(self, module: Optional[str] = None, mutants: int = 12, no_mutation: bool = False, seed: int = 0,
                   budget: float = 240.0) -> str:
        """Is the spec any good? Schema (readable errors), cells the ports point at, vacuity (outputs that never change,
        inputs that never matter, an empty build that passes), IO coverage and a mutation score of the spec against the
        current build (surviving mutants = behaviour the spec does not pin down)."""
        from mira_mcp import project_checks as C
        t0 = time.time()
        sp = self.dir / ("spec.json" if module is None else f"modules/{module}.spec.json")
        if not sp.exists():
            return f"error: no {sp.relative_to(self.dir)}; write a contract v2 first (docs/PRODUCT.md, spec cheat sheet)"
        try:
            raw = json.loads(sp.read_text())
        except ValueError as e:
            return f"SPEC-CHECK FAIL: {sp.name} is not valid JSON: {e}"
        probs = C.schema_problems(raw.get("contract", raw) if module else raw)
        if probs:
            return clip(f"SPEC-CHECK FAIL: {len(probs)} schema problem(s) in {sp.name}\n" + "\n".join(f"  - {x}" for x in probs)
                        + "\nnext: fix them, then run `spec-check` again (docs/PRODUCT.md has a contract cheat sheet)")
        contract, extra = self.contract(module)
        blocks, rev_n = self._spec_blocks(contract, module, extra)
        findings, L = [], []
        n_in, n_out = len(contract.inputs), len(contract.outputs)
        n_seq = len(contract.behaviour.sequences)
        L.append(f"spec {sp.name}: '{contract.id}', {n_in} inputs / {n_out} outputs, {len(contract.vectors())} vectors, {n_seq} sequences; schema ok")
        findings += C.vacuity(contract)
        if blocks is None:
            L.append(f"build: not available ({rev_n}); port-cell, empty-build and mutation checks skipped")
        else:
            from env.runner import fixed_cells, run
            from env.score import score
            world = {p_: st for p_, st in fixed_cells(contract).items()}
            world.update({(b_[0], b_[1], b_[2]): b_[3] for b_ in blocks})
            findings += C.port_cell_findings(contract, world)
            try:
                if score(contract, run(contract, [], fast=True)).full_pass:
                    findings.append("an EMPTY build passes this spec (fixed scenery alone satisfies every check): the spec is vacuous")
            except Exception as e:  # noqa: BLE001
                L.append(f"empty-build run failed: {type(e).__name__}: {str(e)[:120]}")
        L += ["coverage: " + x if i == 0 else "          " + x for i, x in enumerate(C.coverage_lines(contract))]
        surv = 0
        if blocks is not None and not no_mutation:
            groups = None
            if module is None:
                try:
                    rv = self.rev(rev_n)
                    groups = {(b_[0], b_[1], b_[2]): (path.split("/", 1)[0] if path else rv.file_of(line)[0])
                              for b_, (path, line) in zip(rv.data["blocks"], rv.data["prov"])}
                except ProjectError:
                    groups = None
            res = C.mutation(contract, blocks, max_mutants=int(mutants), seed=int(seed), budget_s=float(budget), groups=groups)
            L += C.mutation_lines(res, groups)
            surv = len(res.get("survivors", []))
        elif no_mutation:
            L.append("mutation: skipped (--no-mutation)")
        status = "OK" if not findings and not surv else "WARN"
        head = (f"SPEC-CHECK {status}" + (f" (rev {rev_n})" if blocks is not None and module is None else "")
                + f": {len(findings)} finding(s)" + (f", {surv} surviving mutant(s)" if surv else "") + f"  ({time.time() - t0:.1f}s)")
        out = [head] + [f"  FINDING: {x}" for x in findings] + L
        if status == "OK":
            out.append("next: the spec looks strong; hand it to the user for sign-off, then test/export")
        return clip("\n".join(out))

    def verify_vanilla(self, max_vectors: int = 12, every: bool = False, variants: int = 0, seed: int = 1, workers: int = 3,
                       second_origin: bool = False) -> str:
        """Replay spec.json in the vanilla 26.2 oracle (one JVM per capture) at the env's origin, and compare with the env
        verdict per vector / sequence. Big tables are sampled (max_vectors); `every` replays all."""
        from mira_mcp import project_checks as C
        contract, extra = self.contract()
        if contract is None:
            return "error: no spec.json; write a contract v2 (docs/PRODUCT.md spec cheat sheet) first"
        blocks, rev_n = self._spec_blocks(contract, None, extra)
        if blocks is None:
            return f"error: {rev_n}"
        res = C.vanilla(contract, blocks, max_vectors=int(max_vectors), every=bool(every), variants=int(variants), seed=int(seed),
                        workers=int(workers), second_origin=bool(second_origin))
        text = C.vanilla_text(res, rev_n)
        if res.get("available"):
            rec = {"rev": rev_n, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "verdict": "PASS" if text.startswith("VERIFY-VANILLA") and ": PASS" in text.split("\n", 1)[0] else "FAIL",
                   "cases": res["cases"], "captures": [{k: v for k, v in c.items() if k != "labels"} for c in res["captures"]]}
            (self.mira / "vanilla_receipt.json").write_text(json.dumps(rec, indent=1, default=str))
            self._log({"event": "verify_vanilla", "rev": rev_n, "verdict": rec["verdict"]})
        return clip(text)

    # ---------------------------------------------------------------- notes-check
    def notes_check(self, rev=None) -> str:
        p = self.dir / "NOTES.md"
        if not p.exists():
            return "no NOTES.md"
        r = self.rev(rev)
        d = r.data
        try:
            contract, _ = self.contract()
        except ProjectError:
            contract = None
        cur = {"port": set(d["ports"]) | {pp.name for pp in (contract.ports if contract else [])},
               "label": set(d["labels"]),
               "inst": {i["name"] for i in d["instances"]},
               "module": {Path(f).stem for f in r.meta.get("files", [])} | {V.group_key(i["name"]) for i in d["instances"]}
               | {i["part"] for i in d["instances"]} | {f for f in r.meta.get("files", [])}}
        cur["part"] = {i["part"] for i in d["instances"]}
        hist = set()
        for k in self.revs():
            m = Rev(self.dir, k).meta
            hist |= set(m.get("ports", [])) | set(m.get("labels", [])) | set(m.get("instances", [])) | set(m.get("parts", []))
            hist |= {Path(f).stem for f in m.get("files", [])}
        allcur = set().union(*cur.values())
        world = self.world(r)
        spec_boxes = []        # cells the spec reserves on purpose: port boxes, fixed scenery (incl. air reservations), keep_clear, no_write
        if contract:
            spec_boxes += [list(pp.box) for pp in contract.ports if pp.box]
            spec_boxes += [list(f["box"]) for f in contract.fixed]
            spec_boxes += [list(b_) for b_ in list(contract.keep_clear) + list(contract.no_write) + list(contract.invariants.change_boxes)]
        in_spec = lambda pos: any(b_[0] <= pos[0] <= b_[3] and b_[1] <= pos[1] <= b_[4] and b_[2] <= pos[2] <= b_[5] for b_ in spec_boxes)  # noqa: E731

        def expand(kind_names: str):
            """'ff0..ff3' / 'ff0..3' -> ['ff0', ..., 'ff3']; anything else -> [name]."""
            m = re.fullmatch(r"(.*?)(\d+)\.\.(?:\1)?(\d+)", kind_names)
            if m and int(m.group(2)) <= int(m.group(3)) and int(m.group(3)) - int(m.group(2)) < 1000:
                return [f"{m.group(1)}{i}" for i in range(int(m.group(2)), int(m.group(3)) + 1)]
            return [kind_names]
        stale, checked = [], Counter()
        text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group().count("\n"), p.read_text(), flags=re.S)
        for ln, line in enumerate(text.splitlines(), 1):
            for kind, rng in re.findall(r"\b(port|label|module|inst|part):([A-Za-z0-9_.\[\]#/-]+[A-Za-z0-9_\]#])", line):
                for name in expand(rng):
                    checked[kind] += 1
                    if name not in cur[kind]:
                        stale.append(f"NOTES.md:{ln}: {kind}:{name}{' (from ' + rng + ')' if name != rng else ''} does not exist in rev {r.n}"
                                     + (" (it existed in an earlier revision)" if name in hist else ""))
            for tok in re.findall(r"`([^`\s]+)`", line):
                if re.match(r"^(port|label|module|inst|part):", tok) or re.fullmatch(r"[(\[]?-?\d+,\s*-?\d+,\s*-?\d+[)\]]?", tok):
                    continue
                if all(t_ in allcur for t_ in expand(tok)):
                    checked["name"] += 1
                elif tok in hist:
                    checked["name"] += 1
                    stale.append(f"NOTES.md:{ln}: `{tok}` was a port/label/instance/module in an earlier revision but not in rev {r.n}")
            for xs in re.findall(r"[(\[]\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*[)\]]", line):
                pos = tuple(int(v) for v in xs)
                checked["coord"] += 1
                if pos not in world and not any(tuple(v["pos"]) == pos for v in d["ports"].values()) \
                        and not any(tuple(pp.pos) == pos for pp in (contract.ports if contract else [])) and not in_spec(pos):
                    stale.append(f"NOTES.md:{ln}: no block at {list(pos)} in rev {r.n} (and no port there)")
        summary = ", ".join(f"{n} {k}" for k, n in sorted(checked.items())) or "no references found"
        if not stale:
            return f"NOTES OK (rev {r.n}): {summary} checked"
        return clip(f"NOTES STALE (rev {r.n}): {len(stale)} problem(s); {summary} checked\n" + "\n".join(stale[:40]))

    # ---------------------------------------------------------------- export / paste-check
    def export(self, litematic: Optional[str] = None, nbt: Optional[str] = None, rev=None, include_fixed: bool = True,
               force: bool = False) -> str:
        from simulation import export as X
        r = self._current() if rev is None else self.rev(rev)
        try:
            contract, _ = self.contract()
        except ProjectError:
            contract = None
        world = self.world(r, include_fixed)
        budget = contract.budgets.rest if contract else 400
        t0 = time.time()
        try:
            cells, info = X.settle(world, budget)
        except RuntimeError as e:
            return f"EXPORT FAILED: {e}"
        if not info["at_rest"] and not force:
            return (f"EXPORT REFUSED: the build is still changing after {budget} ticks (clock/oscillator?); "
                    "a settled at-rest file cannot be written. Use --force to save the state after the budget.")
        nbt_map = {(b[0], b[1], b[2]): b[4] for b in r.data["blocks"] if b[4]}
        ents = [tuple(e) for e in r.data["entities"]]
        out_dir = self.mira / "exports"
        lit = Path(litematic) if litematic else out_dir / f"{self.name}-r{r.n}.litematic"
        li = X.save_litematic(cells, str(lit), nbt_map, ents, name=self.name)
        rec = {"rev": r.n, "hash": r.meta["hash"], "litematic": str(lit.resolve()), "litematic_info": li,
               "settle": info, "include_fixed": include_fixed, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        L = [f"EXPORT rev {r.n}: {lit} ({li['blocks']} blocks, settled in {info['ticks']} ticks"
             + ("" if info["at_rest"] else ", NOT at rest (forced)") + f", {info['changed']} cells changed by settling, "
             f"readback {'ok' if li['readback_mismatches'] == 0 else 'MISMATCH ' + str(li['readback_mismatches'])}) sha256 {li['sha256'][:16]}"]
        L.append(f"  file origin = world {li['origin']} (min corner); scenery from spec.json {'included' if include_fixed and contract else 'not included'}")
        if nbt:
            ni = X.write_structure_nbt(cells, nbt, nbt_map, ents)
            rec["nbt"], rec["nbt_info"] = str(Path(nbt).resolve()), ni
            L.append(f"  vanilla structure {nbt}: {ni['bytes']} bytes, DataVersion {ni['data_version']}, readback "
                     f"{'ok' if ni['readback_mismatches'] == 0 else 'MISMATCH'}; no air stored (load with a structure block)")
        pc = X.path_check(world, cells, budget)
        rec["path_check"] = pc
        L.append(f"  placement path: raw program states vs this baked file after settling: {pc['raw_inworld']} cells differ (quiet load), "
                 f"{pc['raw_placement']} (with an update pass); baked file re-loaded: {pc['baked_inworld']} / {pc['baked_placement']}"
                 + ("  WARNING placement-path sensitive (bistable parts?): hand over THIS baked file, never the raw program blocks"
                    if pc["sensitive"] else "  -> not placement-path sensitive")
                 + ("" if pc["paste_stable"] else "  WARNING baked file is not paste-stable in Nucleation"))
        if info["place_errors"]:
            L.append("  engine placement errors: " + "; ".join(info["place_errors"][:3]))
        L.append(f"  {time.time() - t0:.1f}s; next: paste-check")
        out_dir.mkdir(exist_ok=True)
        (out_dir / "last_export.json").write_text(json.dumps(rec, indent=1))
        self._log({"event": "export", "rev": r.n, "file": str(lit)})
        return "\n".join(L)

    def paste_check(self, file: Optional[str] = None, ticks: int = 40, modes=("known_shape", "place")) -> str:
        from simulation import export as X
        if file is None:
            last = self.mira / "exports" / "last_export.json"
            r = self._current()
            rec = json.loads(last.read_text()) if last.exists() else None
            if rec is None or rec["hash"] != r.meta["hash"] or not Path(rec["litematic"]).exists():
                out = self.export()
                if not out.startswith("EXPORT rev"):
                    return out
                rec = json.loads(last.read_text())
            file, rev_n = rec["litematic"], rec["rev"]
        else:
            rev_n = None
        try:
            receipt = X.paste_check(file, ticks=ticks, modes=tuple(modes))
        except RuntimeError as e:
            return f"PASTE CHECK UNAVAILABLE: {e}"
        receipt["rev"] = rev_n
        receipt["project"] = self.name
        (self.mira / "paste_receipt.json").write_text(json.dumps(receipt, indent=1))
        self._log({"event": "paste_check", "rev": rev_n, "verdict": receipt["verdict"]})
        return X.receipt_text(receipt) + f"\nreceipt: {self.mira / 'paste_receipt.json'}"

    # ---------------------------------------------------------------- modules
    def init_module(self, name: str, part: Optional[str] = None) -> str:
        if not name.isidentifier():
            raise ProjectError(f"module name {name!r} must be a Python identifier")
        part = part or name
        mp = self.dir / "modules" / f"{name}.py"
        out = []
        if not mp.exists():
            mp.parent.mkdir(exist_ok=True)
            mp.write_text(MODULE_TEMPLATE.format(name=name))
            out.append(f"modules/{name}.py <- template (define @part {part}(p, ...) and register ports with p.port(...))")
        sp = self.dir / "modules" / f"{name}.spec.json"
        entry, text = self._module_program(name, {"part": part})
        asm, b = self.compile(entry, text)
        if asm.errors or b is None or b.errors or not b.blocks:
            out.append(f"spec skeleton not written: module {name} does not compile to blocks yet"
                       + ("" if b is None else ": " + "; ".join(self._issue_lines(asm, b.errors, 3))))
            return "\n".join(out)
        if sp.exists():
            out.append(f"modules/{name}.spec.json exists; not overwritten")
            return "\n".join(out)
        bb = b.bounds()
        ports, fixed, ins, outs = [], [], [], []
        for k, v in b.ports.items():
            pname = k.split(".", 1)[-1]
            if "." in pname:      # ports of parts nested inside the module are not module ports
                continue
            pos = list(v["pos"])
            if v["kind"] == "in":
                dv = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0)}.get(v.get("face") or "", (-1, 0, 0))
                pad = [pos[0] + dv[0], pos[1] + dv[1], pos[2] + dv[2]]
                ports.append({"name": pname, "kind": "pad", "pos": pad, "doc": f"redstone block next to port {k}"})
                fixed.append({"box": pad + pad, "state": "minecraft:air"})
                ins.append(pname)
            else:
                ports.append({"name": pname, "kind": "wire_power", "pos": pos, "doc": f"output dust {k}"})
                outs.append(pname)
        lo = [min(bb[0][i], *[p["pos"][i] for p in ports]) - 1 for i in range(3)] if ports else [c - 1 for c in bb[0]]
        hi = [max(bb[1][i], *[p["pos"][i] for p in ports]) + 1 for i in range(3)] if ports else [c + 1 for c in bb[1]]
        rows = [{"inputs": {i: 0 for i in ins}, "expect": {o: None for o in outs}, "public": True, "label": "fill me in"}]
        spec = {"part": part, "params": {}, "at": [0, 0, 0], "rot": 0, "mirror": None, "contract": {
            "id": f"module_{name}", "title": f"module {name}", "tier": "module", "description": f"module-level spec of {name}",
            "bbox": lo + hi, "build_box": list(bb[0]) + list(bb[1]), "ports": ports, "fixed": fixed,
            "behaviour": {"truth_table": {"rows": rows}}, "budgets": {"rest": 200, "total": 40000, "max_blocks": 100000}}}
        sp.write_text(json.dumps(spec, indent=1))
        out.append(f"modules/{name}.spec.json <- skeleton: {len(ins)} pad inputs (one cell outside each 'in' port, along its face), "
                   f"{len(outs)} wire_power outputs; fill in behaviour.truth_table.rows, then `test --module {name}`")
        return "\n".join(out)


def _raw_note(raw: str, now: str) -> str:
    """'  (raw: ...)' when settling changed a cell; property-level change when the short forms look the same."""
    if raw == now:
        return ""
    if V.short(raw) != V.short(now):
        return f"  (raw: {V.short(raw)})"
    return f"  (raw differs: {V.state_change(raw, now)})"


def _flow(state: str) -> str:
    bid, props = V.split(state)
    opp = {"north": "south", "south": "north", "east": "west", "west": "east"}
    return f" (signal flows {opp[props['facing']]})" if bid in ("repeater", "comparator") and props.get("facing") in opp else ""


def _short_map(d: dict, limit: int = 8) -> str:
    items = list(d.items())
    s = ",".join(f"{k}={v}" for k, v in items[:limit])
    return "{" + s + (f",+{len(items) - limit}" if len(items) > limit else "") + "}"


def _cell_value(state: str) -> str:
    """One char per tick for a raw block: wire power as hex, 1/0 for lit/powered/extended/triggered, '_' for air."""
    bid, props = V.split(state)
    if bid in ("air", "cave_air", "void_air"):
        return "_"
    if bid == "redstone_wire":
        return "0123456789abcdef"[int(props.get("power", "0"))]
    for k in ("lit", "powered", "extended", "triggered", "enabled"):
        if k in props:
            return "1" if props[k] == "true" else "0"
    return V.klass(state)


# --------------------------------------------------------------------------- dispatch (CLI + MCP share it)
COMMANDS = ("init", "build", "overview", "view", "find", "query", "diff", "test", "trace", "notes-check", "export",
            "paste-check", "log", "init-module", "show", "nets", "spec-check", "verify-vanilla")


def run(project, cmd: str, **kw) -> str:
    """Run one project command by name; agent mistakes come back as 'error: ...' text, never as exceptions."""
    try:
        if cmd == "init":
            _, out = Project.init(kw.pop("name", None) or str(project), **kw)
            return out
        p = Project(resolve(project) if not isinstance(project, Path) else project)
        if cmd == "build":
            return p.build(kw.get("message", ""), kw.get("timeout"))
        if cmd == "overview":
            return p.overview(kw.get("rev"))
        if cmd == "view":
            return p.view(kw.get("layer"), kw.get("box"), kw.get("rev"), axis=kw.get("axis"), at=kw.get("at"),
                          settled=bool(kw.get("settled")))
        if cmd == "find":
            return p.find(kw["pattern"], kw.get("rev"))
        if cmd == "query":
            return p.query(kw["x"], kw["y"], kw["z"], kw.get("radius", 0), kw.get("rev"), settled=bool(kw.get("settled")))
        if cmd == "diff":
            return p.diff(kw.get("a"), kw.get("b"), kw.get("examples", 10), bool(kw.get("raw_vs_settled")))
        if cmd == "nets":
            return p.nets(kw.get("rev"), kw.get("name"), bool(kw.get("warnings")))
        if cmd == "test":
            return p.test(kw.get("module"), kw.get("public_only", False), kw.get("fast", False))
        if cmd == "trace":
            return p.trace(kw.get("signals") or [], kw.get("ticks"), kw.get("seq"), kw.get("sets"), kw.get("vector"),
                           kw.get("module"), kw.get("public_only"), kw.get("width", 100), kw.get("from_tick"), kw.get("to_tick"))
        if cmd == "spec-check":
            return p.spec_check(kw.get("module"), kw.get("mutants", 12), bool(kw.get("no_mutation")), kw.get("seed", 0),
                                kw.get("budget", 240.0))
        if cmd == "verify-vanilla":
            return p.verify_vanilla(kw.get("max_vectors", 12), bool(kw.get("all")), kw.get("variants", 0), kw.get("seed", 1),
                                    kw.get("workers", 3), bool(kw.get("second_origin")))
        if cmd == "notes-check":
            return p.notes_check(kw.get("rev"))
        if cmd == "export":
            return p.export(kw.get("litematic"), kw.get("nbt"), kw.get("rev"), kw.get("include_fixed", True), kw.get("force", False))
        if cmd == "paste-check":
            return p.paste_check(kw.get("file"), kw.get("ticks", 40), kw.get("modes") or ("known_shape", "place"))
        if cmd == "log":
            return p.log(kw.get("n", 20))
        if cmd == "init-module":
            return p.init_module(kw["name"], kw.get("part"))
        if cmd == "show":
            return p.overview()
        return f"error: unknown command {cmd!r}; commands: {', '.join(COMMANDS)}"
    except ProjectError as e:
        return f"error: {e}"
    except Exception as e:  # noqa: BLE001 - a tool must never kill the server
        return f"error: {cmd} failed: {type(e).__name__}: {str(e)[:400]}"
