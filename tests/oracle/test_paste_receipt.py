"""Offline tests of scripts/paste_receipt_server.py against a fake RCON server (no Minecraft needed)."""
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("paste_receipt_server", ROOT / "scripts/paste_receipt_server.py")
prs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prs)


class FakeServer:
    """Interprets the handful of commands the receipt script sends; fillUpdates false = literal states."""

    def __init__(self, reject=()):
        self.world, self.log, self.reject = {}, [], set(reject)

    def cmd(self, c):
        self.log.append(c)
        if m := re.match(r"setblock (-?\d+) (-?\d+) (-?\d+) (\S+)", c):
            st = m.group(4).split("{")[0]
            if any(r in st for r in self.reject):
                return f"Block {st} does not have property 'x'"
            self.world[tuple(map(int, m.groups()[:3]))] = st
            return "Changed the block"
        if m := re.match(r"fill (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (\S+)", c):
            a, b, st = tuple(map(int, m.groups()[:3])), tuple(map(int, m.groups()[3:6])), m.group(7)
            for x in range(a[0], b[0] + 1):
                for y in range(a[1], b[1] + 1):
                    for z in range(a[2], b[2] + 1):
                        if st == "air":
                            self.world.pop((x, y, z), None)
                        else:
                            self.world[(x, y, z)] = st
            return "Successfully filled"
        if m := re.match(r"script run .*range\((-?\d+), (-?\d+)\).*block\(_, (-?\d+), (-?\d+)\)", c):
            x0, x1, y, z = map(int, m.groups())
            vals = []
            for x in range(x0, x1):
                st = self.world.get((x, y, z), "minecraft:air").replace("minecraft:", "")
                vals.append(st if "[" in st else st + "[]")
            return " = " + "|".join(vals) + " (1µs)"
        return ""


BUILD = {(0, 0, 0): ("minecraft:stone", None), (1, 0, 0): ("minecraft:stone", None), (2, 0, 0): ("minecraft:stone", None),
         (1, 1, 0): ("minecraft:redstone_wire[east=side,north=none,power=0,south=none,west=side]", None),
         (0, 1, 0): ("minecraft:lever[face=floor,facing=east,powered=false]", None)}


def test_norm_drops_air_and_canonicalises():
    out = prs.norm([[0, 0, 0, "stone"], [1, 0, 0, "air"], {"x": 2, "y": 0, "z": 0, "state": "lever[powered=false,face=floor]"}])
    assert out == {(0, 0, 0): ("minecraft:stone", None), (2, 0, 0): ("minecraft:lever[face=floor,powered=false]", None)}


def test_boxes_tile_the_region():
    cells = set()
    for a, b in prs.boxes((0, 0, 0), (40, 3, 70)):
        assert all(b[i] - a[i] < 32 for i in range(3))
        cells |= {(x, y, z) for x in range(a[0], b[0] + 1) for y in range(a[1], b[1] + 1) for z in range(a[2], b[2] + 1)}
    assert len(cells) == 41 * 4 * 71


def test_bad_response_detection():
    assert prs.bad("Unknown block type 'minecraft:foo'")
    assert prs.bad("Block minecraft:redstone_wire does not have property 'foo'")
    assert not prs.bad("Could not set the block")       # already that block: not a rejection
    assert not prs.bad("Changed the block at 1, 2, 3")


def test_paste_is_frozen_updateless_bottom_up_with_fill_runs():
    srv = FakeServer()
    info = prs.paste(srv, BUILD, (100, 64, 200), rate=1000)
    first_block = next(i for i, c in enumerate(srv.log) if c.startswith("setblock") or "stone" in c)
    assert srv.log.index("tick freeze") < first_block and srv.log.index("carpet fillUpdates false") < first_block
    assert "fill 100 64 200 102 64 200 minecraft:stone" in srv.log          # one fill for the run of stone
    placing = [c for c in srv.log if c.startswith("setblock") or ("stone" in c and c.startswith("fill"))]
    assert placing[0].startswith("fill 100 64 200")                         # bottom layer first
    assert info["commands"] == 3 and info["rejected"] == []


def test_paste_reports_rejected_states():
    srv = FakeServer(reject=("lever",))
    info = prs.paste(srv, BUILD, (0, 64, 0), rate=1000)
    assert [r["state"] for r in info["rejected"]] == ["minecraft:lever[face=floor,facing=east,powered=false]"]


def test_read_back_and_compare_roundtrip():
    srv = FakeServer()
    info = prs.paste(srv, BUILD, (0, 64, 0), rate=1000)
    got = prs.read_box(srv, *info["box"])
    rec = prs.compare(BUILD, info, got)
    assert rec["mismatched"] == 0 and rec["matched"] == rec["cells"] == 3 * 2 * 1 and rec["blocks"] == 5


def test_compare_finds_a_changed_block():
    srv = FakeServer()
    info = prs.paste(srv, BUILD, (0, 64, 0), rate=1000)
    srv.world[(1, 65, 0)] = "minecraft:redstone_wire[east=side,north=side,power=0,south=side,west=side]"
    srv.world[(-1, 64, 0)] = "minecraft:stone"                              # outside the build, inside the margin
    rec = prs.compare(BUILD, info, prs.read_box(srv, *info["box"]))
    assert rec["mismatched"] == 1 and rec["examples"][0]["pos"] == [1, 1, 0] and rec["non_air_in_margin"] == 1


def test_block_entities_counted():
    class S(FakeServer):
        def cmd(self, c):
            return "0, 64, 0 has the following block data: {}" if c.startswith("data get") else super().cmd(c)
    build = {(0, 0, 0): ("minecraft:chest[facing=north,type=single,waterlogged=false]", {"Items": []})}
    srv = S()
    info = prs.paste(srv, build, (0, 64, 0), rate=1000)
    assert any(c.startswith("data merge block 0 64 0 {Items:[]}") for c in srv.log)
    assert prs.block_entities(srv, build, info) == {"expected": 1, "present": 1}
