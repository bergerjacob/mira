#!/usr/bin/env python3
"""
Standalone helper that mirrors the style of the other MIRA scripts but does not
depend on the broader codebase. Paste the model-generated placement logic inside
`build_schematic` and run this file to emit a `.litematic`.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import importlib.util
import sys
from types import ModuleType
from typing import Callable, Dict, Tuple

from litemapy import Region, Schematic

# Update these values to match the structure provided by the LLM output.
# Default metadata; can be overridden by generated_schematic.SCHEMATIC_META
DEFAULT_SCHEMATIC_META: Dict[str, object] = {
    "name": "Placeholder_Schematic",
    "author": "MIRA_User",
    "size": (1, 1, 1),  # (x, y, z) extents in blocks
    "origin": (0, 0, 0),  # Usually leave at (0,0,0) unless you need offsets
    "filename": "placeholder.litematic",
}


def load_generated_module() -> ModuleType:
    """
    Import `generated_schematic.py` from the scripts directory and return the module.
    Raises helpful guidance if missing.
    """
    module_name = "generated_schematic"
    module_path = Path(__file__).with_name("generated_schematic.py")

    if not module_path.exists():
        raise RuntimeError(
            "Missing scripts/generated_schematic.py. Paste the model output into "
            "that file so it exposes a `build_schematic(region: Region)` function."
        )

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]

    return module


def get_build_function(module: ModuleType) -> Callable[[Region], None]:
    """Extract the required `build_schematic` callable from the generated module."""
    build_fn = getattr(module, "build_schematic", None)
    if build_fn is None:
        raise RuntimeError(
            "`scripts/generated_schematic.py` must define `build_schematic(region: Region) -> None`."
        )
    return build_fn  # type: ignore[return-value]


def resolve_schematic_meta(module: ModuleType) -> Dict[str, object]:
    """
    Merge DEFAULT_SCHEMATIC_META with any SCHEMATIC_META provided by the generated module.
    """
    meta = dict(DEFAULT_SCHEMATIC_META)
    override = getattr(module, "SCHEMATIC_META", None)
    if override:
        meta.update(override)
    if "size" not in meta:
        raise RuntimeError("SCHEMATIC_META must define a `size` tuple.")
    return meta


def create_region(meta: Dict[str, object]) -> Region:
    """
    Initialize a Region anchored at the configured origin and sized per bounds.
    """
    ox, oy, oz = meta["origin"]
    sx, sy, sz = meta["size"]
    if min(sx, sy, sz) <= 0:
        raise ValueError("Region extents must be positive integers.")
    return Region(ox, oy, oz, sx, sy, sz)


def generate_schematic(
    output_dir: Path,
    mutate_fn: Callable[[Region], None],
    meta: Dict[str, object],
) -> Path:
    """
    Execute the placement routine and persist the resulting schematic.
    """
    region = create_region(meta)
    mutate_fn(region)

    schematic = Schematic(
        name=meta["name"],
        author=meta["author"],
        regions={meta["name"]: region},
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / meta["filename"]
    schematic.save(str(output_path))
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Template Litematic generator runner.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw_schematics"),
        help="Directory where the .litematic file will be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    module = load_generated_module()
    build_fn = get_build_function(module)
    meta = resolve_schematic_meta(module)
    result = generate_schematic(args.output, build_fn, meta)
    print(f"Saved schematic to {result}")


if __name__ == "__main__":
    main()

