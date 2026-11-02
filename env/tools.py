"""Tool surface for an agent (set_blocks, fill, remove, view, probe, run_spec, submit, undo, task_info).

Independent of any LLM client: every tool is a plain method returning a string. `Env.call(name, **kw)` dispatches
by name and `tool_specs()` gives JSON-schema descriptions for function-calling front ends. The model only ever
sees public vectors; `submit()` evaluates hidden ones and reports counts only.
"""
from typing import Optional

from env import blocks as B
from env.contract import box_cells, in_box
from env.runner import Driver, prepare, run, parse_blocks
from env.sim import Sim, compile_cells, parse, simulatable
from env.score import feedback, score
from env.task import Task

MAX_FILL = 20000
MAX_PROBE = 400

GLYPH = {
    "redstone_wire": "+", "redstone_lamp": "O", "lever": "L", "redstone_torch": "i", "redstone_wall_torch": "t",
    "stone": "#", "smooth_stone": "=", "piston": "P", "sticky_piston": "S", "piston_head": "h", "observer": "B",
    "comparator": "C", "redstone_block": "R", "glass": "g", "hopper": "H", "dropper": "D", "dispenser": "d",
    "target": "T", "air": ".",
}
FLOW = {"west": ">", "east": "<", "south": "^", "north": "v"}  # repeater facing (input side) -> arrow of the flow direction


class BuildState:
    """The model's current build: {pos: state}. Every mutating call pushes one undo step."""

    def __init__(self, task: Task):
        self.task = task
        self.cells = {}
        self.undo_stack = []

    def _push(self):
        self.undo_stack.append(dict(self.cells))

    def apply(self, put=(), delete=()):
        self._push()
        for p in delete:
            self.cells.pop(p, None)
        for p, s in put:
            self.cells[p] = s

    def undo(self):
        if not self.undo_stack:
            return False
        self.cells = self.undo_stack.pop()
        return True

    def as_list(self):
        return [[x, y, z, s] for (x, y, z), s in sorted(self.cells.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))]


