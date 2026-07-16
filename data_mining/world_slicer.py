"""
MIRA: 3D Connected-Component World Slicer
Parses a large schematic containing multiple spaced-out redstone contraptions
and slices them into independent, normalized circuit components.
"""

import os
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Any, Set

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_mining.parser import SchematicParser
from litemapy import Schematic, Region, BlockState

class WorldSlicer:
    def __init__(self, distance_threshold: int = 2, min_size: int = 3):
        """
        Args:
            distance_threshold: Maximum distance between two blocks to consider them connected.
                                Default of 2 is ideal for redstone networks.
            min_size: Minimum number of blocks in a component to save it (filters out noise).
        """
        self.distance_threshold = distance_threshold
        self.min_size = min_size

    def slice_schematic(self, file_path: str) -> List[List[Tuple[int, int, int, str, Any]]]:
        """
        Parses a schematic file, slices it into distinct components, and returns them.
        """
        print(f"Loading schematic from {file_path}...")
        parser = SchematicParser(file_path)
        blocks = parser.parse_blocks()
        print(f"Parsed {len(blocks)} total non-air blocks from schematic.")

        raw_islands = self._find_islands(blocks)
        print(f"Connected component BFS discovered {len(raw_islands)} total islands.")

        # Filter and normalize islands
        valid_islands = []
        for island in raw_islands:
            if len(island) >= self.min_size:
                normalized = self._normalize_island(island)
                valid_islands.append(normalized)

        print(f"Filtered down to {len(valid_islands)} components of size >= {self.min_size}.")
        return valid_islands

    def _find_islands(self, blocks: List[Tuple[int, int, int, str, Any]]) -> List[List[Tuple[int, int, int, str, Any]]]:
        coords = set(b[:3] for b in blocks)
        block_dict = {b[:3]: b for b in blocks}
        visited: Set[Tuple[int, int, int]] = set()
        islands = []

        for coord in coords:
            if coord in visited:
                continue

            island = []
            queue = [coord]
            visited.add(coord)

            while queue:
                curr = queue.pop(0)
                island.append(block_dict[curr])

                cx, cy, cz = curr
                # Scan Chebyshev 3D neighborhood within distance_threshold
                for nx in range(cx - self.distance_threshold, cx + self.distance_threshold + 1):
                    for ny in range(cy - self.distance_threshold, cy + self.distance_threshold + 1):
                        for nz in range(cz - self.distance_threshold, cz + self.distance_threshold + 1):
                            neighbor = (nx, ny, nz)
                            if neighbor in coords and neighbor not in visited:
                                visited.add(neighbor)
                                queue.append(neighbor)

            islands.append(island)

        return islands

    def _normalize_island(self, island: List[Tuple[int, int, int, str, Any]]) -> List[Tuple[int, int, int, str, Any]]:
        min_x = min(b[0] for b in island)
        min_y = min(b[1] for b in island)
        min_z = min(b[2] for b in island)

        normalized = []
        for x, y, z, state, nbt in island:
            normalized.append((int(x - min_x), int(y - min_y), int(z - min_z), state, nbt))

        return sorted(normalized, key=lambda b: (b[1], b[0], b[2]))

    def save_components_to_litematic(self, components: List[List[Tuple[int, int, int, str, Any]]], output_dir: str, base_name: str):
        """
        Saves each sliced component as an individual .litematic file.
        """
        os.makedirs(output_dir, exist_ok=True)
        print(f"Saving {len(components)} components to {output_dir}...")

        for idx, comp in enumerate(components):
            # Calculate bounds
            max_x = max(b[0] for b in comp)
            max_y = max(b[1] for b in comp)
            max_z = max(b[2] for b in comp)

            reg = Region(0, 0, 0, max_x + 1, max_y + 1, max_z + 1)
            schem = Schematic(name=f"{base_name}_{idx}", author="MIRA Slicer", regions={"Main": reg})

            for x, y, z, state_str, _ in comp:
                try:
                    if "[" in state_str and state_str.endswith("]"):
                        base_id, props_str = state_str.split("[", 1)
                        props_str = props_str[:-1]
                        props = {}
                        for p in props_str.split(","):
                            if "=" in p:
                                k, v = p.split("=", 1)
                                props[k.strip()] = v.strip()
                        reg[x, y, z] = BlockState(base_id, **props)
                    else:
                        reg[x, y, z] = BlockState(state_str)
                except Exception as e:
                    print(f"Error parsing block state {state_str} in slice saving: {e}")

            out_path = os.path.join(output_dir, f"{base_name}_{idx}.litematic")
            schem.save(out_path)
            print(f"  Saved component {idx} ({len(comp)} blocks) -> {out_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python world_slicer.py <schematic_file> [output_dir] [base_name]")
        sys.exit(1)

    schem_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "data/sliced_components"
    b_name = sys.argv[3] if len(sys.argv) > 3 else "sliced"

    slicer = WorldSlicer(distance_threshold=2, min_size=3)
    sliced = slicer.slice_schematic(schem_file)
    slicer.save_components_to_litematic(sliced, out_dir, b_name)
    print("Done!")
