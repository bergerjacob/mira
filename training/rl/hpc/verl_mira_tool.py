"""SKETCH: multi-turn agentic GRPO in verl with the MIRA harness tools (build / view / run_spec / submit) as a verl tool.

Not runnable here (no verl/sglang). Mirrors the shape of verl's tool interface (verl.tools.base_tool.BaseTool: async create /
execute / calc_reward / release; check the signature of the installed verl version, it has changed between releases). Each rollout
instance owns one harness Episode-equivalent state: an env.tools.Env for the task plus the builder compile path from harness/agent.py,
so tool results are byte-identical to the benchmark harness and the final reward is the same shaped reward as training/rl/reward.py.

The cleaner long-term alternative (recommended in docs/TRAINING.md): keep harness/agent.py as THE rollout loop and serve the policy
behind an OpenAI-compatible endpoint (vLLM/SGLang), i.e. run `run_episode(cfg, OpenAICompatLLM(endpoint))` in rollout workers and
feed (prompt_ids, completion_ids, reward) back to the trainer, exactly like training/rl/grpo.py --mode multi does locally.
"""
import json
from typing import Any, Dict, Tuple


class MiraEpisodeState:
    """Per-rollout state: the harness Episode without its LLM loop."""

    def __init__(self, task_id: str, rounds: int, seed: int = 0):
        from harness.agent import Episode, RunConfig
        from harness.llm import ScriptedLLM
        self.ep = Episode(RunConfig(task=task_id, model="policy", arm="builder", rounds=rounds, seed=seed), ScriptedLLM([]))

    def call(self, name: str, args: Dict[str, Any]) -> str:
        self.ep.turn += 1
        return self.ep._clip(self.ep._exec(name, args))

    def final_reward(self) -> Tuple[float, dict]:
        from training.rl.reward import shape
        if not self.ep.env.done:
            self.ep._final_submit()
        sc = self.ep.env.final
        fin = {"r0": sc.r0, "r1": sc.r1, "r2": sc.r2, "r3": sc.r3, "r4": sc.r4, "full_pass": sc.full_pass}
        loaded = any(a.get("ok") for a in self.ep.build_attempts) and sc.blocks > 0
        return shape(fin, loaded), fin


try:  # pragma: no cover - only on the HPC image
    from verl.tools.base_tool import BaseTool  # type: ignore
except Exception:  # noqa: BLE001
    BaseTool = object


class MiraTool(BaseTool):  # pragma: no cover - sketch
    """One verl tool per harness function name (configure four instances: build, view, run_spec, submit) sharing `_STATES`."""
    _STATES: Dict[str, MiraEpisodeState] = {}

    def __init__(self, config: dict, tool_schema=None):
        if BaseTool is not object:
            super().__init__(config, tool_schema)
        self.name = config["name"]

    async def create(self, instance_id: str, task_id: str = None, rounds: int = 3, **kw):
        if instance_id not in self._STATES:
            self._STATES[instance_id] = MiraEpisodeState(task_id, rounds, kw.get("seed", 0))
        return instance_id

    async def execute(self, instance_id: str, parameters: dict, **kw):
        st = self._STATES[instance_id]
        text = st.call(self.name, parameters if isinstance(parameters, dict) else json.loads(parameters))
        step_reward = 0.0                          # trajectory-level reward only (computed in calc_reward)
        return text, step_reward, {"turn": st.ep.turn}

    async def calc_reward(self, instance_id: str, **kw) -> float:
        return self._STATES[instance_id].final_reward()[0]

    async def release(self, instance_id: str, **kw):
        self._STATES.pop(instance_id, None)
