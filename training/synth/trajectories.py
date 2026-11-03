"""Scripted (LLM-free) builder-arm episodes in the harness chat format, with REAL tool outputs.

The episode runs through `harness.agent.Episode` itself (same system prompt, task prompt, tool list, footers, clipping and
scoring as an LLM run); only the assistant side is scripted: a reactive `ScriptedLLM` callable reads the last tool result
and emits the next message. Assistant text is templated, assistant programs are the reference program or a bugged copy
(training/synth/bugs.py); every tool message is the environment's own output.

Kinds:
  oneshot      R=0: [view] -> build(reference) -> submit
  verify       R=3: build(reference) -> run_spec (passes) -> submit
  debug        R=3: build(bugged) -> run_spec (fails) -> build(reference) -> run_spec (passes) -> submit
  debug_view   as debug, with a view() of the bug's layer before the fix
  typo         R=3: build(program with a misspelled call) -> compile error -> build(reference) -> run_spec -> submit
  repair       R=3, task text contains the broken program: build(broken) -> run_spec (fails) -> [view] -> build(fix) -> run_spec -> submit

An episode is exported only if the final submit is a full pass (and, for debug/repair, the first run_spec really failed).
"""
import json
import random
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from harness.agent import Episode, RunConfig  # noqa: E402
from harness.llm import ScriptedLLM, assistant  # noqa: E402

KINDS = ("oneshot", "verify", "debug", "debug_view", "typo", "repair")
ROUNDS = {"oneshot": 0}


def _fail_summary(text):
    """First failing public vector / sequence line + the R3 line of a run_spec result (for the templated diagnosis)."""
    r3 = next((ln.strip() for ln in text.splitlines() if ln.startswith("R3 ")), "")
    fail = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("FAIL ")), "")
    miss = next((ln.strip()[2:] for ln in text.splitlines() if ln.strip().startswith("- ") and "no circuit path" in ln), "")
    r1 = next((ln.strip()[2:] for ln in text.splitlines() if ln.strip().startswith("- ") and ("paste_stable" in ln or "floating" in ln)), "")
    return r3, fail, miss, r1


def _short(s, n=220):
    return s if len(s) <= n else s[:n] + " ..."


class Player:
    """Reactive script: a queue of callables last_tool_text -> assistant message (a callable may append more steps).
    The live message list of the episode is kept in `messages`."""

    def __init__(self, steps):
        self.queue = list(steps)
        self.messages = None
        self.n = 0

    def __call__(self, messages, tools, n_calls):
        self.messages = messages
        last = messages[-1]["content"] if messages and messages[-1]["role"] == "tool" else ""
        step = self.queue.pop(0) if self.queue else (lambda _l: assistant("Submitting.", [("submit", {})]))
        self.n += 1
        msg = step(last)
        for i, c in enumerate(msg.get("tool_calls") or []):
            c["id"] = f"call_{self.n}_{i}"
        return msg


def _plan_text(info):
    nl = info.get("netlist")
    if nl:
        gates = "; ".join(f"{n} = {k}({', '.join(i)})" for n, k, i in nl["gates"])
        head = f"Netlist: {gates}; outputs " + ", ".join(f"{o} = {s}" for o, s in nl["outputs"].items()) + ". "
        if info.get("router"):
            return head + ("Plan: the gate cells in a row north of the levers, then one b.route(source, [sinks]) call per net: the 3D "
                           "router finds dust paths, bridges and repeaters around everything placed before.")
        return (head + "Plan: one bus per signal running east at y=1 (levers at the west end, lamps at the east end), the gate cells in a row "
                "north of the buses, every gate input branches off its bus through a repeater and bridges over the buses in between at y=3.")
    return info.get("plan") or "Plan: the block layout of a working design for this task."


def build_msg(text, program):
    return assistant(text, [("build", {"source": program})])


def _say(text, name, args=None):
    return lambda _last: assistant(text, [(name, args or {})])


CHECK = _say("Loaded. Checking it against the public vectors.", "run_spec")
RECHECK = _say("Re-checking after the fix.", "run_spec")
SUBMIT = _say("All public checks pass. Submitting.", "submit")


