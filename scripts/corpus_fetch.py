#!/usr/bin/env python3
"""Fetch the licence-vetted external corpora into data/external/ and write data/external/MANIFEST.json.

    python scripts/corpus_fetch.py            # clone missing sources (depth 1, throttled), rewrite manifest
    python scripts/corpus_fetch.py --manifest-only

Downloaded files are untrusted data: this script only runs `git clone` and reads file metadata,
never executes or imports anything from the clones. LFS objects are NOT pulled (GIT_LFS_SKIP_SMUDGE).
See docs/DATA_SOURCES.md for the per-source licence reasoning.
"""
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "data" / "external"
SCHEM_EXTS = {".litematic", ".schem", ".schematic", ".nbt"}

# ml_use: clean = permissive licence (CC0/MIT/Apache); permission = explicit permission or copyleft/attribution
# conditions that need a decision before training; internal-only = evaluation/retrieval only.
SOURCES = [
    dict(name="scorpio-file-downloads", url="https://github.com/DuskScorpio/Scorpio-File-Downloads",
         licence="CC0-1.0", licence_file="LICENSE", ml_use="clean",
         notes="Farms/dupers by one author (YouTube). 34 .zip (worlds/guides) are not extracted."),
    dict(name="rouge", url="https://github.com/Dhanika-Botejue/rouge",
         licence="CC0-1.0", licence_file="LICENSE", ml_use="clean",
         notes="70 redstone circuits as JSON block lists with step-by-step prose (src/main/resources/rouge/circuits). "
               "Converted by corpus_convert.py. Authorship of the geometry (hand/LLM) is not stated."),
    dict(name="redstone-verify", url="https://github.com/formalized-si/redstone-verify",
         licence="MIT", licence_file="LICENSE", ml_use="clean",
         notes="43 circuits with Verilog spec.v + .rsl text layouts (seed ACCEPT / mutant REJECT), certificates, "
               "third-party layouts (REDA MIT, Redstone-Compiler MIT, rtl2mc Apache-2.0 per NOTICE). "
               "paper/ is NOT under the MIT grant. Layouts are placed in an MCHPRS world (simulator), cross-checked "
               "on vanilla 1.21.1; they are not hand-built in game."),
    dict(name="claudestone", url="https://github.com/Screendead/claudestone",
         licence="MIT", licence_file="LICENSE", ml_use="clean",
         notes="457 YAML specs (palette+layers, truth tables, tick delays) tested tick-by-tick on a vanilla 26.3 "
               "server; designed by Claude agents. Best spec-checked ground truth found. Converted by corpus_convert.py."),
    dict(name="redstone-cpu", url="https://github.com/qbradq/redstone-cpu",
         licence="MIT", licence_file="LICENSE", ml_use="clean", notes="24 .schem of one redstone CPU design."),
    dict(name="redstone-machine-schematics", url="https://github.com/FlyDogDaDa/Redstone-Machine_Schematics",
         licence="Apache-2.0", licence_file="LICENSE", ml_use="clean", notes="1 litematic (coin prize machine)."),
    dict(name="redstone-schematics-nacl", url="https://github.com/NaCl-5844/redstone_schematics",
         licence="MIT", licence_file="LICENSE", ml_use="clean", notes="A world folder (.mca, not indexed) plus 1 .schem."),
    dict(name="minecraft-computers", url="https://github.com/HashDG/minecraft-computers",
         licence="CC-BY-SA-4.0", licence_file="LICENSE", ml_use="permission",
         notes="2 redstone computers (.schem). Explicit licence, but attribution + share-alike: needs a decision "
               "before training."),
    dict(name="litematicas-leroymilo", url="https://github.com/Leroymilo/Litematicas",
         licence="none", licence_file=None, ml_use="permission",
         notes="'permission-readme': README says 'Feel free to download and use them.' No licence, no explicit ML "
               "clause. 3 litematic + description.md."),
    dict(name="storage-catalog-archive", url="https://github.com/Storage-Catalog/Archive",
         licence="none", licence_file=None, ml_use="permission",
         notes="'permission-readme': machine-readable archive of the Storage Catalog Discord. README: credit the "
               "creators and link the repo; storagecatalog.org FAQ says all data is on GitHub for use in own projects. "
               "No licence file (dictionary/ is GPL-3.0, not used). Per-post authors in data.json. 25 .zip are LFS "
               "pointers (not pulled). Cloned from the new location of Storage-Tech-2/Archive."),
]

