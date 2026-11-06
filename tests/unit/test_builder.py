"""Unit tests for simulation/builder.py and simulation/blockstates.py (offline, no server)."""
import glob
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from simulation import blockstates as bs  # noqa: E402
from simulation import builder as B  # noqa: E402

ROOT = Path(__file__).parent.parent.parent
PROMPT = ROOT / "evaluation" / "prompts" / "builder_system.md"
CORPUS = sorted(glob.glob(str(ROOT / "discord_scraper/data/raw_schematics/*/*.litematic")), key=os.path.getsize)

try:
    import nucleation  # noqa: F401
    HAVE_NUC = True
except ImportError:
    HAVE_NUC = False
needs_nuc = pytest.mark.skipif(not HAVE_NUC, reason="nucleation not installed")


def run(src, **kw):
    """Compile in-process (fast); sandbox behaviour is tested separately."""
    kw.setdefault("sandbox", False)
    return B.compile("from simulation.builder import *\n" + src, **kw)


def states(build):
    return {(b[0], b[1], b[2]): b[3] for b in build.blocks}


# --- blockstates: validation -------------------------------------------------------------

def test_normalize_fills_defaults_and_sorts():
    assert bs.normalize("repeater[facing=east]") == "minecraft:repeater[delay=1,facing=east,locked=false,powered=false]"
    assert bs.normalize("minecraft:lever[powered=true,face=floor]") == \
        "minecraft:lever[face=floor,facing=north,powered=true]"


def test_normalize_property_less_block_has_no_brackets():
    assert bs.normalize("stone") == "minecraft:stone"
    assert bs.normalize("minecraft:stone[]") == "minecraft:stone"


def test_unknown_block_error_suggests_close_match():
    with pytest.raises(bs.StateError) as e:
        bs.normalize("minecraft:redstone_wir")
    assert e.value.kind == "unknown_block" and "redstone_wire" in str(e.value)


def test_unknown_property_error_lists_valid_properties():
    with pytest.raises(bs.StateError) as e:
        bs.normalize("repeater[power=3]")
    assert e.value.kind == "unknown_property" and "delay" in str(e.value)


def test_bad_value_error_lists_allowed_values():
    with pytest.raises(bs.StateError) as e:
        bs.normalize("repeater[delay=9]")
    assert e.value.kind == "bad_value" and "1, 2, 3, 4" in str(e.value)
    with pytest.raises(bs.StateError):
        bs.normalize("repeater[facing=up]")
    with pytest.raises(bs.StateError):
        bs.normalize("lever[powered=True]")  # booleans are lowercase


def test_syntax_errors():
    for bad in ("stone[", "stone[a]", "stone[a=]", "lever[powered=true,powered=false]", "", "Stone Block"):
        with pytest.raises(bs.StateError) as e:
            bs.normalize(bad)
        assert e.value.kind == "syntax"


def test_non_strict_passes_foreign_states_through():
    assert bs.normalize("minecraft:copper_golem_statue[zzz=1,aaa=2]", strict=False) == \
        "minecraft:copper_golem_statue[aaa=2,zzz=1]"
    assert bs.normalize("stone[foo=bar]", strict=False) == "minecraft:stone[foo=bar]"


def test_registry_matches_1_21_0():
    assert "minecraft:crafter" in bs.known_blocks() and "minecraft:vault" in bs.known_blocks()
    assert "minecraft:copper_golem_statue" not in bs.known_blocks()  # newer than 1.21.0
    assert bs.block_properties("minecraft:lever")["face"] == ("floor", "wall", "ceiling")


# --- blockstates: rotate / mirror ----------------------------------------------------------

def tr(state, rot=0, mirror=None):
    return bs.transform_state(bs.normalize(state), rot, mirror)


def test_rotate_repeater_east_to_south():
    assert "facing=south" in tr("repeater[facing=east,delay=3]", 90)
    assert "delay=3" in tr("repeater[facing=east,delay=3]", 90)
    assert "facing=west" in tr("repeater[facing=east]", 180)
    assert "facing=north" in tr("repeater[facing=east]", 270)


def test_mirror_repeater_and_stairs_swap_handedness():
    assert "facing=west" in tr("repeater[facing=east]", mirror="x")
    assert "facing=east" in tr("repeater[facing=east]", mirror="z")
    s = tr("oak_stairs[facing=east,shape=inner_left]", mirror="x")
    assert "facing=west" in s and "shape=inner_right" in s


def test_rotate_stairs_keeps_shape():
    s = tr("oak_stairs[facing=north,shape=outer_left,half=top]", 90)
    assert "facing=east" in s and "shape=outer_left" in s and "half=top" in s


def test_rotate_wire_side_properties():
    s = tr("redstone_wire[north=up,east=side]", 90)
    assert "east=up" in s and "south=side" in s and "north=none" in s


def test_mirror_wire_swaps_east_west():
    s = tr("redstone_wire[east=side,west=up]", mirror="x")
    assert "west=side" in s and "east=up" in s


def test_rotate_axis_rotation_orientation_rail():
    assert "axis=z" in tr("oak_log[axis=x]", 90) and "axis=x" in tr("oak_log[axis=x]", 180)
    assert "axis=y" in tr("oak_log[axis=y]", 90)
    assert "rotation=8" in tr("oak_sign[rotation=4]", 90)  # west -> north
    assert "rotation=12" in tr("oak_sign[rotation=4]", mirror="x")  # west -> east
    assert "rotation=4" in tr("oak_sign[rotation=4]", mirror="z")  # west stays west
    assert "orientation=south_up" in tr("crafter[orientation=east_up]", 90)
    assert "shape=south_west" in tr("rail[shape=south_east]", 90)
    assert "shape=east_west" in tr("rail[shape=north_south]", 90)
    assert "shape=ascending_south" in tr("powered_rail[shape=ascending_east]", 90)


def test_mirror_door_hinge_and_chest_type():
    assert "hinge=right" in tr("oak_door[hinge=left,facing=east]", mirror="x")
    assert "type=right" in tr("chest[type=left]", mirror="x")
    assert "type=left" in tr("chest[type=left]", 90)  # rotation keeps the pairing side