class Env:
    def __init__(self, task: Task, seed: int = 0, max_rounds: Optional[int] = None):
        self.task = task
        self.contract = task.contract
        self.state = BuildState(task)
        self.seed = seed
        self.max_rounds = max_rounds
        self.rounds = 0
        self.done = False
        self.final = None            # Score after submit()
        self.last_score = None
        self.fixed = prepare(self.contract, []).fixed

    # ------------------------------------------------------------------ helpers
    def _check_pos(self, pos):
        c = self.contract
        if not in_box(pos, c.build_box):
            return f"{list(pos)} is outside the build box {c.build_box}"
        if pos in self.fixed:
            return f"{list(pos)} is a fixed cell and cannot be changed"
        return None

    def _guard(self):
        return "episode finished: submit() was already called" if self.done else None

    # ------------------------------------------------------------------ tools
    def task_info(self) -> str:
        """Task description, ports, fixed blocks and the public example vectors."""
        return self.contract.describe(public=True)

    def set_blocks(self, blocks: list) -> str:
        """Place blocks: [[x,y,z,"minecraft:redstone_wire"], ...] (or dicts with x,y,z,state). Air removes."""
        if (g := self._guard()):
            return g
        try:
            entries = parse_blocks(blocks)
        except (ValueError, KeyError, TypeError) as e:
            return f"error: malformed blocks ({e}); expected [[x,y,z,state], ...]"
        put, dele, errs = [], [], []
        for pos, state, _nbt in entries:
            if (e := self._check_pos(pos)):
                errs.append(e)
                continue
            try:
                st = B.canonical(state)
            except B.BlockError as ex:
                errs.append(f"{list(pos)}: {ex}")
                continue
            if not B.is_air(st) and not simulatable(st):
                errs.append(f"{list(pos)}: {state} cannot be simulated by Nucleation; use another block/state")
                continue
            if B.is_air(st):
                dele.append(pos)
            else:
                put.append((pos, st))
        if put or dele:
            self.state.apply(put, dele)
        msg = f"placed {len(put)}, removed {len(dele)}; build now has {len(self.state.cells)} blocks"
        if errs:
            msg += f"; REJECTED {len(errs)}:\n  " + "\n  ".join(errs[:10])
        return msg

    def fill(self, x1, y1, z1, x2, y2, z2, state: str) -> str:
        """Fill the box (inclusive corners) with a block state; air clears it."""
        if (g := self._guard()):
            return g
        box = [min(x1, x2), min(y1, y2), min(z1, z2), max(x1, x2), max(y1, y2), max(z1, z2)]
        n = (box[3] - box[0] + 1) * (box[4] - box[1] + 1) * (box[5] - box[2] + 1)
        if n > MAX_FILL:
            return f"error: fill of {n} cells exceeds the limit {MAX_FILL}"
        try:
            st = B.canonical(state)
        except B.BlockError as e:
            return f"error: {e}"
        cells = [p for p in box_cells(box)]
        errs = [self._check_pos(p) for p in cells]
        ok = [p for p, e in zip(cells, errs) if e is None]
        skipped = len(cells) - len(ok)
        if not B.is_air(st) and not simulatable(st):
            return f"error: {state} cannot be simulated by Nucleation; use another block/state"
        if B.is_air(st):
            self.state.apply(delete=ok)
        else:
            self.state.apply(put=[(p, st) for p in ok])
        return f"filled {len(ok)} cells" + (f" ({skipped} skipped: outside build box or fixed)" if skipped else "") + f"; build now has {len(self.state.cells)} blocks"

    def remove(self, x1, y1, z1, x2=None, y2=None, z2=None) -> str:
        """Remove all blocks in the box (or the single block if only x1,y1,z1 given)."""
        if x2 is None:
            x2, y2, z2 = x1, y1, z1
        return self.fill(x1, y1, z1, x2, y2, z2, "minecraft:air")

    def undo(self) -> str:
        """Undo the last mutating call."""
        if (g := self._guard()):
            return g
        return f"undone; build has {len(self.state.cells)} blocks" if self.state.undo() else "nothing to undo"

    def view(self, layer: Optional[int] = None, axis: str = "y", box: Optional[list] = None, live: bool = False) -> str:
        """ASCII slice of the world (fixed + your blocks). axis y: layer=y, rows=z, cols=x; axis x: rows=y (top first),
        cols=z; axis z: rows=y, cols=x. `box`=[x1,y1,z1,x2,y2,z2] limits the area. live=True shows simulated state
        (wire power as hex, lit lamps) after settling."""
        c = self.contract
        world = {**self.state.cells, **self.fixed}
        if live:
            world = self._live_world(world)
        bx = box or c.bbox
        if axis not in "xyz" or len(axis) != 1:
            return "error: axis must be x, y or z"
        ai = "xyz".index(axis)
        layers = [layer] if layer is not None else list(range(bx[ai], bx[ai + 3] + 1))
        if layer is None and len(layers) > 8:
            return f"error: {len(layers)} layers; pass layer=N or a smaller box (max 8 layers at once)"
        # axes of the slice
        if axis == "y":
            ra, ca, rev = 2, 0, False
        elif axis == "x":
            ra, ca, rev = 1, 2, True
        else:
            ra, ca, rev = 1, 0, True
        rows = list(range(bx[ra], bx[ra + 3] + 1))
        if rev:
            rows.reverse()
        cols = list(range(bx[ca], bx[ca + 3] + 1))
        glyphs, legend = {}, {}
        used = set()

        def glyph(st):
            bid, props = parse(st)
            name = bid.split(":")[-1]
            if live and name == "redstone_wire":
                return format(int(props.get("power", "0")), "x")
            if live and name == "redstone_lamp":
                return "O" if props.get("lit") == "true" else "o"
            key = bid if name == "redstone_wire" else B.join_state(bid, props)
            if key in glyphs:
                return glyphs[key]
            pref = FLOW.get(props.get("facing"), "?") if name in ("repeater",) else GLYPH.get(name)
            if pref is None and name.endswith("_button"):
                pref = "b"
            cand = [pref] if pref else []
            cand += list("abcdefjkmnpqrsuwxyzABEFGIJKMNQUVWXYZ0123456789")
            g = next(ch for ch in cand if ch and ch not in used)
            used.add(g)
            glyphs[key] = g
            legend[g] = key
            return g

        out = []
        for ly in layers:
            out.append(f"--- {axis}={ly} ---")
            head = "     " + "".join(str(v % 10) for v in cols)
            out.append(f"     {'x' if ca == 0 else 'z'}: " + f"{cols[0]}..{cols[-1]}")
            out.append(head)
            for r in rows:
                line = []
                for cc in cols:
                    p = [0, 0, 0]
                    p[ai], p[ra], p[ca] = ly, r, cc
                    st = world.get(tuple(p))
                    line.append("." if st is None else glyph(st))
                out.append(f"{r:>4} " + "".join(line))
        if legend:
            out.append("legend: " + "; ".join(f"{g}={s.replace('minecraft:', '')}" for g, s in sorted(legend.items())))
        if layer is not None or box:
            ps = [f"{p.name}@{list(p.pos)}" for p in c.ports if in_box(p.pos, bx) and p.pos[ai] in layers]
            if ps:
                out.append("ports in slice: " + ", ".join(ps))
        out.append("(repeater arrows show the direction the signal flows; '.' = air)")
        return "\n".join(out)

    def _live_world(self, world):
        sc, origin, _ = compile_cells(world)
        try:
            sim = Sim(sc, origin)
            sim.settle(self.contract.budgets.rest)
            return sim.snapshot()
        except Exception:
            return world

    def probe(self, pos, ticks: int = 20, inputs: Optional[dict] = None) -> str:
        """Watch one cell (or output port name) change over time. `inputs` {"A":1,...} are applied at tick 0 from the
        at-rest start. Reports the state at tick 0 and every change."""
        if (g := self._guard()):
            return g
        c = self.contract
        ports = {p.name: p for p in c.ports}
        label = ""
        if isinstance(pos, str):
            if pos not in ports:
                return f"error: unknown port {pos!r}; ports: {sorted(ports)}"
            label, pos = pos, ports[pos].pos
        pos = tuple(pos)
        ticks = max(1, min(int(ticks), MAX_PROBE))
        pr = prepare(c, self.state.as_list())
        if pr.errors:
            return "error: build does not compile:\n  " + "\n  ".join(pr.errors)
        sc, origin, _ = compile_cells(pr.world, pr.nbt)
        try:
            sim = Sim(sc, origin)
        except Exception as e:
            return f"error: simulator refused to load the build: {e}"
        sim.settle(c.budgets.rest)
        drv = Driver(sim, c)
        for k, v in (inputs or {}).items():
            if k not in ports or not ports[k].is_input:
                return f"error: {k!r} is not an input port"
            drv.set(ports[k], v)
        lines, last = [], None
        for t in range(ticks + 1):
            st = sim.get(pos).replace("minecraft:", "")
            if st != last:
                lines.append(f"t={t}: {st}")
                last = st
            if t < ticks:
                sim.step()
        head = f"probe {label or ''}{list(pos)} for {ticks} ticks" + (f" with inputs {inputs}" if inputs else " (no inputs)")
        return head + "\n" + "\n".join(lines) + (f"\n(no change after t={t})" if len(lines) > 1 else "\n(never changed)")

    def run_spec(self) -> str:
        """Run the public vectors/sequences + invariants on the current build and explain failures."""
        if (g := self._guard()):
            return g
        if self.max_rounds is not None and self.rounds >= self.max_rounds:
            return f"error: feedback budget exhausted ({self.max_rounds} run_spec calls); call submit()"
        self.rounds += 1
        res = run(self.contract, self.state.as_list(), seed=self.seed, public_only=True, fast=True)
        sc = score(self.contract, res)
        self.last_score = sc
        return f"[run_spec #{self.rounds}]\n" + feedback(self.contract, res, sc, final=False)

    def submit(self) -> str:
        """Finish: evaluate everything (hidden vectors included) and return the final score."""
        if (g := self._guard()):
            return g
        res = run(self.contract, self.state.as_list(), seed=self.contract.eval_seed)
        sc = score(self.contract, res)
        self.final, self.done = sc, True
        return "[submit]\n" + feedback(self.contract, res, sc, final=True)

    # ------------------------------------------------------------------ dispatch
    def call(self, name: str, **kw) -> str:
        if name not in TOOL_NAMES:
            return f"error: unknown tool {name!r}; tools: {TOOL_NAMES}"
        try:
            return getattr(self, name)(**kw)
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}"

    def export(self):
        """The current build as a flat block list."""
        return self.state.as_list()