NOT_DOWNLOADED = [
    dict(name="hack337-minecraft-schematics", url="https://huggingface.co/datasets/Hack337/Minecraft-Schematics",
         licence="MIT (uploader's HF tag)", ml_use="internal-only",
         notes="Metadata read via fetch_content only (huggingface.co is outside the bash network allowlist). 669 builds "
               "(mostly houses/landmarks, ~64 .litematic + 4 .schem raw, rest are converted JSON grids). The MIT tag is "
               "the uploader's; no provenance statement for the underlying third-party builds. Low redstone value."),
    dict(name="desquerteur-schematic-library", url="https://github.com/desquerteur/schematic-library",
         licence="MIT (repo)", ml_use="internal-only",
         notes="Not downloaded: imports third-party builds that 'remain with their original authors and licences' "
               "(THIRD_PARTY_ATTRIBUTION.md); duplicates Scorpio files."),
    dict(name="rom1504-minecraft-schematics-dataset", url="https://github.com/rom1504/minecraft-schematics-dataset",
         licence="none", ml_use="internal-only", notes="No licence; scraped third-party builds."),
    dict(name="piscescup-mc-java-schematics", url="https://github.com/Piscescup/MC-Java-Schematics",
         licence="none", ml_use="internal-only", notes="No licence."),
    dict(name="gpl-schematic-repos", url="https://github.com/RedstoneTools/redstone-library",
         licence="GPL-2.0/3.0", ml_use="internal-only",
         notes="Copyleft redstone schematic repos (RedstoneTools/redstone-library, BEGINWITHF/Minecraft-schematics, "
               "zsoltfrks/schematics) skipped: copyleft on data is ambiguous for model training."),
    dict(name="site-restricted", url="n/a", licence="site ToS", ml_use="internal-only",
         notes="PlanetMinecraft, minecraft-schematics.com, Reddit, YouTube, ORE/Schemati, Discord: not accessed."),
]


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)


def clone(src):
    dest = EXT / src["name"]
    if (dest / ".git").exists():
        return False
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} exists and is not a git clone; refusing to touch it")
    dest.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", src["url"] + ".git", str(dest)],
                       env=env, capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise SystemExit(f"clone {src['name']} failed: {r.stderr[:300]}")
    time.sleep(2)  # be polite
    return True


def describe(src):
    d = EXT / src["name"]
    out = dict(src, dir=str(d.relative_to(ROOT)), present=d.exists())
    if not d.exists():
        return out
    out["commit"] = run(["git", "rev-parse", "HEAD"], d).stdout.strip()
    out["commit_date"] = run(["git", "log", "-1", "--format=%cI"], d).stdout.strip()
    counts = collections.Counter()
    nbytes = 0
    for p in d.rglob("*"):
        if ".git" in p.parts or not p.is_file():
            continue
        counts[p.suffix.lower() or "(none)"] += 1
        nbytes += p.stat().st_size
    out["file_counts"] = dict(counts.most_common())
    out["schematic_files"] = sum(n for e, n in counts.items() if e in SCHEM_EXTS)
    out["size_bytes"] = nbytes
    lf = src.get("licence_file")
    if lf and (d / lf).exists():
        out["licence_file_sha256"] = hashlib.sha256((d / lf).read_bytes()).hexdigest()
    out["fetched"] = time.strftime("%Y-%m-%d", time.gmtime((d / ".git").stat().st_mtime))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest-only", action="store_true")
    args = ap.parse_args()
    EXT.mkdir(parents=True, exist_ok=True)
    if not args.manifest_only:
        for s in SOURCES:
            if clone(s):
                print("cloned", s["name"])
    manifest = dict(
        generated=time.strftime("%Y-%m-%d", time.gmtime()),
        note="ml_use: clean (permissive licence) | permission (README permission or copyleft/attribution; decide "
             "before training) | internal-only. Downloaded files are untrusted data. nucleation_src is owned by "
             "another agent and is not part of this manifest.",
        sources=[describe(s) for s in SOURCES],
        not_downloaded=NOT_DOWNLOADED,
    )
    (EXT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for s in manifest["sources"]:
        print(f"{s['name']:32s} {s.get('commit', '-')[:10]:10s} {s['ml_use']:10s} schem_files={s.get('schematic_files')}")


if __name__ == "__main__":
    sys.exit(main())