def test_vertical_facing_untouched_by_rotation():
    assert "facing=up" in tr("piston[facing=up]", 90)
    assert "facing=down" in tr("hopper[facing=down]", mirror="x")


def test_transform_validates_arguments():
    with pytest.raises(ValueError):
        bs.transform_state("minecraft:stone", 45)
    with pytest.raises(ValueError):
        bs.transform_state("minecraft:stone", 0, "y")


def test_transform_group_laws_over_registry_sample():
    names = sorted(bs.known_blocks())[::6]
    for bid in names:
        props, defaults = bs.block_properties(bid), bs.block_defaults(bid)
        variants = [bs.format_state(bid, defaults)] + [
            bs.format_state(bid, {**defaults, k: v}) for k, vals in props.items() for v in vals]
        for st in variants:
            r = st
            for _ in range(4):
                r = bs.transform_state(r, 90)
            assert r == st, st
            for m in "xz":
                assert bs.transform_state(bs.transform_state(st, 0, m), 0, m) == st
            assert bs.transform_state(bs.transform_state(st, 0, "x"), 0, "z") == bs.transform_state(st, 180)
            for rot, m in ((90, None), (0, "x"), (270, "z")):
                bs.normalize(bs.transform_state(st, rot, m))  # result must still be a valid state


def test_rotate_point_matches_state_rotation():
    assert bs.rotate_point((1, 0, 0), 90, None) == (0, 0, 1)  # east -> south
    assert bs.rotate_point((0, 0, -1), 90, None) == (1, 0, 0)  # north -> east
    assert bs.rotate_point((2, 3, 1), 0, "x") == (-2, 3, 1)


# --- builder API ---------------------------------------------------------------------------

def test_set_canonicalizes_and_air_removes():
    b = run("def build(b):\n b.set((0,0,0),'lever')\n b.set((1,0,0),'stone')\n b.set((1,0,0),air())\n")
    assert b.ok and states(b) == {(0, 0, 0): "minecraft:lever[face=wall,facing=north,powered=false]"}


def test_blocks_sorted_and_tuple_shape():
    b = run("def build(b):\n b.set((2,1,0),'stone')\n b.set((0,0,5),'stone')\n b.set((1,0,0),'stone')\n")
    assert [(x, y, z) for x, y, z, _, _ in b.blocks] == [(1, 0, 0), (0, 0, 5), (2, 1, 0)]
    assert all(len(t) == 5 and t[4] is None for t in b.blocks)


def test_fill_box_and_keep():
    b = run("def build(b):\n b.set((1,0,0),'dirt')\n b.fill((2,0,0),(0,0,1),'stone',keep=True)\n")
    st = states(b)
    assert len(st) == 6 and st[(1, 0, 0)] == "minecraft:dirt" and st[(0, 0, 1)] == "minecraft:stone"


def test_layers_and_unknown_char_error():
    b = run("def build(b):\n b.layers((5,2,1), {'#':'stone','w':wire()}, [['#w.','..#'], ['#  ']])\n")
    assert states(b) == {(5, 2, 1): "minecraft:stone", (6, 2, 1): wire_default(), (7, 2, 2): "minecraft:stone",
                         (5, 3, 1): "minecraft:stone"}
    b = run("def build(b):\n b.layers((0,0,0), {'#':'stone'}, [['#?']])\n")
    assert b.errors and b.errors[0].kind == "unknown_palette_char" and b.errors[0].line == 3


def wire_default():
    return bs.normalize("minecraft:redstone_wire")


def test_points_places_same_block_many_times():
    b = run("def build(b):\n b.points('stone', (0,0,0), (4,0,0), (0,0,4))\n")
    assert len(b.blocks) == 3


def test_validation_errors_carry_source_line_and_continue():
    src = "def build(b):\n b.set((0,0,0),'stone')\n b.set((1,0,0),'minecraft:redstone_wir')\n" \
          " b.set((2,0,0),'repeater[delay=9]')\n b.set((3,0,0),'stone[foo=1]')\n b.set((4,0,0),'stone')\n"
    b = run(src)
    assert [(e.kind, e.line) for e in b.errors] == [("unknown_block", 4), ("bad_value", 5), ("unknown_property", 6)]
    assert len(b.blocks) == 2 and not b.ok  # valid blocks are still placed


def test_runtime_error_reports_line_and_keeps_partial_build():
    b = run("def build(b):\n b.set((0,0,0),'stone')\n x = 1/0\n")
    assert b.errors[0].kind == "runtime" and b.errors[0].line == 4 and "ZeroDivisionError" in b.errors[0].message
    assert len(b.blocks) == 1


def test_api_misuse_is_reported_as_api_error():
    b = run("def build(b):\n b.set((0,0),'stone')\n")
    assert b.errors[0].kind == "api" and "(x, y, z)" in b.errors[0].message and b.errors[0].line == 3


def test_syntax_error_and_missing_build():
    assert B.compile("def build(b:\n", sandbox=False).errors[0].kind == "syntax"
    assert B.compile("x = 1\n", sandbox=False).errors[0].kind == "no_build"


def test_overlap_warning_for_conflicting_sets_only():
    b = run("def build(b):\n b.set((0,0,0),'stone')\n b.set((0,0,0),'dirt')\n b.fill((0,0,0),(1,0,0),'glass')\n"
            " b.set((5,0,0),'stone')\n b.set((5,0,0),'stone')\n")
    assert [w.kind for w in b.warnings] == ["overlap"] and b.warnings[0].line == 4
    assert b.ok


def test_provenance_points_to_part_path_and_line():
    src = ("@part\ndef cell(p):\n    p.set((0,0,0),'stone')\n    p.set((1,0,0),'dirt')\n"
           "def build(b):\n    b.set((9,9,9),'stone')\n    b.place(cell, at=(0,0,0), name='c0')\n"
           "    b.array(cell, 2, (5,0,0), at=(10,0,0), name='arr')\n")
    b = run(src)
    assert b.provenance(9, 9, 9) == ("", 7)
    assert b.provenance(1, 0, 0) == ("c0", 5)
    assert b.provenance(15, 0, 0) == ("arr[1]", 4)


