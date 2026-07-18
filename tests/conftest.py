"""
Root conftest for MIRA test suite.
Handles sys.path setup (project convention: no __init__.py, manual sys.path).
Provides shared fixtures available to all test levels.
"""
import sys
import os
import json
import re
import time
from pathlib import Path

# Add project root to sys.path — same convention as every other script in the repo
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_root():
    """The absolute path to the project root."""
    return PROJECT_ROOT


@pytest.fixture
def fixtures_dir():
    """Path to the test fixtures directory."""
    return PROJECT_ROOT / "tests" / "fixtures"


@pytest.fixture
def circuits_dir():
    """Path to the circuit fixtures directory."""
    return PROJECT_ROOT / "tests" / "fixtures" / "circuits"


@pytest.fixture
def load_circuit():
    """
    Load a circuit fixture JSON by name.
    Usage: fixture = load_circuit("lever_lamp")
    """
    def _load(name):
        path = PROJECT_ROOT / "tests" / "fixtures" / "circuits" / f"{name}.json"
        with open(path) as f:
            return json.load(f)
    return _load


# ---------------------------------------------------------------------------
# Block state utilities
# ---------------------------------------------------------------------------

def parse_block_state(state_str):
    """
    Parse a block state string into (block_id, properties_dict).
    Example: "minecraft:repeater[delay=2,facing=west]" -> ("minecraft:repeater", {"delay": "2", "facing": "west"})
    """
    if "[" not in state_str:
        return state_str, {}

    base_id, props_str = state_str.split("[", 1)
    props_str = props_str.rstrip("]")

    props = {}
    for pair in props_str.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            props[k.strip()] = v.strip()

    return base_id, props


def format_block_state(block_id, props=None):
    """
    Format a block state string from components.
    Example: ("minecraft:repeater", {"delay": "2", "facing": "west"}) -> "minecraft:repeater[delay=2,facing=west]"
    """
    if not props:
        return block_id
    props_str = ",".join(f"{k}={v}" for k, v in sorted(props.items()))
    return f"{block_id}[{props_str}]"


def states_match(actual, expected):
    """
    Check if actual block state matches expected.
    Expected can be partial — only checks properties that are specified.
    Example: states_match("minecraft:repeater[delay=2,facing=west,locked=false]", "minecraft:repeater[delay=2]")
    -> True (only checks delay=2)
    """
    actual_id, actual_props = parse_block_state(actual)
    expected_id, expected_props = parse_block_state(expected)

    if actual_id != expected_id:
        return False

    for key, val in expected_props.items():
        if actual_props.get(key) != val:
            return False

    return True


@pytest.fixture
def parse_state():
    """Fixture exposing parse_block_state."""
    return parse_block_state


@pytest.fixture
def format_state():
    """Fixture exposing format_block_state."""
    return format_block_state


@pytest.fixture
def match_state():
    """Fixture exposing states_match."""
    return states_match


# ---------------------------------------------------------------------------
# Schematic generation fixture (for parser unit tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def make_litematic(tmp_path):
    """
    Create a small .litematic file programmatically for parser tests.
    Returns a function that takes blocks and returns the file path.

    Args to the returned function:
        blocks: list of (x, y, z, state_str) tuples
        name: schematic name (default "test_schem")

    Returns:
        Path to the generated .litematic file
    """
    from litemapy import Schematic, Region, BlockState

    def _make(blocks, name="test_schem"):
        if not blocks:
            max_x, max_y, max_z = 1, 1, 1
        else:
            max_x = max(b[0] for b in blocks) + 1
            max_y = max(b[1] for b in blocks) + 1
            max_z = max(b[2] for b in blocks) + 1

        reg = Region(0, 0, 0, max_x, max_y, max_z)
        schem = Schematic(name=name, author="MIRA-Test", regions={"Main": reg})

        for x, y, z, state_str in blocks:
            bid, props = parse_block_state(state_str)
            try:
                reg[x, y, z] = BlockState(bid, **props)
            except Exception as e:
                raise ValueError(f"Failed to create BlockState for '{state_str}': {e}")

        path = tmp_path / f"{name}.litematic"
        schem.save(str(path))
        return str(path)

    return _make
