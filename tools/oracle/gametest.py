"""Python wrapper around the vanilla oracle (Mojang's in-process GameTestServer).

Block list (or .litematic/.schem) + Plan -> per-tick Trace of every block-state change in
the build region, from real Minecraft. One JVM runs many captures (`capture_batch`);
`capture_many` fans batches out over several JVMs. Setup: `bash scripts/oracle_setup.sh`.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from tools.oracle.sim import structure_snbt
from tools.oracle.trace import Plan, Pos, Trace, canon_state

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("ORACLE_WORK", ROOT / "data/external/oracle_work"))
CLASSES = Path(os.environ.get("ORACLE_CLASSES", WORK / "classes"))   # override to test a recompiled OracleBatch
JAVA = os.environ.get("JAVA", "java")
# Conservative JVM flags; each oracle JVM is a full Minecraft server (~1 GB resident).
JVM_FLAGS = ["-Xmx1536m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1", "-Xshare:auto"]


class OracleUnavailable(RuntimeError):
    pass


def versions() -> dict:
    p = WORK / "oracle_versions.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _recompile_if_stale(inner: Path, libs: list) -> None:
    """Rebuild OracleBatch when tools/oracle/java/OracleBatch.java is newer than the installed class (same javac
    call as scripts/oracle_setup.sh, under a lock so parallel processes do not race)."""
    src, cls = ROOT / "tools/oracle/java/OracleBatch.java", CLASSES / "OracleBatch.class"
    if CLASSES != WORK / "classes" or not src.exists() or not cls.exists() or cls.stat().st_mtime >= src.stat().st_mtime:
        return
    import fcntl
    with open(WORK / ".compile.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if cls.stat().st_mtime >= src.stat().st_mtime:      # another process did it meanwhile
            return
        cp = os.pathsep.join([str(CLASSES), str(inner)] + [str(x) for x in libs])
        r = subprocess.run(["javac", "-nowarn", "-cp", cp, "-d", str(CLASSES), str(src)], capture_output=True, text=True)
        if r.returncode:
            raise OracleUnavailable(f"javac failed for OracleBatch.java:\n{r.stderr[-2000:]}")


def classpath() -> str:
    v = versions()
    mc = v.get("mc_version", "26.2")
    inner = WORK / f"inner/META-INF/versions/{mc}/server-{mc}.jar"
    libs = sorted((WORK / "inner/META-INF/libraries").rglob("*.jar"))
    if inner.exists():
        _recompile_if_stale(inner, libs)
    if not inner.exists() or not (CLASSES / "OracleBatch.class").exists():
        raise OracleUnavailable(f"oracle not installed under {WORK}; run scripts/oracle_setup.sh")
    return os.pathsep.join([str(CLASSES), str(inner)] + [str(x) for x in libs])


def available() -> bool:
    try:
        classpath()
        return shutil.which(JAVA) is not None
    except OracleUnavailable:
        return False


Job = Tuple  # (name, blocks, plan) or (name, blocks, plan, source_data_version)


def load_build(path):
    """(flat block list incl. block-entity NBT as SNBT, source DataVersion) from .litematic/.schem via Nucleation.

    Entities (mobs, items) are dropped; the oracle capture covers blocks and containers only.
    """
    from nucleation import Schematic
    sch = Schematic.load_from_file(str(path))
    bes = {tuple(e["position"]): e["snbt"] for e in json.loads(sch.get_all_block_entities_snbt_json())}
    out = []
    for e in json.loads(sch.get_all_blocks_json()):
        if e["name"].endswith(":air"):
            continue
        props = ",".join(f"{k}={v}" for k, v in e.get("properties", []))
        state = e["name"] + (f"[{props}]" if props else "")
        out.append((e["x"], e["y"], e["z"], state, bes.get((e["x"], e["y"], e["z"]))))
    return out, sch.source_data_version()


def load_blocks(path) -> list:
    return load_build(path)[0]


def _job_json(name: str, blocks, plan: Plan, seed: int = 0, source_dv: Optional[int] = None) -> Tuple[dict, Pos]:
    snbt, shift = structure_snbt(blocks, source_dv or versions().get("data_version", 4903))
    ops = [{"tick": o.tick, "op": o.op,
            "pos": [o.pos[0] + shift[0], o.pos[1] + shift[1], o.pos[2] + shift[2]], "block": o.block}
           for o in plan.ops]
    job = {"id": name, "snbt": snbt, "ticks": plan.ticks, "preroll": plan.preroll,
           "settle": plan.settle, "seed": seed, "ops": ops}
    if plan.origin is not None:
        job["origin"] = [int(v) for v in plan.origin]
    return job, shift


def _to_trace(res: dict, shift: Pos, plan: Plan, wall_ms: float) -> Trace:
    if "error" in res:
        raise RuntimeError(f"oracle job {res.get('id')} failed: {res['error']}")
    back = lambda p: (p[0] - shift[0], p[1] - shift[1], p[2] - shift[2])
    st = lambda rows: {back(r[:3]): canon_state(r[3]) for r in rows}
    changes = {}
    for entry in res["tick_changes"]:
        changes[entry["tick"]] = [(back(c[:3]), canon_state(c[3]), canon_state(c[4]))
                                  for c in entry["changes"]]
    inv = lambda rows: [{"pos": back(r["pos"]), "slots": r["slots"]} for r in rows]
    return Trace(
        backend="vanilla-oracle", mc_version=res["mc_version"],
        data_version=res.get("data_version") or versions().get("data_version", 0),
        origin=tuple(res["origin"]), n_ticks=res["ticks"], placed=st(res["placed"]),
        initial=st(res["initial"]), changes=changes, wall_ms=wall_ms,
        meta={"tick_ms": res["tick_ms"], "job_ms": res["job_ms"], "boot_ms": res["boot_ms"],
              "warmup_ticks": res["warmup_ticks"], "settle": plan.settle,
              "inventory_initial": inv(res["inventory_initial"]),
              "inventory_events": [{**e, "pos": back(e["pos"])} for e in res["inventory_events"]]})


def capture_batch(jobs: Sequence[Job], idle: int = 10, seed: int = 0, keep_dir: Optional[str] = None,
                  timeout: Optional[float] = None, isolated: bool = False, workers: int = 1) -> List[Trace]:
    """Run `jobs` sequentially in ONE JVM; returns traces in order (raises on a failed job).

    `idle` ticks run after wiping the region between jobs so no scheduled tick, block
    event or torch-burnout history leaks into the next capture.

    `isolated=True` runs every job in its own fresh JVM (3-20 s each, `workers` at a time): the reference
    verdict for anything that is reported as a result. Batched traces are tested to be identical
    (tests/oracle/test_isolation.py), but a JVM per job cannot be contaminated by an earlier job at all.
    """
    if isolated:
        jobs = list(jobs)
        kw = dict(idle=idle, seed=seed, timeout=timeout)
        if keep_dir:
            raise ValueError("keep_dir is not supported with isolated=True")
        if workers <= 1 or len(jobs) <= 1:
            return [capture_batch([j], **kw)[0] for j in jobs]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(min(workers, len(jobs))) as ex:
            return list(ex.map(lambda j: capture_batch([j], **kw)[0], jobs))
    cp = classpath()
    tmp = Path(keep_dir or tempfile.mkdtemp(prefix="oracle_", dir=WORK))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        specs = [_job_json(j[0], j[1], j[2], seed, j[3] if len(j) > 3 else None) for j in jobs]
        (tmp / "jobs.json").write_text(json.dumps([s[0] for s in specs]))
        out_dir = tmp / "out"
        t0 = time.perf_counter()
        proc = subprocess.run(
            [JAVA, *JVM_FLAGS, "-cp", cp, "OracleBatch", "--universe", str(tmp / "universe"),
             "--jobs", str(tmp / "jobs.json"), "--out", str(out_dir), "--idle", str(idle)],
            capture_output=True, text=True, timeout=timeout, cwd=str(tmp))
        wall = (time.perf_counter() - t0) * 1000
        (tmp / "stdout.log").write_text(proc.stdout + "\n--stderr--\n" + proc.stderr)
        traces = []
        for (name, _b, plan, *_dv), (_spec, shift) in zip(jobs, specs):
            f = out_dir / f"{name}.json"
            if not f.exists():
                raise RuntimeError(f"oracle produced no result for {name} (rc={proc.returncode}); "
                                   f"see {tmp / 'stdout.log'}")
            tr = _to_trace(json.loads(f.read_text()), shift, plan, wall / max(1, len(jobs)))
            tr.meta["batch_wall_ms"] = wall
            traces.append(tr)
        return traces
    finally:
        if keep_dir is None:
            shutil.rmtree(tmp, ignore_errors=True)


def capture(blocks, plan: Plan, name: str = "capture", source_data_version: Optional[int] = None, **kw) -> Trace:
    return capture_batch([(name, blocks, plan, source_data_version)], **kw)[0]


def capture_many(jobs: Sequence[Job], workers: int = 4, **kw) -> List[Trace]:
    """Split jobs round-robin over `workers` JVMs running concurrently; order preserved.
    With `isolated=True` every job gets its own JVM instead (`workers` at a time)."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = list(jobs)
    if kw.get("isolated"):
        return capture_batch(jobs, workers=workers, **kw)
    workers = max(1, min(workers, len(jobs)))
    shards = [jobs[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(lambda s: capture_batch(s, **kw) if s else [], shards))
    out: List[Optional[Trace]] = [None] * len(jobs)
    for i, shard in enumerate(results):
        for k, tr in enumerate(shard):
            out[i + k * workers] = tr
    return out  # type: ignore[return-value]