def test_place_rotation_transforms_positions_states_and_ports():
    src = ("@part(ports={'out': P(2,1,0)})\ndef cell(p):\n    p.set((0,0,0),'stone')\n    p.set((1,1,0),repeater(flow=EAST))\n"
           "def build(b):\n    i = b.place(cell, at=(10,0,10), rot=90, name='r')\n    b.port('o', i['out'])\n")
    b = run(src)
    st = states(b)
    assert st[(10, 1, 11)].startswith("minecraft:repeater") and "facing=north" in st[(10, 1, 11)]  # flow south
    assert b.ports["o"]["pos"] == [10, 1, 12]


def test_place_mirror_and_unknown_port_error():
    src = ("@part(ports={'a': P(3,0,0)})\ndef cell(p):\n    p.set((3,0,0),repeater(flow=EAST))\n"
           "def build(b):\n    i = b.place(cell, mirror='x')\n    i['nope']\n")
    b = run(src)
    assert states(b)[(-3, 0, 0)].find("facing=east") > 0  # mirrored flow west -> input east
    assert b.errors and "no port 'nope'" in b.errors[0].message and "a" in b.errors[0].message


def test_array_places_n_copies_with_step_and_ports():
    src = ("@part(ports={'p': P(0,1,0)})\ndef cell(p):\n    p.set((0,0,0),'stone')\n"
           "def build(b):\n    fa = b.array(cell, n=4, step=(0,0,6), at=(1,0,0))\n"
           "    b.set(fa[3]['p'], lamp(), port='last')\n")
    b = run(src)
    assert len([k for k in states(b) if k[1] == 0]) == 4
    assert b.ports["last"]["pos"] == [1, 1, 18] and b.ports["cell[2].p"]["pos"] == [1, 1, 12]


def test_part_cell_runs_once_per_params():
    src = ("calls = []\n@part\ndef cell(p, n=1):\n    calls.append(n)\n    p.fill((0,0,0),(n,0,0),'stone')\n"
           "def build(b):\n    b.array(cell, 3, (0,0,2))\n    b.place(cell, at=(9,0,9), n=2)\n    b.place(cell, at=(9,0,12), n=2)\n"
           "    b.set((50,0,0), 'stone' if len(calls) == 2 else 'dirt')\n")
    b = run(src)
    assert states(b)[(50, 0, 0)] == "minecraft:stone" and len(b.blocks) == 3 * 2 + 3 + 3 + 1


def test_nested_parts_compose_paths_and_transforms():
    src = ("@part\ndef leaf(p):\n    p.set((1,0,0),repeater(flow=EAST))\n"
           "@part\ndef mid(p):\n    p.place(leaf, at=(0,0,2), name='lf')\n"
           "def build(b):\n    b.place(mid, rot=90, name='m')\n")
    b = run(src)
    (x, y, z, st, _), = b.blocks
    assert (x, y, z) == (-2, 0, 1) and "facing=north" in st
    assert b.provenance(x, y, z)[0] == "m/lf"


def test_label_and_port_api():
    b = run("def build(b):\n b.port('in', (1,2,3), 'in', face=EAST)\n b.label('core', (3,0,0), (0,2,0))\n")
    assert b.ports["in"] == {"pos": [1, 2, 3], "kind": "in", "face": "east"}
    assert b.labels["core"] == {"min": [0, 0, 0], "max": [3, 2, 0]}
    b = run("def build(b):\n b.port('a',(0,0,0))\n b.port('a',(1,0,0))\n")
    assert b.errors[0].kind == "duplicate_port"


def test_set_port_kind_guess():
    b = run("def build(b):\n b.set((0,0,0), lever(), port='l')\n b.set((1,0,0), lamp(), port='q')\n")
    assert b.ports["l"]["kind"] == "in" and b.ports["q"]["kind"] == "out"


def test_p_arithmetic():
    p = B.P(1, 2, 3)
    assert p + (1, 1, 1) == (2, 3, 4) and (1, 1, 1) + p == B.P(2, 3, 4) and isinstance(p + (0, 0, 0), B.P)
    assert p.step("south", 2) == (1, 2, 5) and p.step("up") == (1, 3, 3) and p - (1, 2, 3) == (0, 0, 0)


def test_state_factories():
    assert bs.normalize(B.lever(face="floor", facing="east")) == "minecraft:lever[face=floor,facing=east,powered=false]"
    assert "face=wall" in B.lever(facing="west")
    assert "facing=west" in B.repeater(flow="east", delay=2) and "delay=2" in B.repeater(flow="east", delay=2)
    assert "facing=east" in B.repeater(facing="east")  # raw Minecraft property
    assert "facing=south" in B.comparator(flow="north", mode="subtract") and "mode=subtract" in B.comparator(flow="north", mode="subtract")
    assert B.torch() == "minecraft:redstone_torch[lit=true]"
    assert B.torch(wall="east") == "minecraft:redstone_wall_torch[facing=east,lit=true]"
    assert B.button(facing="north", face="floor") == "minecraft:stone_button[face=floor,facing=north,powered=false]"
    assert B.piston("up", sticky=True) == "minecraft:sticky_piston[extended=false,facing=up]"
    assert B.pressure_plate("heavy_weighted", powered=True).endswith("[power=15]")
    for s in (B.observer("east"), B.hopper(), B.dropper(), B.dispenser("west"), B.chest(), B.barrel(), B.slab("oak"),
              B.stairs("oak"), B.target(), B.note_block(), B.daylight_detector(), B.lamp(True), B.wire(),
              B.pressure_plate(), B.redstone_block(), B.solid("smooth_stone")):
        bs.normalize(s)  # every factory yields a valid state
    with pytest.raises(B.BuilderError):
        B.repeater()
    with pytest.raises(B.BuilderError):
        B.repeater(flow="east", facing="west")


