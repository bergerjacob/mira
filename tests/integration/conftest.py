"""
Integration test conftest — fixtures for tests that need a running Minecraft server.
Provides:
  - Server availability check (auto-skip if server is down)
  - Session-scoped bridge connection (connect once, reuse across tests)
  - Per-test coordinate isolation (unique Y-level per test, cleanup on teardown)
  - VerificationContext (the glue layer wrapping bridge + Scarpet API)
  - Circuit builder helper (builds a circuit from fixture JSON)
"""
import sys
import os
import time
import json
import pytest
from pathlib import Path

# Ensure project root is on path (root conftest also does this, but be safe)
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks


# ---------------------------------------------------------------------------
# Coordinate allocation for test isolation
# ---------------------------------------------------------------------------

# Each test gets a unique region on a 2D grid (X/Z spread, fixed Y=200).
# This prevents tests from interfering with each other's placed blocks
# while staying within the world height limit (-64 to 320 in MC 1.21).
#
# Grid: 20 columns × N rows, each cell 64 blocks apart.
# For 217 server tests, this uses rows 0-10 (Z range 0-640), well within
# loaded chunk range. Regions are 50 blocks wide in cleanup, so 64-block
# spacing prevents overlap.
_grid_counter = 0
_GRID_COLUMNS = 20
_GRID_SPACING = 64
_GRID_Y = 200


def _next_origin():
    global _grid_counter
    col = _grid_counter % _GRID_COLUMNS
    row = _grid_counter // _GRID_COLUMNS
    _grid_counter += 1
    return (col * _GRID_SPACING, _GRID_Y, row * _GRID_SPACING)


def _reset_y_counter():
    """Reset the grid counter (useful for test isolation within a session)."""
    global _grid_counter
    _grid_counter = 0


# ---------------------------------------------------------------------------
# Server fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def server_available():
    """
    Check if the Minecraft server is running and accessible via RCON.
    Tests marked @pytest.mark.server will be skipped if this returns False.
    """
    try:
        bridge = MinecraftBridge()
        bridge.connect()
        resp = bridge.run_command("list")
        bridge.disconnect()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def session_bridge(server_available):
    """
    Session-scoped bridge connection.
    Connects once at the start of the test session, disconnects at the end.
    Also loads the mira_api Scarpet script.
    """
    if not server_available:
        pytest.skip("Minecraft server not available (RCON localhost:25575)")

    bridge = MinecraftBridge()
    bridge.connect()

    # Load the Scarpet verification API
    try:
        bridge.run_command("script load mira_api")
    except Exception as e:
        print(f"Warning: could not load mira_api: {e}")

    yield bridge

    bridge.disconnect()


@pytest.fixture
def bridge(session_bridge):
    """
    Per-test bridge (reuses the session connection).
    Tests that need the server should depend on this fixture.
    """
    return session_bridge


# ---------------------------------------------------------------------------
# Coordinate isolation fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_region(bridge):
    """
    Allocate a unique region on the 2D grid for this test.
    Returns the origin (x, y, z) tuple.
    Force-loads the chunk so blocks can be placed (headless server has no
    players to keep chunks loaded).
    Automatically cleans up (fills with air, removes forceload) after the test.
    """
    origin = _next_origin()
    ox, oy, oz = origin

    # Force-load chunks covering the test region (3x3 chunk area = 48 blocks)
    bridge.run_command(f"forceload add {ox} {oz} {ox + 48} {oz + 48}")

    # Ensure consistent game state: unfrozen + fillUpdates enabled.
    # Previous tests (especially replicate_blocks) may leave the game frozen
    # or fillUpdates disabled, which breaks redstone signal propagation.
    # Also resume Scarpet in case it paused itself (happens after many operations).
    bridge.run_command("script resume")
    bridge.run_command("tick unfreeze")
    bridge.run_command("carpet fillUpdates true")

    # Clear any residual blocks from previous test runs (when running stability
    # tests as a separate invocation, the grid counter resets but old blocks
    # may remain in the world from the previous run).
    # Disable fillUpdates during the clear so we don't trigger cascading updates
    # that can pollute neighboring test regions or leave pending update queues.
    # NOTE: Minecraft's fill command has a 32,768 block limit. The area below
    # is 31x16x31 = 15,376 blocks, well within the limit. The previous 55x25x55
    # = 75,625 block fill was silently failing, leaving residual blocks that
    # caused false test failures (e.g. redstone wire in a "gap" position).
    bridge.run_command("carpet fillUpdates false")
    bridge.fill(ox - 5, oy - 5, oz - 5, ox + 25, oy + 10, oz + 25, "air")
    bridge.run_command("carpet fillUpdates true")

    # Process any pending updates from previous tests so they don't interfere.
    # Without this, accumulated update queues can cause blocks to turn on/off
    # unexpectedly in later tests (especially negative tests that expect lamps
    # to stay off).
    bridge.run_command("tick freeze")
    bridge.run_command("tick step 5")
    bridge.run_command("tick unfreeze")
    time.sleep(0.2)

    yield origin

    # Cleanup: fill the region with air, kill entities, remove forceload
    try:
        bridge.run_command("carpet fillUpdates false")
        bridge.fill(ox - 5, oy - 5, oz - 5, ox + 25, oy + 10, oz + 25, "air")
        bridge.run_command("carpet fillUpdates true")
        bridge.run_command(
            f"kill @e[x={ox - 5},y={oy - 5},z={oz - 5},dx=35,dy=20,dz=35,type=!player]"
        )
        bridge.run_command(f"forceload remove {ox} {oz} {ox + 48} {oz + 48}")
    except Exception as e:
        print(f"Warning: test_region cleanup failed: {e}")


