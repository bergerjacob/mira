"""Content identities for local project evidence, not a security/signature system.

A PASS applies to these inputs and options, never merely to a revision number.
Paths inside projects are relative so moving a project does not invalidate receipts.
"""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_DIRS = ("simulation", "env", "mira_mcp", "tools/oracle")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def file_hash(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def engine():
    """Conservative identity: implementation, block registry, library metadata, runtime and oracle pin."""
    files = {str(p.relative_to(ROOT)): file_hash(p) for d in CORE_DIRS for p in sorted((ROOT / d).glob("*.py"))}
    for pattern in ("simulation/blockstates*.json", "lib/*.meta.json", "tools/oracle/java/*.java"):
        files.update({str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.glob(pattern))})
    from tools.oracle.gametest import versions
    try:
        nuc = importlib.metadata.version("nucleation")
    except importlib.metadata.PackageNotFoundError:
        nuc = "unavailable"
    import sys
    return {"implementation": digest(files), "nucleation": nuc, "python": list(sys.version_info[:2]),
            "placement": os.environ.get("MIRA_PLACEMENT", "canonical"), "oracle": versions()}


def bind(project, revision, module=None):
    spec = project.dir / (f"modules/{module}.spec.json" if module else "spec.json")
    provenance = project.import_provenance()
    inputs = {"source": project.source_hash(), "spec": file_hash(spec), "build": revision.meta["hash"], "engine": engine(),
              "import": digest(provenance) if provenance else None}
    return {"schema": 1, "inputs": inputs, "id": digest(inputs)}


def current(record, binding):
    return bool(record and record.get("binding", {}).get("id") == binding["id"])


def stored_path(project_dir, path):
    p = Path(path).resolve()
    try:
        return str(p.relative_to(project_dir))
    except ValueError:
        return str(p)


def resolve_path(project_dir, value):
    p = Path(value).expanduser()
    return p if p.is_absolute() else project_dir / p


def artifact(project_dir, path):
    return {"file": stored_path(project_dir, path), "sha256": file_hash(path)}


def artifact_matches(project_dir, info):
    if not info or not info.get("sha256") or not info.get("file"):
        return False
    return file_hash(resolve_path(project_dir, info["file"])) == info["sha256"]