def test_snbt_canonical_form_and_items():
    assert B.canon_nbt({"b": 1, "a": True, "c": [1, 2], "d": "x\"y"}) == '{a:1B,b:1,c:[1,2],d:"x\\"y"}'
    assert B.canon_nbt('{ z:1b , A:"s", n:{q:1.5f}, arr:[I;1,2] }') == '{A:"s",arr:[I;1,2],n:{q:1.5F},z:1B}'
    assert B.canon_nbt({}) is None and B.canon_nbt(None) is None
    assert B.canon_nbt(B.items(("minecraft:redstone", 64))) == '{Items:[{Slot:0B,count:64,id:"minecraft:redstone"}]}'
    with pytest.raises(B.SnbtError):
        B.canon_nbt("{a:1")


def test_block_entity_nbt_and_bad_nbt_reported():
    b = run("def build(b):\n b.set((0,0,0), chest(), nbt=items(('minecraft:stone', 3)))\n b.set((1,0,0), 'barrel', nbt='{oops')\n")
    assert b.blocks[0][4] == '{Items:[{Slot:0B,count:3,id:"minecraft:stone"}]}'
    assert b.errors[0].kind == "bad_nbt" and b.errors[0].line == 4
    b = run("def build(b):\n b.set((0,0,0), ('chest', {'Items': []}))\n")
    assert b.ok and b.blocks[0][4] == '{Items:[]}'


def test_entities_follow_part_transforms():
    src = ("@part\ndef cell(p):\n    p.entity('armor_stand', (2.5, 1.0, 0.5), {'Invisible': True})\n"
           "def build(b):\n    b.entity('minecraft:item_frame', (0.5, 0.0, 0.5))\n    b.place(cell, at=(10, 0, 0), rot=90)\n")
    b = run(src)
    assert b.entities == [(0.5, 0.0, 0.5, "minecraft:item_frame", None),
                          (10.5, 1.0, 2.5, "minecraft:armor_stand", "{Invisible:1B}")]


def test_wire_path_straight_with_repeaters_and_supports():
    b = run("def build(b):\n wire_path(b, P(0,1,0), P(40,1,0))\n")
    st = states(b)
    reps = sorted(x for (x, y, z), s in st.items() if "repeater" in s)
    assert reps == [14, 29] and all("facing=west" in st[(x, 1, 0)] for x in reps)  # flow east
    assert all(st[(x, 0, 0)] == "minecraft:stone" for x in range(41))
    assert sum("redstone_wire" in s for s in st.values()) == 41 - 2


def test_wire_path_corner_and_climb_and_skip_occupied():
    b = run("def build(b):\n b.set((0,1,0),lamp())\n wire_path(b, P(0,1,0), P(2,3,2))\n")
    st = states(b)
    assert st[(0, 1, 0)].startswith("minecraft:redstone_lamp")  # occupied endpoint kept
    ys = [y for (x, y, z), s in sorted(st.items()) if "redstone_wire" in s]
    assert max(ys) == 3  # climbed two blocks over the first two horizontal steps
    b = run("def build(b):\n wire_path(b, P(0,1,0), P(1,5,0))\n")
    assert b.errors and b.errors[0].kind == "api"


def test_limits_are_enforced_with_error_not_exception():
    b = run("def build(b):\n b.fill((0,0,0),(999,999,999),'stone')\n", max_blocks=10_000)
    assert b.errors[0].kind == "limit" and b.errors[0].line == 3
    b = run("def build(b):\n for i in range(100):\n  b.set((i,0,0),'stone')\n", max_blocks=50)
    assert b.errors[0].kind == "limit" and len(b.blocks) <= 51
    b = run("def build(b):\n b.array(lambda p: p.set((0,0,0),'stone'), 10**7, (1,0,0))\n")
    assert b.errors and b.errors[0].kind == "api"


def test_build_limit_not_swallowed_by_except_exception():
    b = run("def build(b):\n try:\n  b.fill((0,0,0),(999,999,999),'stone')\n except Exception:\n  pass\n", max_blocks=1000)
    assert b.errors[0].kind == "limit"


def test_to_json_bounds_summary():
    b = run("def build(b):\n b.set((1,2,3),'stone')\n b.set((0,0,0),chest(),nbt={'a':1})\n b.port('p',(1,2,3))\n")
    assert b.bounds() == ((0, 0, 0), (1, 2, 3))
    js = b.to_json()
    assert js[0] == {"x": 0, "y": 0, "z": 0, "state": "minecraft:chest[facing=north,type=single,waterlogged=false]", "nbt": "{a:1}"}
    assert "2 blocks" in b.summary() and "p@(1, 2, 3)" in b.summary()


def test_validate_false_accepts_unknown_blocks():
    b = B.compile("def build(b):\n b.set((0,0,0),'minecraft:copper_golem_statue[x=1]')\n", sandbox=False, validate=False)
    assert b.ok and b.blocks[0][3] == "minecraft:copper_golem_statue[x=1]"


# --- sandbox -------------------------------------------------------------------------------

ESCAPES = {
    "import_os": "import os\ndef build(b):\n pass\n",
    "import_from_os": "from os import system\ndef build(b):\n pass\n",
    "import_subprocess": "import subprocess\ndef build(b):\n pass\n",
    "import_builtin_module_via_builder": "from simulation import blockstates\ndef build(b):\n pass\n",
    "dunder_import": "def build(b):\n __import__('os')\n",
    "eval": "def build(b):\n eval('1+1')\n",
    "exec": "def build(b):\n exec('x=1')\n",
    "open": "def build(b):\n open('/etc/passwd')\n",
    "getattr": "def build(b):\n getattr(b, '_cells')\n",
    "underscore_attr": "def build(b):\n b._cells.clear()\n",
    "class_subclasses": "def build(b):\n ().__class__.__bases__[0].__subclasses__()\n",
    "globals": "def build(b):\n globals()\n",
    "class_def": "class X:\n pass\ndef build(b):\n pass\n",
    "bare_except": "def build(b):\n try:\n  pass\n except:\n  pass\n",
    "generator_frame_walk": "def build(b):\n g = (i for i in [1])\n g.gi_frame.f_back.f_globals\n",
    "type_builtin": "def build(b):\n type(b)\n",
    "relative_import": "from . import x\ndef build(b):\n pass\n",
}


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_sandbox_blocks_escape_in_process(name):
    b = B.compile(ESCAPES[name], sandbox=False)
    assert not b.ok and b.errors[0].kind == "sandbox", (name, b.errors)


