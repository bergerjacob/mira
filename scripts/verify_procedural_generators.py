"""
MIRA: Procedural Generator Verification Script

Verifies that procedurally generated circuits actually work in Minecraft.
Builds each circuit, runs its verification contract, and records pass/fail.

Usage:
    python scripts/verify_procedural_generators.py [--category NAME] [--limit N]
"""
from __future__ import annotations
import sys
import os
import json
import time
from pathlib import Path
from datetime import datetime

# sys.path setup
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from training.generators import ALL_GENERATORS
from training.generators.base import CircuitExample, VerifyContract
from simulation.bridge import MinecraftBridge
from simulation.replicator import replicate_blocks


def build_and_verify(bridge: MinecraftBridge, example: CircuitExample,
                     origin: tuple, region_size: int = 32) -> dict:
    """Build a circuit in MC and run its verification contract."""
    ox, oy, oz = origin
    result = {
        "generator": example.metadata.get("generator", "unknown"),
        "params": example.metadata.get("params", {}),
        "description": example.description,
        "block_count": example.block_count,
        "category": example.category,
        "difficulty": example.difficulty,
        "origin": list(origin),
        "initial_checks": [],
        "actions": [],
        "final_checks": [],
        "negative_checks": [],
        "passed": False,
        "errors": [],
    }

    try:
        # Forceload the area
        bridge.run_command(f"forceload add {ox} {oz} {ox + region_size} {oz + region_size}")

        # Clear the area
        bridge.run_command(f"carpet fillUpdates false")
        bridge.fill(ox - 2, oy - 2, oz - 2,
                    ox + region_size, oy + 10, oz + region_size, "air")
        bridge.run_command(f"carpet fillUpdates true")

        # Combine blocks + support blocks
        all_blocks = example.blocks + example.support_blocks

        # Calculate bounds
        xs = [b[0] for b in all_blocks]
        ys = [b[1] for b in all_blocks]
        zs = [b[2] for b in all_blocks]
        bounds = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))

        # Build the circuit
        replicate_blocks(
            blocks=all_blocks,
            origin=origin,
            bounds=bounds,
            bridge=bridge,
            rate_limit=64,
            use_updates=True,
            force_update_region=False,
        )

        # Emit proper redstone updates (NC + PP) on each block using TIS Addition's
        # manipulate command. This is needed because setblock only emits priority 3
        # updates which redstone components (repeaters, comparators, torches, dust)
        # do NOT respond to. redstone_update emits priority 0 NC updates.
        for x, y, z, state, nbt in all_blocks:
            bridge.run_command(f"mira_api redstone_update {ox + x} {oy + y} {oz + z}")

        # Tick to settle
        bridge.run_command("tick freeze")
        bridge.run_command("tick step 5")
        bridge.run_command("tick unfreeze")
        time.sleep(0.3)

        # Run initial checks
        for check in example.verify_contract.initial_checks:
            cx, cy, cz = check.position
            abs_pos = (ox + cx, oy + cy, oz + cz)
            resp = bridge.run_command(
                f"mira_api check_block {abs_pos[0]} {abs_pos[1]} {abs_pos[2]} {check.expected_state}"
            )
            passed = "PASS" in resp
            result["initial_checks"].append({
                "position": list(check.position),
                "expected": check.expected_state,
                "passed": passed,
                "response": resp.strip()[:200],
            })
            if not passed:
                result["errors"].append(f"Initial check failed at {check.position}: expected {check.expected_state}")

        # Run actions
        for action in example.verify_contract.actions:
            ax, ay, az = action.position
            abs_pos = (ox + ax, oy + ay, oz + az)
            if action.action_type == "toggle_lever":
                # Toggle lever: change state, tick to propagate, emit redstone updates
                # This sequence is required because set() doesn't propagate redstone
                # signal immediately — the lever state needs a tick to propagate
                # before block_update will cause neighbors to re-evaluate.
                bridge.run_command(f"mira_api toggle_lever {abs_pos[0]} {abs_pos[1]} {abs_pos[2]}")
                # Tick to let lever state propagate internally
                bridge.run_command("tick freeze")
                bridge.run_command("tick step 5")
                bridge.run_command("tick unfreeze")
                # Emit redstone updates on ALL blocks in the circuit
                # (not just lever + 6 neighbors — the lamp may be far away
                # and needs its own redstone_update to re-evaluate)
                for bx, by, bz, bstate, bnbt in all_blocks:
                    bridge.run_command(
                        f"mira_api redstone_update {ox + bx} {oy + by} {oz + bz}"
                    )
                result["actions"].append({
                    "position": list(action.position),
                    "action_type": "toggle_lever",
                })
            else:
                # set_block action
                bridge.run_command(
                    f"setblock {abs_pos[0]} {abs_pos[1]} {abs_pos[2]} {action.state}"
                )
                # Tick to let state propagate, then emit redstone updates on ALL blocks
                bridge.run_command("tick freeze")
                bridge.run_command("tick step 5")
                bridge.run_command("tick unfreeze")
                for bx, by, bz, bstate, bnbt in all_blocks:
                    bridge.run_command(
                        f"mira_api redstone_update {ox + bx} {oy + by} {oz + bz}"
                    )
                result["actions"].append({
                    "position": list(action.position),
                    "state": action.state,
                    "action_type": "set_block",
                })

        # Tick for redstone propagation
        tick_steps = example.verify_contract.tick_steps
        bridge.run_command("tick freeze")
        bridge.run_command(f"tick step {tick_steps}")
        bridge.run_command("tick unfreeze")
        time.sleep(0.3)

        # Run final checks
        for check in example.verify_contract.final_checks:
            cx, cy, cz = check.position
            abs_pos = (ox + cx, oy + cy, oz + cz)
            resp = bridge.run_command(
                f"mira_api check_block {abs_pos[0]} {abs_pos[1]} {abs_pos[2]} {check.expected_state}"
            )
            passed = "PASS" in resp
            result["final_checks"].append({
                "position": list(check.position),
                "expected": check.expected_state,
                "passed": passed,
                "response": resp.strip()[:200],
            })
            if not passed:
                result["errors"].append(f"Final check failed at {check.position}: expected {check.expected_state}")

        # Run negative checks (unpower circuit, verify output changes)
        if example.verify_contract.negative_checks:
            # Unpower all levers using toggle_lever (proper redstone updates)
            for x, y, z, state, nbt in all_blocks:
                if "lever" in state:
                    abs_pos = (ox + x, oy + y, oz + z)
                    # Check if lever is powered, if so toggle it OFF
                    state_resp = bridge.run_command(f"mira_api get_state {abs_pos[0]} {abs_pos[1]} {abs_pos[2]}")
                    if "powered: true" in state_resp or "powered=true" in state_resp:
                        # Toggle lever OFF with proper timing
                        bridge.run_command(f"mira_api toggle_lever {abs_pos[0]} {abs_pos[1]} {abs_pos[2]}")
                        bridge.run_command("tick freeze")
                        bridge.run_command("tick step 5")
                        bridge.run_command("tick unfreeze")
                elif "redstone_block" in state:
                    bridge.run_command(
                        f"setblock {ox + x} {oy + y} {oz + z} minecraft:stone"
                    )
                    bridge.run_command("tick freeze")
                    bridge.run_command("tick step 5")
                    bridge.run_command("tick unfreeze")

            # Emit redstone updates on ALL blocks in the circuit
            for bx, by, bz, bstate, bnbt in all_blocks:
                bridge.run_command(
                    f"mira_api redstone_update {ox + bx} {oy + by} {oz + bz}"
                )

            # Tick
            bridge.run_command("tick freeze")
            bridge.run_command(f"tick step {tick_steps}")
            bridge.run_command("tick unfreeze")
            time.sleep(0.3)

            for check in example.verify_contract.negative_checks:
                cx, cy, cz = check.position
                abs_pos = (ox + cx, oy + cy, oz + cz)
                resp = bridge.run_command(
                    f"mira_api check_block {abs_pos[0]} {abs_pos[1]} {abs_pos[2]} {check.expected_state}"
                )
                passed = "PASS" in resp
                result["negative_checks"].append({
                    "position": list(check.position),
                    "expected": check.expected_state,
                    "passed": passed,
                    "response": resp.strip()[:200],
                })
                if not passed:
                    result["errors"].append(f"Negative check failed at {check.position}: expected {check.expected_state}")

        # Determine overall pass/fail
        all_initial = all(c["passed"] for c in result["initial_checks"])
        all_final = all(c["passed"] for c in result["final_checks"])
        all_negative = all(c["passed"] for c in result["negative_checks"]) if result["negative_checks"] else True
        result["passed"] = all_initial and all_final and all_negative

    except Exception as e:
        result["errors"].append(f"Exception: {str(e)}")
        result["passed"] = False

    finally:
        # Cleanup
        try:
            bridge.run_command(f"forceload remove {ox} {oz} {ox + region_size} {oz + region_size}")
        except Exception:
            pass

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Verify procedural circuit generators")
    parser.add_argument("--category", default=None, help="Only test this category")
    parser.add_argument("--limit", type=int, default=None, help="Max examples per generator")
    parser.add_argument("--output", default=None, help="Output JSON file")
    args = parser.parse_args()

    # Connect to server
    bridge = MinecraftBridge()
    try:
        bridge.connect()
        print("Connected to MC server")
    except Exception as e:
        print(f"Failed to connect to MC server: {e}")
        sys.exit(1)

    # Generate all examples
    all_examples = []
    for gen_class in ALL_GENERATORS:
        gen = gen_class()
        if args.category and gen.category != args.category:
            continue
        examples = gen.generate_all()
        if args.limit:
            examples = examples[:args.limit]
        print(f"  {gen.name}: {len(examples)} examples")
        all_examples.extend(examples)

    print(f"\nVerifying {len(all_examples)} examples...")

    # Verify each example
    results = []
    origin_x = 0
    origin_z = 0
    spacing = 64

    for i, example in enumerate(all_examples):
        # Grid layout for origins
        col = i % 10
        row = i // 10
        origin = (col * spacing, 200, row * spacing)

        print(f"  [{i+1}/{len(all_examples)}] {example.metadata.get('generator', '?')} "
              f"({example.block_count} blocks)...", end=" ", flush=True)

        result = build_and_verify(bridge, example, origin)
        results.append(result)

        status = "PASS" if result["passed"] else "FAIL"
        print(f"{status}")
        if not result["passed"]:
            for err in result["errors"][:3]:
                print(f"    -> {err}")

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{total} passed ({100*passed/total:.1f}%)")
    print(f"{'='*60}")

    # Per-generator breakdown
    by_gen = {}
    for r in results:
        gen = r["generator"]
        if gen not in by_gen:
            by_gen[gen] = {"total": 0, "passed": 0}
        by_gen[gen]["total"] += 1
        if r["passed"]:
            by_gen[gen]["passed"] += 1

    print("\nPer-generator breakdown:")
    for gen, stats in sorted(by_gen.items()):
        pct = 100 * stats["passed"] / stats["total"]
        print(f"  {gen:30s}: {stats['passed']}/{stats['total']} ({pct:.0f}%)")

    # Save results
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = PROJECT_ROOT / "evaluation" / "results" / "procedural_verification"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"verification_{timestamp}.json"

    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "total": total,
            "passed": passed,
            "failed": failed,
            "results": results,
        }, f, indent=2)
    print(f"\nResults saved to {output_path}")

    bridge.disconnect()


if __name__ == "__main__":
    main()