@pytest.fixture
def origin(test_region):
    """Alias for test_region — returns just the origin tuple."""
    return test_region


# ---------------------------------------------------------------------------
# VerificationContext — the glue layer
# ---------------------------------------------------------------------------

class VerificationContext:
    """
    Glue layer wrapping bridge + Scarpet API to execute verification contracts.
    This connects teacher_client's verify_circuit(ctx) contracts to the actual server.

    All positions are RELATIVE to the origin. The context converts them to absolute
    coordinates internally.
    """

    def __init__(self, bridge, origin=(0, 100, 0)):
        self.bridge = bridge
        self.origin = origin
        self._ox, self._oy, self._oz = origin
        self._set_positions = []  # track positions for update_region in tick()

    def _abs(self, pos):
        """Convert relative (x, y, z) to absolute coordinates."""
        return (self._ox + pos[0], self._oy + pos[1], self._oz + pos[2])

    def set_block(self, pos, block_state):
        """Place a block at a relative position."""
        x, y, z = self._abs(pos)
        self.bridge.set_block(x, y, z, block_state)
        self._set_positions.append((x, y, z))

    def tick(self, ticks=20):
        """Advance the game simulation by N ticks.

        Uses freeze→step→unfreeze pattern because 'tick step' only works
        when the game is frozen, and 'tick warp' is not a valid command
        in this Carpet version.

        Before stepping, emits proper redstone updates (NC + PP) on all
        positions set via set_block(). This is needed because setblock
        only emits priority 3 updates, which redstone components (repeaters,
        comparators, pistons, torches, dust) do NOT respond to. We use
        TIS Addition's `manipulate block emit block_update` which emits
        priority 0 NC updates that redstone components respond to.

        We use redstone_update (individual positions) NOT update_region
        (scan). The latter causes false positives on negative tests (broken
        circuits get lamps to light incorrectly) and can crash/hang the server.
        """
        for pos in self._set_positions:
            self.bridge.run_command(f"mira_api redstone_update {pos[0]} {pos[1]} {pos[2]}")

        self.bridge.run_command("tick freeze")
        self.bridge.run_command(f"tick step {ticks}")
        self.bridge.run_command("tick unfreeze")
        time.sleep(0.3)
        self._set_positions.clear()  # Reset for next tick cycle

    def toggle_lever(self, pos):
        """Simulate a player flipping a lever at a relative position.
        
        Uses mira_api toggle_lever which reads the current powered state,
        flips it, and emits proper redstone updates (NC + PP at priority 0/2).
        This is the reliable way to toggle levers — setblock alone only
        emits priority 3 updates which redstone components don't respond to.
        """
        x, y, z = self._abs(pos)
        self.bridge.run_command(f"mira_api toggle_lever {x} {y} {z}")
        # Step a few ticks to let the signal propagate
        self.bridge.run_command("tick freeze")
        self.bridge.run_command("tick step 5")
        self.bridge.run_command("tick unfreeze")
        time.sleep(0.3)

    def redstone_update(self, pos):
        """Force proper redstone updates (NC + PP) at a relative position.
        
        Uses TIS Addition's manipulate block emit block_update/state_update.
        Call this after setblock if you need redstone components to respond
        to the block change.
        """
        x, y, z = self._abs(pos)
        self.bridge.run_command(f"mira_api redstone_update {x} {y} {z}")

    def assert_block(self, pos, expected):
        """
        Assert that a block matches the expected state.
        Raises AssertionError on mismatch.
        Expected can be partial (e.g. "minecraft:lever[powered=true]" only checks powered).
        """
        x, y, z = self._abs(pos)
        resp = self.bridge.run_command(f"mira_api check_block {x} {y} {z} {expected}")
        if "PASS" not in resp:
            raise AssertionError(
                f"Block check FAILED at rel {pos} (abs {x},{y},{z}): "
                f"expected '{expected}', response: {resp}"
            )

    def assert_power(self, pos, min_level):
        """
        Assert that redstone power at a position is at least min_level.
        Checks the 'power' property of redstone_wire at that position.
        """
        x, y, z = self._abs(pos)
        resp = self.bridge.run_command(f"mira_api get_state {x} {y} {z}")
        # get_state returns properties in format {power: 15, east: none, ...}
        # Check for both "power=" and "power:" formats
        if "power=" in resp or "power:" in resp:
            try:
                # Try "power=N" format first, then "power: N"
                if "power=" in resp:
                    power_str = resp.split("power=")[1].split(",")[0].split("]")[0].strip()
                else:
                    power_str = resp.split("power:")[1].split(",")[0].split("}")[0].strip()
                power = int(power_str)
                if power < min_level:
                    raise AssertionError(
                        f"Power check FAILED at rel {pos}: expected >={min_level}, got {power}"
                    )
            except (ValueError, IndexError):
                raise AssertionError(f"Could not parse power from state: {resp}")
        else:
            raise AssertionError(f"No power property at rel {pos}: {resp}")

    def get_state(self, pos):
        """Get the block state string at a relative position."""
        x, y, z = self._abs(pos)
        return self.bridge.run_command(f"mira_api get_state {x} {y} {z}")

    def check_block(self, pos, expected):
        """Check a block without raising. Returns True/False."""
        x, y, z = self._abs(pos)
        resp = self.bridge.run_command(f"mira_api check_block {x} {y} {z} {expected}")
        return "PASS" in resp

    def check_inv(self, pos, slot, count, item):
        """Check inventory at a relative position. Returns True/False."""
        x, y, z = self._abs(pos)
        resp = self.bridge.run_command(f"mira_api check_inv {x} {y} {z} {slot} {count} {item}")
        return "PASS" in resp

    def check_entity(self, pos, entity_type):
        """Check for entity at a relative position. Returns True/False."""
        x, y, z = self._abs(pos)
        resp = self.bridge.run_command(f"mira_api check_entity {x} {y} {z} {entity_type}")
        return "PASS" in resp

    def update_block(self, pos):
        """Force a block update at a relative position."""
        x, y, z = self._abs(pos)
        self.bridge.run_command(f"mira_api update_block {x} {y} {z}")

    def update_region(self, pos1, pos2):
        """Force block updates in a region (relative coordinates)."""
        x1, y1, z1 = self._abs(pos1)
        x2, y2, z2 = self._abs(pos2)
        self.bridge.run_command(f"mira_api update_region {x1} {y1} {z1} {x2} {y2} {z2}")