def episode(task, kind, reference, info, bug=None, view_layer=1, seed=0, max_turns=None):
    """Run one scripted episode. -> (record or None, final dict). `bug` = {"text", "kind", "why", "fix"} for debug/repair
    kinds; for `typo` bug = {"text", "name"}."""
    rounds = ROUNDS.get(kind, 3)
    rng = random.Random(seed)
    seen = {}
    plan = _plan_text(info) + " Writing the program."
    if kind == "oneshot":
        steps = ([_say("Looking at the fixed devices on the logic layer before writing the program.", "view", {"layer": view_layer})]
                 if view_layer is not None and rng.random() < 0.5 else [])
        steps += [lambda _l: build_msg(plan, reference), _say("The program compiled and loaded. Submitting.", "submit")]
    elif kind == "verify":
        steps = [lambda _l: build_msg(plan, reference), CHECK, SUBMIT]
    elif kind == "typo":
        steps = [lambda _l: build_msg(plan, bug["text"]),
                 lambda _l: build_msg(f"The compile failed: `{bug['name']}` is not a builder function (NameError). Fixing the name.", reference),
                 CHECK, SUBMIT]
    else:
        def diagnose(last):
            seen["first"] = last
            r3, fail, miss, r1 = _fail_summary(last)
            obs = "; ".join(x for x in (r3, _short(fail), miss, r1) if x)
            if view_layer is not None and (kind == "debug_view" or (kind == "repair" and rng.random() < 0.5)):
                player.queue[:0] = [lambda _l: build_msg(f"In the slice: {bug['why']}. Fix: {bug['fix']}.", reference)]
                return assistant(f"run_spec: {obs}. Viewing layer {view_layer} to find the faulty connection.", [("view", {"layer": view_layer})])
            return build_msg(f"run_spec: {obs}. Cause: {bug['why']}. Fix: {bug['fix']}.", reference)

        intro = plan if kind != "repair" else "Loading the given program as it is, to see how it fails."
        steps = [lambda _l: build_msg(intro, bug["text"]), CHECK, diagnose, RECHECK, _say("All public checks pass now. Submitting.", "submit")]
    player = Player(steps)
    llm = ScriptedLLM(player, model="synthetic")
    cfg = RunConfig(task=task.id, model="synthetic", arm="builder", rounds=rounds, seed=seed, max_turns=max_turns)
    ep = Episode(cfg, llm, task=task)
    final = ep.run()
    msgs = player.messages or []
    ok = bool(final.get("full_pass")) and final.get("ended_by") == "submit"
    if kind in ("debug", "debug_view", "repair"):
        ok = ok and "R3 BEHAVIOUR" in seen.get("first", "") and not _passes(seen["first"])
    if kind == "typo":
        ok = ok and any(m["role"] == "tool" and m["content"].startswith("COMPILE FAILED") for m in msgs)
    if not ok:
        return None, final
    return {"messages": msgs, "tools": ep.tools, "spans": span_sources(msgs, reference)}, final


def _passes(text):
    m = re.search(r"R3 BEHAVIOUR \(public vectors\): (\d+)/(\d+) pass", text)
    return bool(m) and m.group(1) == m.group(2) and "R1 STRUCTURE: ok" in text


def span_sources(messages, reference):
    """Where each message comes from: system/user = harness prompts; assistant content = template; build source = reference /
    bugged program; tool = environment output (real)."""
    out = []
    for i, m in enumerate(messages):
        if m["role"] in ("system", "user"):
            out.append({"i": i, "role": m["role"], "source": "harness_prompt"})
        elif m["role"] == "tool":
            out.append({"i": i, "role": "tool", "source": "env_real"})
        else:
            src = []
            for c in m.get("tool_calls") or []:
                if c["function"]["name"] == "build":
                    prog = json.loads(c["function"]["arguments"])["source"]
                    src.append("reference_program" if prog == reference else "bugged_program")
                else:
                    src.append("tool_call_template")
            out.append({"i": i, "role": "assistant", "source": "template", "calls": src})
    return out


def record(task, kind, ep, final, synth, family):
    """JSONL row: {messages, tools, meta}."""
    return {"messages": ep["messages"], "tools": ep["tools"],
            "meta": {"task": task.id, "source": synth.get("source", f"synth/{family}"), "family": family, "kind": kind,
                     "difficulty": synth.get("difficulty", "?"), "n_blocks": len(task.solution), "verified": True,
                     "reward": final["reward"], "rounds": final["R"], "rounds_used": final["rounds_used"], "turns": final["turns"],
                     "arm": "builder", "synthetic_spans": ep["spans"], "base_task": synth.get("base"), "bug": synth.get("bug_kind")}}