def test_sandbox_escape_attempts_blocked_in_subprocess_too():
    for name in ("import_os", "eval", "underscore_attr", "open"):
        b = B.compile(ESCAPES[name])
        assert not b.ok and b.errors[0].kind == "sandbox", (name, b.errors)


def test_dynamic_import_blocked_at_runtime_by_import_hook():
    # reachable only through a name the AST pass allows: a fake module name via importlib is not importable
    b = B.compile("from simulation.builder import *\ndef build(b):\n import math\n b.set((0,0,int(math.sqrt(16))),'stone')\n", sandbox=False)
    assert b.ok and b.blocks[0][2] == 4  # math is allowed
    b = B.compile("def build(b):\n import json\n", sandbox=False)
    assert b.errors[0].kind == "sandbox"


def test_builder_module_object_exposes_only_public_names():
    for name in ("os", "sys", "subprocess", "nucleation", "ROOT", "compile"):
        b = B.compile(f"from simulation.builder import {name}\ndef build(b):\n pass\n", sandbox=False)
        assert b.errors and b.errors[0].kind == "runtime" and "ImportError" in b.errors[0].message, name


def test_print_is_captured_not_emitted(capfd):
    b = run("def build(b):\n print('hello', 3)\n b.set((0,0,0),'stone')\n")
    assert b.log == ["hello 3"] and b.ok
    assert capfd.readouterr().out == ""


def test_sandbox_timeout_kills_infinite_loop():
    b = B.compile("def build(b):\n while True:\n  pass\n", timeout=1)
    assert not b.ok and b.errors[0].kind in ("timeout", "sandbox", "limit")


def test_sandbox_subprocess_memory_bomb_contained():
    b = B.compile("def build(b):\n x = [0] * (10 ** 11)\n", timeout=10)
    assert not b.ok and b.errors[0].kind in ("limit", "runtime", "sandbox")


def test_sandbox_block_cap_in_subprocess():
    b = B.compile("def build(b):\n b.fill((0,0,0),(500,500,500),'stone')\n", max_blocks=1000)
    assert b.errors[0].kind == "limit"


def test_sandbox_subprocess_has_no_inherited_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    b = B.compile("def build(b):\n b.set((0,0,0),'stone')\n")  # no os access exists; the child env is also scrubbed
    assert b.ok
    # white-box: the subprocess env passed by compile() contains no API keys
    src = Path(B.__file__).read_text()
    assert 'env={"PYTHONHASHSEED": "0"' in src and "OPENROUTER" not in src


def test_determinism_same_hash_across_runs_and_hash_seeds():
    src = ("@part\ndef cell(p):\n    for s in {'stone', 'dirt', 'glass', 'oak_planks'}:\n"
           "        p.set((len(s), 0, 0), s)\n"
           "def build(b):\n    b.array(cell, 3, (0, 0, 3))\n")
    h1 = B.compile("from simulation.builder import *\n" + src)
    h2 = B.compile("from simulation.builder import *\n" + src)
    assert h1.ok and h1.hash() == h2.hash()  # str-set order is pinned by PYTHONHASHSEED=0 in the sandbox
    assert h1.blocks == h2.blocks and h1.prov == h2.prov


def test_sandbox_matches_in_process_result():
    src = "from simulation.builder import *\ndef build(b):\n b.fill((0,0,0),(3,0,3),solid())\n b.set((1,1,1),lever(face='floor'),port='x')\n b.set((9,9,9),'bogus')\n"
    a, c = B.compile(src), B.compile(src, sandbox=False)
    assert a.blocks == c.blocks and a.ports == c.ports and [str(e) for e in a.errors] == [str(e) for e in c.errors]


# --- Nucleation-backed: schematic, save, decompile -----------------------------------------

@needs_nuc
def test_schematic_has_blocks_and_validator_catches_what_nucleation_accepts():
    import nucleation as n
    sc = n.Schematic.create("t")
    sc.set_block(0, 0, 0, "minecraft:repeater[facing=up]")  # nucleation silently accepts garbage
    assert sc.block_count() == 1
    b = run("def build(b):\n b.set((0,0,0),'repeater[facing=up]')\n")
    assert not b.ok  # ...the validator does not


@needs_nuc
def test_save_and_reload_litematic_round_trip(tmp_path):
    b = run("def build(b):\n b.fill((0,0,0),(3,0,3),solid())\n b.set((1,1,1),lever(face='floor',facing=EAST))\n"
            " b.set((2,1,2), chest(), nbt=items(('minecraft:stone', 3)))\n")
    path = str(tmp_path / "t.litematic")
    b.save(path)
    blocks, ents, off = B.load_normalized(path)
    assert off == (0, 0, 0) and {p: s for p, (s, _) in blocks.items()} == states(b)
    assert "Items" in blocks[(2, 1, 2)][1]


@needs_nuc
def test_decompile_round_trip_preserves_normalized_content(tmp_path):
    src = ("@part\ndef cell(p):\n    p.fill((0,0,0),(2,0,2),solid())\n    p.set((1,1,1),repeater(flow=EAST,delay=3))\n"
           "    p.set((0,1,0),wire())\n    p.set((2,1,2),lamp())\n"
           "def build(b):\n    b.array(cell, 6, (4,0,0))\n    b.set((0,5,0), chest(facing=SOUTH), nbt=items(('minecraft:redstone', 9)))\n"
           "    b.entity('minecraft:armor_stand', (3.25, 2.0, 1.5))\n    b.fill((0,0,6),(9,3,9), 'stone')\n")
    orig = run(src)
    path = str(tmp_path / "o.litematic")
    orig.save(path)
    text = B.decompile(path)
    again = B.compile(text, sandbox=False, validate=False)
    ref_blocks, ref_ents, _ = B.load_normalized(path)
    assert again.ok
    assert again.state_dict() == ref_blocks
    assert [tuple(e) for e in again.entities] == ref_ents
    assert B.decompile(path) == text  # one fixed printer: same input, same program