@pytest.fixture
def ctx(bridge, test_region):
    """
    Per-test VerificationContext.
    Provides set_block, tick, assert_block, assert_power, check_block, etc.
    All positions are relative to the test's allocated origin.
    """
    return VerificationContext(bridge, test_region)


# ---------------------------------------------------------------------------
# Circuit builder fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def build_circuit(bridge):
    """
    Returns a function that builds a circuit from a fixture JSON.
    The function takes (fixture_dict, origin) and builds all blocks + support blocks.

    Usage:
        fixture = load_circuit("lever_lamp")
        build_circuit(fixture, origin)
    """
    def _build(fixture, origin):
        blocks_data = fixture.get("blocks", [])
        support_data = fixture.get("support_blocks", [])

        # Convert to (x, y, z, state, nbt) tuples
        all_blocks = []
        for b in blocks_data + support_data:
            all_blocks.append((b["x"], b["y"], b["z"], b["state"], None))

        if not all_blocks:
            return

        # Calculate bounds
        min_x = min(b[0] for b in all_blocks)
        min_y = min(b[1] for b in all_blocks)
        min_z = min(b[2] for b in all_blocks)
        max_x = max(b[0] for b in all_blocks)
        max_y = max(b[1] for b in all_blocks)
        max_z = max(b[2] for b in all_blocks)

        bounds = ((min_x, min_y, min_z), (max_x + 1, max_y + 1, max_z + 1))

        # Build with updates disabled, then force update
        replicate_blocks(
            all_blocks, origin, bounds, bridge,
            use_updates=False, force_update_region=True
        )

        # Let physics settle — freeze→step→unfreeze (tick step requires frozen game)
        bridge.run_command("tick freeze")
        bridge.run_command("tick step 20")
        bridge.run_command("tick unfreeze")
        time.sleep(0.5)

    return _build


@pytest.fixture
def build_and_verify(bridge, build_circuit):
    """
    Returns a function that builds a circuit from a fixture JSON and returns
    a VerificationContext for it.

    Usage:
        fixture = load_circuit("lever_lamp")
        ctx = build_and_verify(fixture, origin)
        ctx.assert_block([2, 0, 0], "minecraft:redstone_lamp[lit=false]")
    """
    def _build_and_verify(fixture, origin):
        build_circuit(fixture, origin)
        return VerificationContext(bridge, origin)

    return _build_and_verify


# ---------------------------------------------------------------------------
# Auto-skip marker for server tests
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.server tests when the server is unavailable."""
    server_marker = pytest.mark.server
    skip_server = pytest.mark.skip(reason="Minecraft server not available (RCON localhost:25575)")

    # Check server availability once
    server_up = False
    try:
        bridge = MinecraftBridge()
        bridge.connect()
        bridge.run_command("list")
        bridge.disconnect()
        server_up = True
    except Exception:
        pass

    if not server_up:
        for item in items:
            if "server" in item.keywords:
                item.add_marker(skip_server)
