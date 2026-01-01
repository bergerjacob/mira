from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import math
from litemapy import BlockState, Region

SCHEMATIC_META = {
    "name": "Complex_Crystalline_Wireframe",
    "size": (256, 1, 256),
    "filename": "crystalline_wireframe.litematic",
}

# Single block type for the wireframe lines for maximum contrast
LINE_BLOCK = "minecraft:white_concrete"

@dataclass(frozen=True)
class BuildContext:
    region: Region
    origin: Tuple[int, int, int]
    size: Tuple[int, int, int]

def block(block_id: str, **props: str) -> BlockState:
    state = BlockState(block_id)
    if props:
        state._BlockState__properties = {str(k): str(v) for k, v in props.items()}
    return state

def validate_request(ctx: BuildContext) -> None:
    if ctx.size != (256, 1, 256):
        raise ValueError(f"Invalid size {ctx.size}. Expected (256, 1, 256).")

def place(ctx: BuildContext, x: int, y: int, z: int, state: BlockState) -> None:
    if not (0 <= x < ctx.size[0] and 0 <= y < ctx.size[1] and 0 <= z < ctx.size[2]):
        raise ValueError(f"Coordinate ({x}, {y}, {z}) out of bounds.")
    ctx.region[ctx.origin[0] + x, ctx.origin[1] + y, ctx.origin[2] + z] = state

def rotate_2d(u: float, v: float, angle_rad: float) -> Tuple[float, float]:
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return u * c - v * s, u * s + v * c

def is_fractal_line(u: float, v: float) -> bool:
    """
    Determines if a coordinate (u,v) belongs to the thin fractal line structure.
    Uses an "Orbit Trap" method: tracking the minimum distance to an axis
    during iteration.
    """
    # 1. Pre-fold for 12-fold symmetry (Hexagonal)
    angle = math.atan2(v, u)
    radius = math.sqrt(u*u + v*v)
    # Fold space into a 30-degree wedge (PI/6)
    sector = math.pi / 6.0
    angle = abs((angle % (sector * 2.0)) - sector)
    u = radius * math.cos(angle)
    v = radius * math.sin(angle)

    # 2. Iterative Folding (KIFS) with Orbit Trapping
    # We track the closest the point ever gets to the 'v=0' axis (the 'trap')
    min_trap_dist = 100.0 
    scale_acc = 1.0
    
    # High iterations for extreme complexity
    iterations = 18 
    
    # Fractal parameters tweak the shape's "DNA"
    fold_angle = math.radians(-25) 
    offset_x = 1.2
    scale_factor = 1.45

    for _ in range(iterations):
        # A. Spatial Folding
        u = abs(u)
        v = abs(v)
        # Fold across a diagonal to create crystalline intersections
        if v > u: u, v = v, u 
            
        # B. Transform for next iteration
        # Rotate, shift center, and scale up
        u, v = rotate_2d(u, v, fold_angle)
        u -= offset_x * scale_acc
        u *= scale_factor
        v *= scale_factor
        scale_acc *= scale_factor
        
        # C. Orbit Trap
        # How close is the current point to the 'v=0' axis?
        # We normalize by the current scale to keep line width consistent across scales.
        current_dist = abs(v) / scale_acc
        min_trap_dist = min(min_trap_dist, current_dist)

    # 3. Thresholding
    # If the point's orbit came very close to the trap, it's part of a line.
    # The threshold determines line thickness. Very small = very thin lines.
    # threshold = 0.0025
    threshold = 0.005
    return min_trap_dist < threshold

def build_layers(ctx: BuildContext) -> None:
    center_x = ctx.size[0] / 2.0
    center_z = ctx.size[2] / 2.0
    # Scaling factor to fit the fractal nicely in the 256 bounds
    zoom = 100.0

    line_state = block(LINE_BLOCK)
    air_state = block("minecraft:air")

    for x in range(ctx.size[0]):
        for z in range(ctx.size[2]):
            # Normalize coordinates to center
            nx = (x - center_x) / zoom
            nz = (z - center_z) / zoom
            
            # Hard circular cropping to keep edges clean
            if nx*nx + nz*nz > 1.8: # 1.8 squared radius constraint
                 place(ctx, x, 0, z, air_state)
                 continue

            # Check if this pixel is part of the fractal line
            if is_fractal_line(nx, nz):
                place(ctx, x, 0, z, line_state)
            else:
                place(ctx, x, 0, z, air_state)

def wire_redstone(ctx: BuildContext) -> None:
    pass

def build_schematic(region: Region) -> None:
    size = SCHEMATIC_META["size"]
    ctx = BuildContext(region=region, origin=(0, 0, 0), size=size)
    validate_request(ctx)
    build_layers(ctx)
    wire_redstone(ctx)
