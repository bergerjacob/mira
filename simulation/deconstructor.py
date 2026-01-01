from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from simulation.teacher_client import TeacherClient


BlockRecord = Tuple[int, int, int, str, Any]


@dataclass
class DeconstructionStep:
    step_index: int
    reasoning: str
    removed_blocks: List[Dict[str, Any]]
    remaining_count: int
    prompt: Dict[str, str]
    snapshot_after: List[Dict[str, Any]] = field(default_factory=list)


class ReverseDeconstructor:
    """
    Iteratively calls the TeacherClient to obtain reverse-construction steps.
    """

    def __init__(self, teacher: TeacherClient):
        self.teacher = teacher

    def plan(self, blocks: List[BlockRecord]) -> List[Dict[str, Any]]:
        remaining: Dict[Tuple[int, int, int], Dict[str, Any]] = {
            (x, y, z): {"state": state, "nbt": nbt}
            for x, y, z, state, nbt in blocks
        }

        steps: List[Dict[str, Any]] = []
        max_iters = len(remaining) + 5
        iteration = 0

        while remaining and iteration < max_iters:
            current_blocks = [
                (x, y, z, data["state"], data["nbt"])
                for (x, y, z), data in remaining.items()
            ]

            payload = self.teacher.suggest_deconstruction_layer(current_blocks, iteration)
            response = payload["response"]
            suggested = [
                tuple(block)
                for block in response.get("remove_blocks", [])
            ]

            valid_positions = [pos for pos in suggested if pos in remaining]

            reasoning = response.get("reasoning", "")

            if not valid_positions:
                # Fallback removal: highest Y block.
                fallback = max(remaining.keys(), key=lambda p: (p[1], p[0], p[2]))
                valid_positions = [fallback]
                reasoning = f"{reasoning} | Fallback applied to maintain progress."

            removed_blocks = []
            for pos in valid_positions:
                data = remaining.pop(pos)
                removed_blocks.append({
                    "pos": list(pos),
                    "state": data["state"],
                    "nbt": data["nbt"],
                })

            snapshot_after = self._serialize_snapshot(remaining)

            steps.append({
                "step": iteration,
                "reasoning": reasoning.strip(),
                "removed_blocks": removed_blocks,
                "remaining_count": len(remaining),
                "prompt": payload["prompt"],
                "snapshot_after": snapshot_after,
            })

            iteration += 1

        if remaining:
            raise RuntimeError("Deconstruction did not converge within iteration budget.")

        return steps

    def _serialize_snapshot(self, remaining: Dict[Tuple[int, int, int], Dict[str, Any]]) -> List[Dict[str, Any]]:
        serialized = []
        for (x, y, z), data in sorted(remaining.items(), key=lambda item: (item[0][1], item[0][0], item[0][2])):
            serialized.append({
                "pos": [x, y, z],
                "state": data["state"],
            })
        return serialized


