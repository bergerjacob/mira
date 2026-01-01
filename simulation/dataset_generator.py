import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_mining.parser import SchematicParser
from simulation.deconstructor import ReverseDeconstructor
from simulation.teacher_client import TeacherClient

class NBTEncoder(json.JSONEncoder):
    def default(self, obj):
        # Handle nbtlib types that are not JSON serializable by default
        if hasattr(obj, 'snbt'):
            return obj.snbt()
        if hasattr(obj, 'tolist'): # Handle numpy arrays or similar
            return obj.tolist()
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)

class ReverseDatasetGenerator:
    """
    Phase 4 dataset generator implementing Reverse Deconstruction.
    """

    def __init__(self):
        self.teacher = TeacherClient()
        self.deconstructor = ReverseDeconstructor(self.teacher)

    def process_schematic(self, schematic_path: str) -> Dict[str, Any]:
        parser = SchematicParser(schematic_path)
        blocks = parser.parse_blocks()
        meta = parser.get_metadata()

        contract = self.teacher.generate_test_contract(meta, blocks)
        deconstruction_steps = self.deconstructor.plan(blocks)
        build_steps = self._derive_build_steps(deconstruction_steps)

        return {
            "schematic_id": meta.get("name") or os.path.basename(schematic_path),
            "status": "success",
            "data": {
                "metadata": meta,
                "contract_prompt": contract["prompt"],
                "verify_contract": contract["script"],
                "deconstruction_steps": deconstruction_steps,
                "build_steps": build_steps,
            },
        }

    def _derive_build_steps(self, deconstruction_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        build_steps: List[Dict[str, Any]] = []
        stage = 0

        for step in reversed(deconstruction_steps):
            blocks_to_place = step["removed_blocks"]
            if not blocks_to_place:
                continue

            build_steps.append({
                "stage": stage,
                "instruction": f"Recreate layer from deconstruction step {step['step']}",
                "blocks_to_place": blocks_to_place,
                "source_reasoning": step["reasoning"],
            })
            stage += 1

        return build_steps


def main():
    parser = argparse.ArgumentParser(description="MIRA Reverse Dataset Generator (Phase 4)")
    parser.add_argument("--input-dir", default="data/raw_schematics", help="Directory containing .litematic files")
    parser.add_argument("--output-file", default="data/training/reverse_dataset.jsonl", help="Output JSONL file")
    parser.add_argument("--single-file", help="Process a single schematic file")

    args = parser.parse_args()

    generator = ReverseDatasetGenerator()

    if args.single_file:
        files = [args.single_file]
    else:
        files = [
            os.path.join(root, fname)
            for root, _, fnames in os.walk(args.input_dir)
            for fname in fnames
            if fname.endswith(".litematic")
        ]

    print(f"Found {len(files)} schematics to process.")
    os.makedirs(os.path.dirname(args.output_file), exist_ok=True)

    with open(args.output_file, "a") as outfile:
        for path in files:
            print(f"Processing {path} ...")
            try:
                result = generator.process_schematic(path)
                outfile.write(json.dumps(result, cls=NBTEncoder) + "\n")
            except Exception as exc:
                print(f"Failed to process {path}: {exc}")


if __name__ == "__main__":
    main()
