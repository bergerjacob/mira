"""
MIRA: Procedural Circuit Generator Framework

Base classes for generating correct-by-construction redstone circuits
for training data. Each generator produces CircuitExample instances with
a natural-language description, block list, and verification contract.

Key constraint: circuits must be verifiable via setblock (no repeaters,
comparators, or pistons that need to change state via redstone updates).
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any
import itertools
import random


# Type aliases
Block = Tuple[int, int, int, str, Optional[str]]  # (x, y, z, state, nbt)


@dataclass
class VerifyCheck:
    """A single verification check: assert block at position matches expected state."""
    position: Tuple[int, int, int]
    expected_state: str  # e.g. "minecraft:redstone_lamp[lit=true]"


@dataclass
class VerifyAction:
    """A verification action: set a block to a new state."""
    position: Tuple[int, int, int]
    state: str  # e.g. "minecraft:lever[facing=east,powered=true]"


@dataclass
class VerifyContract:
    """Machine-executable verification spec for a circuit."""
    initial_checks: List[VerifyCheck] = field(default_factory=list)
    actions: List[VerifyAction] = field(default_factory=list)
    tick_steps: int = 20
    final_checks: List[VerifyCheck] = field(default_factory=list)
    # Optional: negative checks (unpower circuit, verify output changes)
    negative_checks: List[VerifyCheck] = field(default_factory=list)


@dataclass
class CircuitExample:
    """A single generated circuit example for training data."""
    description: str  # Natural-language description (training input)
    blocks: List[Block]  # (x, y, z, state, nbt) tuples
    support_blocks: List[Block]  # Stone blocks underneath redstone components
    verify_contract: VerifyContract
    category: str  # e.g. "logic_gate", "signal_routing"
    difficulty: str  # "beginner", "intermediate", "advanced"
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Training-format fields (filled by format_training.py)
    plan: Optional[List[Dict]] = None
    reasoning: Optional[str] = None

    @property
    def block_count(self) -> int:
        return len(self.blocks)


class CircuitGenerator(ABC):
    """Base class for procedural circuit generators."""

    name: str = "base"
    category: str = "uncategorized"

    @abstractmethod
    def enumerate_params(self) -> List[Dict[str, Any]]:
        """Return all parameter combinations for this generator."""
        ...

    @abstractmethod
    def generate(self, params: Dict[str, Any]) -> CircuitExample:
        """Generate a single circuit example from params."""
        ...

    def generate_all(self) -> List[CircuitExample]:
        """Generate all examples from all parameter combinations."""
        examples = []
        for params in self.enumerate_params():
            try:
                example = self.generate(params)
                examples.append(example)
            except Exception as e:
                print(f"  WARN: {self.name} failed for params {params}: {e}")
        return examples


def add_support_blocks(blocks: List[Block], y_offset: int = -1) -> List[Block]:
    """Add stone support blocks underneath redstone components that need them."""
    needs_support = {"redstone_wire", "lever", "repeater", "comparator",
                     "button", "pressure_plate", "redstone_torch"}
    support_blocks = []
    existing_positions = {(b[0], b[1], b[2]) for b in blocks}
    for x, y, z, state, nbt in blocks:
        block_name = state.split("[")[0].split(":")[-1]
        if block_name in needs_support:
            support_pos = (x, y + y_offset, z)
            if support_pos not in existing_positions:
                support_blocks.append((support_pos[0], support_pos[1], support_pos[2],
                                       "minecraft:stone", None))
                existing_positions.add(support_pos)
    return support_blocks


def make_plan(blocks: List[Block], reasons: Optional[List[str]] = None,
             connects: Optional[List[List[int]]] = None) -> List[Dict]:
    """Convert block list to plan format for training data."""
    plan = []
    for i, (x, y, z, state, nbt) in enumerate(blocks):
        step = {
            "step": i + 1,
            "block_type": state.split("[")[0],
            "position": f"{x},{y},{z}",
            "reason": reasons[i] if reasons and i < len(reasons) else "Place block",
            "connects_to": connects[i] if connects and i < len(connects) else [],
        }
        plan.append(step)
    return plan


def format_state(block_name: str, **properties) -> str:
    """Format a block state string: minecraft:block[prop=val,...]."""
    if not properties:
        return f"minecraft:{block_name}"
    props = ",".join(f"{k}={v}" for k, v in properties.items())
    return f"minecraft:{block_name}[{props}]"