@needs_nuc
def test_decompile_finds_arrays_and_fills(tmp_path):
    src = ("@part\ndef cell(p):\n    p.fill((0,0,0),(2,0,2),solid())\n    p.set((1,1,1),repeater(flow=EAST,delay=3))\n"
           "    p.set((0,1,0),wire())\n    p.set((2,1,2),lamp())\n    p.set((2,2,2),'stone')\n"
           "def build(b):\n    b.array(cell, 8, (3,0,0))\n")
    path = str(tmp_path / "a.litematic")
    run(src).save(path)
    text = B.decompile(path)
    assert "b.array(cell0,8,(3,0,0)" in text and "fill(" in text
    assert len(text) < len(json.dumps(run(src).to_json())) / 3
    flat = B.decompile(path, motifs=False)
    assert "array(" not in flat and len(flat) > len(text)


@needs_nuc
def test_decompile_gate_raises_on_mismatch(tmp_path, monkeypatch):
    path = str(tmp_path / "g.litematic")
    run("def build(b):\n b.fill((0,0,0),(3,0,3),'stone')\n b.set((1,1,1),'dirt')\n").save(path)
    monkeypatch.setattr(B, "_print_program", lambda *a, **k: "from simulation.builder import *\ndef build(b):\n    b.set((0,0,0),'stone')\n")
    with pytest.raises(B.DecompileError) as e:
        B.decompile(path)
    assert e.value.reason == "block_mismatch"
    assert B.decompile(path, verify=False)  # the gate can be skipped explicitly


@needs_nuc
def test_decompile_translates_to_min_corner(tmp_path):
    path = str(tmp_path / "off.litematic")
    run("def build(b):\n b.fill((7,-3,20),(9,-3,20),'stone')\n").save(path)
    assert "fill((0,0,0),(2,0,0)" in B.decompile(path)


@needs_nuc
@pytest.mark.skipif(len(CORPUS) < 3, reason="corpus schematics not present")
@pytest.mark.parametrize("idx", [0, 1, 2])
def test_corpus_small_files_round_trip(idx):
    path = CORPUS[idx * 40 + 10]
    try:
        text = B.decompile(path)
    except B.DecompileError as e:
        pytest.skip(f"unsupported corpus file: {e}")
    blocks, ents, _ = B.load_normalized(path)
    rebuilt = B.compile(text, sandbox=False, validate=False)
    assert rebuilt.ok and rebuilt.state_dict() == blocks
    assert [tuple(e) for e in rebuilt.entities] == ents
    # the sandboxed subprocess agrees with the in-process result
    assert B.compile(text, validate=False).hash() == rebuilt.hash()


# --- simulation: system-prompt examples really work ------------------------------------------

def prompt_examples():
    text = PROMPT.read_text()
    out = {}
    for m in re.finditer(r"```python\n# example: (\w+)\n(.*?)```", text, re.S):
        out[m.group(1)] = m.group(2)
    return out


def drive(build, inputs, out_port, table):
    sim = build.simulate()
    sim.run(10)
    ip = [build.local(build.ports[i]["pos"]) for i in inputs]
    q = build.local(build.ports[out_port]["pos"])
    cur = [0] * len(inputs)
    seen = []
    for vec, expected in table:
        for i, v in enumerate(vec):
            if cur[i] != v:
                sim.use_block(*ip[i])
                cur[i] = v
        sim.run(30)
        seen.append(("lit=true" in sim.get_block(*q), expected))
    return seen


def test_prompt_has_three_examples_that_compile_clean():
    ex = prompt_examples()
    assert set(ex) == {"lamp", "not_gate", "and_gate"}
    for name, src in ex.items():
        b = B.compile(src)
        assert b.ok and not b.warnings, (name, b.summary())


@needs_nuc
def test_example_lamp_simulates():
    b = B.compile(prompt_examples()["lamp"])
    assert all(g == e for g, e in drive(b, ["in"], "out", [((0,), False), ((1,), True), ((0,), False)]))


@needs_nuc
def test_example_not_gate_simulates():
    b = B.compile(prompt_examples()["not_gate"])
    assert all(g == e for g, e in drive(b, ["in"], "out", [((0,), True), ((1,), False), ((0,), True)]))


@needs_nuc
def test_example_and_gate_truth_table_simulates():
    b = B.compile(prompt_examples()["and_gate"])
    table = [((0, 0), False), ((1, 0), False), ((1, 1), True), ((0, 1), False), ((1, 0), False), ((1, 1), True), ((0, 0), False)]
    assert all(g == e for g, e in drive(b, ["A", "B"], "Q", table))


@needs_nuc
@pytest.mark.parametrize("rot,mirror", [(90, None), (180, None), (270, None), (0, "z"), (90, "x")])
def test_transformed_inverter_still_inverts(rot, mirror):
    """Rotating/mirroring a part must keep a lever->NOT->lamp cell working (torch + lever + wire states)."""
    src = ("@part(ports={'lever': P(0,1,0), 'lamp': P(6,1,0)})\ndef cell(p):\n"
           "    p.fill((0,0,0),(6,0,0), solid())\n    p.set((1,1,0), solid())\n"
           "    p.set((0,1,0), lever(face='wall', facing=WEST))\n    p.set((2,1,0), torch(wall=EAST))\n"
           "    p.fill((3,1,0),(5,1,0), wire())\n    p.set((6,1,0), lamp())\n"
           f"def build(b):\n    i = b.place(cell, at=(20,0,20), rot={rot}, mirror={mirror!r})\n"
           "    b.port('in', i['lever'], 'in')\n    b.port('out', i['lamp'], 'out')\n")
    b = run(src)
    assert b.ok
    assert all(g == e for g, e in drive(b, ["in"], "out", [((0,), True), ((1,), False), ((0,), True)]))


