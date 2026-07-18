"""
Integration tests: entity placement and verification.
Tests summoning entities and checking their presence via the Scarpet API.
"""
import time
import pytest

pytestmark = pytest.mark.server


def test_summon_armor_stand(ctx, bridge, origin):
    """Summon armor_stand, verify with check_entity -> PASS."""
    ox, oy, oz = origin
    # Place block underneath to prevent falling, then summon with NoGravity
    ctx.set_block((0, -1, 0), "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox} {oy} {oz} {{NoGravity:1b}}")
    ctx.tick()
    time.sleep(0.5)
    assert ctx.check_entity((0, 0, 0), "minecraft:armor_stand"), "Armor stand should be detected"


def test_summon_wrong_type_fails(ctx, bridge, origin):
    """Summon armor_stand, check for zombie -> FAIL."""
    ox, oy, oz = origin
    ctx.set_block((0, -1, 0), "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox} {oy} {oz} {{NoGravity:1b}}")
    ctx.tick()
    time.sleep(0.5)
    assert not ctx.check_entity((0, 0, 0), "minecraft:zombie"), "Zombie should not match armor stand"


def test_entity_at_empty_location(ctx, origin):
    """check_entity where no entity exists -> FAIL."""
    # No entity summoned at the origin
    assert not ctx.check_entity((0, 0, 0), "minecraft:armor_stand"), "No entity should be found"


def test_summon_multiple_entities(ctx, bridge, origin):
    """Summon 2 armor stands at different locations, verify both."""
    ox, oy, oz = origin
    ctx.set_block((0, -1, 0), "minecraft:stone")
    ctx.set_block((3, -1, 0), "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox} {oy} {oz} {{NoGravity:1b}}")
    bridge.run_command(f"summon minecraft:armor_stand {ox + 3} {oy} {oz} {{NoGravity:1b}}")
    ctx.tick()
    time.sleep(0.5)
    assert ctx.check_entity((0, 0, 0), "minecraft:armor_stand"), "First armor stand missing"
    assert ctx.check_entity((3, 0, 0), "minecraft:armor_stand"), "Second armor stand missing"


def test_kill_entity(ctx, bridge, origin):
    """Summon entity, kill it, verify check_entity -> FAIL."""
    ox, oy, oz = origin
    ctx.set_block((0, -1, 0), "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox} {oy} {oz} {{NoGravity:1b}}")
    ctx.tick()
    time.sleep(0.5)
    assert ctx.check_entity((0, 0, 0), "minecraft:armor_stand"), "Entity should exist before kill"

    # Kill the entity
    bridge.run_command(
        f"kill @e[x={ox},y={oy},z={oz},distance=..2,type=minecraft:armor_stand]"
    )
    ctx.tick()
    time.sleep(0.5)
    assert not ctx.check_entity((0, 0, 0), "minecraft:armor_stand"), "Entity should not exist after kill"


def test_entity_within_range(ctx, bridge, origin):
    """Summon entity 1 block away, check at origin -> PASS (distance=..2 range)."""
    ox, oy, oz = origin
    # Summon 1 block away from the check position
    ctx.set_block((1, -1, 0), "minecraft:stone")
    bridge.run_command(f"summon minecraft:armor_stand {ox + 1} {oy} {oz} {{NoGravity:1b}}")
    ctx.tick()
    time.sleep(0.5)
    # Check at origin — should pick up the entity within range
    assert ctx.check_entity((0, 0, 0), "minecraft:armor_stand"), (
        "Armor stand 1 block away should be detected"
    )