TOOL_NAMES = ["task_info", "set_blocks", "fill", "remove", "view", "probe", "run_spec", "submit", "undo"]


def tool_specs():
    """OpenAI-style function specs (name/description/parameters) for the tools above."""
    num = {"type": "integer"}
    box6 = {"x1": num, "y1": num, "z1": num, "x2": num, "y2": num, "z2": num}
    return [
        {"name": "task_info", "description": Env.task_info.__doc__, "parameters": {"type": "object", "properties": {}}},
        {"name": "set_blocks", "description": Env.set_blocks.__doc__,
         "parameters": {"type": "object", "required": ["blocks"], "properties": {"blocks": {"type": "array", "items": {"type": "array"}}}}},
        {"name": "fill", "description": Env.fill.__doc__,
         "parameters": {"type": "object", "required": list(box6) + ["state"], "properties": {**box6, "state": {"type": "string"}}}},
        {"name": "remove", "description": Env.remove.__doc__,
         "parameters": {"type": "object", "required": ["x1", "y1", "z1"], "properties": box6}},
        {"name": "view", "description": Env.view.__doc__,
         "parameters": {"type": "object", "properties": {"layer": num, "axis": {"type": "string", "enum": ["x", "y", "z"]},
                                                          "box": {"type": "array", "items": num}, "live": {"type": "boolean"}}}},
        {"name": "probe", "description": Env.probe.__doc__,
         "parameters": {"type": "object", "required": ["pos"], "properties": {"pos": {"description": "[x,y,z] or a port name"},
                                                                              "ticks": num, "inputs": {"type": "object"}}}},
        {"name": "run_spec", "description": Env.run_spec.__doc__, "parameters": {"type": "object", "properties": {}}},
        {"name": "submit", "description": Env.submit.__doc__, "parameters": {"type": "object", "properties": {}}},
        {"name": "undo", "description": Env.undo.__doc__, "parameters": {"type": "object", "properties": {}}},
    ]