@needs_nuc
def test_repeater_flow_convention_delivers_signal_forward():
    """repeater(flow=EAST) carries the signal east: lever -> wire -> repeater -> wire -> lamp."""
    def lamp_after(flow):
        src = ("def build(b):\n    b.fill((0,0,0),(8,0,0), solid())\n    b.set((0,1,0), lever(face='floor', facing=EAST), port='in')\n"
               f"    b.fill((1,1,0),(3,1,0), wire())\n    b.set((4,1,0), repeater(flow={flow}, delay=2))\n"
               "    b.fill((5,1,0),(7,1,0), wire())\n    b.set((8,1,0), lamp(), port='out')\n")
        return drive(run(src), ["in"], "out", [((1,), True)])[0][0]
    assert lamp_after("EAST") is True
    assert lamp_after("WEST") is False  # reversed repeater blocks the signal


@needs_nuc
def test_wire_path_with_repeaters_carries_signal_past_15_blocks():
    src = ("def build(b):\n    b.set((0,1,0), lever(face='floor', facing=EAST), port='in')\n    b.set((0,0,0), solid())\n"
           "    wire_path(b, P(1,1,0), P(39,1,0))\n    b.set((40,0,0), solid())\n    b.set((40,1,0), lamp(), port='out')\n")
    b = run(src)
    assert drive(b, ["in"], "out", [((1,), True), ((0,), False)]) == [(True, True), (False, False)]
    no_rep = run(src.replace("P(39,1,0))", "P(39,1,0), repeat_every=100)"))
    assert drive(no_rep, ["in"], "out", [((1,), False)])[0][0] is False  # dust alone decays before 39 blocks


# --- wire_path: fixed cells, boxes, floors, lever climbs, junction repeaters, conflicts ------------

def wp(body, **kw):
    return run("def build(b):\n" + "".join(f" {line}\n" for line in body.strip().splitlines()), **kw)


def dust(build):
    return sorted(p for p, s in states(build).items() if "redstone_wire" in s)


def test_wire_path_returns_the_cells_and_keeps_old_defaults():
    b = wp("cells = wire_path(b, P(0,1,0), P(3,1,2))\nb.set((9,9,9), stone())\nassert len(cells) == 6 and cells[0] == (0,1,0) and cells[-1] == (3,1,2)")
    assert b.ok, b.errors
    assert dust(b)[0] == (0, 1, 0) and states(b)[(0, 0, 0)] == "minecraft:stone"


def test_wire_path_avoid_end_exclusive_and_start_exclusive_write_nothing_on_the_fixed_cells():
    b = wp("wire_path(b, P(0,1,0), P(5,1,0), avoid=[(0,1,0), (5,1,0)], floor_exists=True)")
    assert b.ok and dust(b) == [(x, 1, 0) for x in range(1, 5)] and (0, 1, 0) not in states(b) and (5, 1, 0) not in states(b)
    b = wp("wire_path(b, P(0,1,0), P(5,1,0), start_exclusive=True, end_exclusive=True, floor_exists=True)")
    assert b.ok and dust(b) == [(x, 1, 0) for x in range(1, 5)]
    assert not any(y == 0 for (_, y, _) in states(b))              # floor_exists: no supports below y=1
    b = wp("wire_path(b, P(0,1,0), P(5,1,0))")
    assert dust(b)[0] == (0, 1, 0) and dust(b)[-1] == (5, 1, 0)    # default unchanged: both end cells are dust


def test_wire_path_avoid_boxes_and_crossing_a_fixed_cell_is_an_error_naming_it():
    b = wp("wire_path(b, P(0,1,0), P(5,1,0), avoid=[(3,1,0,3,2,0)])")
    assert not b.ok and "(3, 1, 0)" in b.errors[0].message and "fixed" in b.errors[0].message
    assert b.blocks == []                                           # planned first: nothing written on error
    b = wp("wire_path(b, P(0,1,0), P(4,1,0), avoid=[(0,0,0,4,0,0)])")   # supports inside avoid are assumed to exist
    assert b.ok and not any(y == 0 for (_, y, _) in states(b))


def test_wire_path_floor_exists_skips_only_the_floor_supports():
    b = wp("wire_path(b, P(0,1,0), P(4,3,0), floor_exists=True)")
    st = states(b)
    assert not any(y == 0 for (_, y, _) in st) and any(y == 1 and s == "minecraft:stone" for (_, y, _), s in st.items())
    b = wp("wire_path(b, P(0,5,0), P(4,5,0), floor_exists=True, floor_y=4)")
    assert not any(y == 4 for (_, y, _) in states(b))
    b = wp("wire_path(b, P(0,1,0), P(4,1,0), support=None)")
    assert len(b.blocks) == 5


def test_wire_path_box_rejects_writes_outside_with_coordinates_and_a_floor_hint():
    b = wp("wire_path(b, P(0,1,0), P(4,1,0), box=(0,1,0,4,5,0))")
    assert not b.ok and "(0, 0, 0)" in b.errors[0].message and "floor_exists" in b.errors[0].message
    b = wp("wire_path(b, P(0,1,0), P(4,1,0), box=(0,1,0,4,5,0), floor_exists=True)")
    assert b.ok and len(dust(b)) == 5
    b = wp("wire_path(b, P(0,1,0), P(9,1,0), box=(0,1,0,4,5,0), floor_exists=True)")
    assert not b.ok and "(5, 1, 0)" in b.errors[0].message and b.blocks == []


