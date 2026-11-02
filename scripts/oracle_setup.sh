#!/usr/bin/env bash
# Idempotent setup of the vanilla oracle (Nucleation's tools/gametest on Minecraft 26.2).
#
#   bash scripts/oracle_setup.sh           # clone, download jar, extract, compile
#   MC_VERSION=26.2 NUCLEATION_REF=<sha> bash scripts/oracle_setup.sh
#
# Everything lands under data/external (gitignored):
#   nucleation_src/   upstream clone (UNTRUSTED data: never cd in and run interpreters)
#   oracle_work/      server.jar (sha1-verified), inner/ (unbundled server + libs),
#                     classes/ (compiled drivers), oracle_versions.json (provenance)
#
# Terms: the jar comes from Mojang's public launcher metadata and stays local (never
# commit/redistribute it). No EULA acceptance is needed because the server is never
# started as a public server; GameTestServer runs in-process (see docs/ORACLE.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/data/external"
SRC="${NUCLEATION_SRC:-$EXT/nucleation_src}"
WORK="${ORACLE_WORK:-$EXT/oracle_work}"
MC_VERSION="${MC_VERSION:-26.2}"
# Commit the oracle was validated against (2026-10-23 master).
NUCLEATION_REF="${NUCLEATION_REF:-346733e9f47f50d0ecfac56160b9c6b9a597685f}"
MANIFEST_URL="https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

mkdir -p "$EXT" "$WORK"

# --- 1. upstream source (pinned) ----------------------------------------------
if [[ ! -d "$SRC/.git" ]]; then
    echo "==> cloning Schem-at/Nucleation"
    git clone --quiet https://github.com/Schem-at/Nucleation.git "$SRC"
fi
if [[ "$(git -C "$SRC" rev-parse HEAD)" != "$NUCLEATION_REF" ]]; then
    echo "==> checking out pinned Nucleation $NUCLEATION_REF"
    git -C "$SRC" fetch --quiet origin "$NUCLEATION_REF" 2>/dev/null || git -C "$SRC" fetch --quiet --unshallow origin
    git -C "$SRC" -c advice.detachedHead=false checkout --quiet "$NUCLEATION_REF"
fi

# --- 2. server jar (cached, sha1 from Mojang's metadata) -------------------------
JAR="$WORK/server.jar"
META="$WORK/version_$MC_VERSION.json"
if [[ ! -f "$META" ]]; then
    echo "==> fetching Minecraft $MC_VERSION version metadata"
    fetch() { curl -sSL --fail -o "$2" "$1" || { echo "FAIL: cannot download $1 (blocked host? see docs/ORACLE.md)" >&2; exit 1; }; }
    fetch "$MANIFEST_URL" "$WORK/version_manifest.json"
    URL=$(python3 -I -c '
import json, sys
m = json.load(open(sys.argv[1]))
print(next(v for v in m["versions"] if v["id"] == sys.argv[2])["url"])' "$WORK/version_manifest.json" "$MC_VERSION")
    fetch "$URL" "$META"
fi
read -r JAR_URL JAR_SHA1 JAR_SIZE < <(python3 -I -c '
import json, sys
d = json.load(open(sys.argv[1]))["downloads"]["server"]
print(d["url"], d["sha1"], d["size"])' "$META")
if [[ ! -f "$JAR" ]] || [[ "$(sha1sum "$JAR" | cut -d' ' -f1)" != "$JAR_SHA1" ]]; then
    echo "==> downloading server.jar ($JAR_SIZE bytes)"
    curl -sSL --fail -o "$JAR.part" "$JAR_URL" \
        || { echo "FAIL: cannot download $JAR_URL (blocked host? see docs/ORACLE.md)" >&2; exit 1; }
    mv "$JAR.part" "$JAR"
fi
if [[ "$(sha1sum "$JAR" | cut -d' ' -f1)" != "$JAR_SHA1" ]]; then
    echo "FAIL: server.jar sha1 mismatch (expected $JAR_SHA1)" >&2
    exit 1
fi

# --- 3. unbundle server + libraries -----------------------------------------------
INNER="$WORK/inner/META-INF/versions/$MC_VERSION/server-$MC_VERSION.jar"
if [[ ! -f "$INNER" ]]; then
    echo "==> extracting bundled server and libraries"
    rm -rf "$WORK/inner"
    unzip -o -q "$JAR" "META-INF/versions/*" "META-INF/libraries/*" -d "$WORK/inner"
fi
CP="$INNER:$(find "$WORK/inner/META-INF/libraries" -name '*.jar' | sort | tr '\n' ':')"

# --- 4. compile drivers (upstream's + ours) -----------------------------------------
echo "==> compiling drivers"
mkdir -p "$WORK/classes"
javac -nowarn -cp "$CP" -d "$WORK/classes" \
    "$SRC/tools/gametest/src/Snbt2Nbt.java" "$SRC/tools/gametest/src/RunGameTests.java" \
    "$SRC/tools/gametest/src/TraceCapture.java" "$SRC/tools/gametest/src/EntityDims.java" \
    "$ROOT/tools/oracle/java/OracleBatch.java"

# --- 5. provenance --------------------------------------------------------------------
WORLD_VERSION=$(unzip -p "$INNER" version.json | python3 -I -c '
import json, sys
d = json.load(sys.stdin)
print(json.dumps({"mc_version": d["id"], "name": d["name"], "data_version": d["world_version"],
                  "pack_version": d.get("pack_version"), "java_version": d.get("java_version")}))')
python3 -I - "$WORK/oracle_versions.json" "$WORLD_VERSION" "$JAR_URL" "$JAR_SHA1" \
    "$NUCLEATION_REF" "$(java -version 2>&1 | head -1)" <<'PY'
import json, sys, time
out, world, url, sha1, ref, java = sys.argv[1:7]
d = json.loads(world)
d.update({"mc_version_requested": d["mc_version"], "server_jar_url": url, "server_jar_sha1": sha1,
          "nucleation_ref": ref, "java": java, "setup_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
json.dump(d, open(out, "w"), indent=2)
print(json.dumps(d, indent=2))
PY
echo "==> oracle ready under $WORK"