def test_wire_path_from_a_lever_or_to_a_lamp_never_climbs_diagonally_off_the_component():
    b = wp("b.set((0,1,0), lever(face='floor', facing=EAST))\ncells = wire_path(b, P(0,1,0), P(4,3,0))\n")
    assert b.ok
    assert (1, 1, 0) in states(b) and "redstone_wire" in states(b)[(1, 1, 0)]          # one flat cell next to the lever, then up
    assert dust(b) == [(1, 1, 0), (2, 2, 0), (3, 3, 0), (4, 3, 0)]
    b = wp("b.set((6,3,0), lamp())\nwire_path(b, P(0,1,0), P(6,3,0))")
    assert b.ok and dust(b)[-1] == (5, 3, 0) and (4, 3, 0) in dust(b)                   # climbs early, arrives flat
    b = wp("wire_path(b, P(0,1,0), P(6,3,0), end_exclusive=True)")
    assert b.ok and dust(b)[-1] == (5, 3, 0)
    b = wp("b.set((0,1,0), lever(face='floor', facing=EAST))\nwire_path(b, P(0,1,0), P(2,3,0))")
    assert not b.ok and "flat" in b.errors[0].message                                   # not enough steps for a flat cell + 2 climbs
    b = wp("wire_path(b, P(0,1,0), P(3,3,0))")                                          # a plain dust start still climbs at once
    assert b.ok and dust(b) == [(0, 1, 0), (1, 2, 0), (2, 3, 0), (3, 3, 0)]


def test_wire_path_repeater_never_goes_on_a_junction_a_turn_or_a_tap():
    b = wp("wire_path(b, P(0,1,0), P(12,1,0), repeat_every=4)")
    reps = [x for (x, y, z), s in states(b).items() if "repeater" in s]
    assert reps == [3, 7, 11]
    # a branch already hanging off cell 3: the repeater moves to the next flat cell
    b = wp("b.set((3,1,1), wire())\nb.set((3,0,1), stone())\nwire_path(b, P(0,1,0), P(12,1,0), repeat_every=4)")
    reps = [x for (x, y, z), s in states(b).items() if "repeater" in s and z == 0]
    assert reps[0] == 4 and 3 not in reps
    b = wp("wire_path(b, P(0,1,0), P(12,1,0), repeat_every=4, taps=[(3,1,0)])")
    assert [x for (x, y, z), s in states(b).items() if "repeater" in s][0] == 4
    # a turn is never a repeater: x first, then z; the corner is (4,1,0)
    b = wp("wire_path(b, P(0,1,0), P(4,1,8), repeat_every=5)")
    assert (4, 1, 0) in dust(b)                                     # the 5th cell is the corner: dust there, repeater further on
    reps = [(x, z) for (x, y, z), s in states(b).items() if "repeater" in s]
    assert reps and all(x == 4 and z > 0 for x, z in reps)


def test_wire_path_overwriting_another_block_is_an_error_but_joining_dust_is_fine():
    b = wp("b.set((2,1,0), stone())\nwire_path(b, P(0,1,0), P(4,1,0))")
    assert not b.ok and "(2, 1, 0)" in b.errors[0].message and "stone" in b.errors[0].message
    b = wp("b.set((2,1,0), wire())\nwire_path(b, P(0,1,0), P(4,1,0))")
    assert b.ok and len(dust(b)) == 5
    b = wp("b.set((2,1,0), repeater(flow=EAST))\nwire_path(b, P(0,1,0), P(4,1,0))")      # in-line repeater is kept
    assert b.ok and "repeater" in states(b)[(2, 1, 0)]
    b = wp("b.set((2,1,0), repeater(flow=SOUTH))\nwire_path(b, P(0,1,0), P(4,1,0))")      # across the route: error
    assert not b.ok and "(2, 1, 0)" in b.errors[0].message
    b = wp("b.set((0,1,0), lamp())\nb.set((4,1,0), lever(face='floor', facing=EAST))\nwire_path(b, P(0,1,0), P(4,1,0))")
    assert b.ok                                                                              # end components are left alone


def test_wire_path_error_lists_every_conflict_up_to_a_limit():
    b = wp("b.fill((1,1,0),(8,1,0), stone())\nwire_path(b, P(0,1,0), P(9,1,0))")
    assert not b.ok and "(1, 1, 0)" in b.errors[0].message and "problems" in b.errors[0].message


def test_wire_path_zfirst_routes_along_z_then_x():
    b = wp("wire_path(b, P(0,1,0), P(3,1,2), zfirst=True)")
    assert dust(b) == [(0, 1, 0), (0, 1, 1), (0, 1, 2), (1, 1, 2), (2, 1, 2), (3, 1, 2)]
    b = wp("wire_path(b, P(0,1,0), P(3,1,2))")
    assert dust(b) == [(0, 1, 0), (1, 1, 0), (2, 1, 0), (3, 1, 0), (3, 1, 1), (3, 1, 2)]
    b = wp("wire_path(b, P(0,1,0), P(0,1,-3), zfirst=True)")
    assert [p[2] for p in dust(b)] == [-3, -2, -1, 0]


def test_wire_path_check_spacing_rejects_touching_foreign_dust_but_allows_the_route_itself_and_blocked_diagonals():
    b = wp("b.points(wire(), (3,1,1), (4,1,1))\nb.points(stone(), (3,0,1), (4,0,1))\nwire_path(b, P(0,1,0), P(8,1,0), check_spacing=True)")
    assert not b.ok and "(3, 1, 0)" in b.errors[0].message and "foreign dust" in b.errors[0].message and "(3, 1, 1)" in b.errors[0].message
    b = wp("b.points(wire(), (3,1,2), (4,1,2))\nwire_path(b, P(0,1,0), P(8,1,0), check_spacing=True)")     # one cell apart: fine
    assert b.ok
    b = wp("b.points(wire(), (3,1,1), (4,1,1))\nwire_path(b, P(0,1,0), P(8,1,0))")                         # default stays permissive
    assert b.ok
    # joining a port by ending ON it is the intended way: the port is a path cell, not foreign
    b = wp("b.set((5,1,0), wire())\nwire_path(b, P(0,1,0), P(5,1,0), check_spacing=True)")
    assert b.ok
    # diagonal: foreign dust one level up beside the route connects down to it unless a conductor sits above the LOWER (route) dust
    b = wp("b.set((3,2,1), wire())\nb.set((3,1,1), stone())\nwire_path(b, P(0,1,0), P(6,1,0), check_spacing=True)")
    assert not b.ok and "(3, 2, 1)" in b.errors[0].message
    b = wp("b.set((3,2,1), wire())\nb.set((3,1,1), stone())\nb.set((3,2,0), stone())\nwire_path(b, P(0,1,0), P(6,1,0), check_spacing=True)")
    assert b.ok
