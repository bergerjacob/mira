import random
import re
import copy

class CircuitCorruptor:
    def __init__(self, blocks):
        """
        blocks: List of (x, y, z, block_state, nbt) tuples.
        """
        self.original_blocks = copy.deepcopy(blocks)
        self.corrupted_blocks = copy.deepcopy(blocks)
        self.modifications = []

    def corrupt(self, mode="random"):
        """
        Applies a random corruption.
        Returns the corrupted block list and the modification log.
        """
        options = [self.break_redstone_dust, self.rotate_repeater, self.remove_power_source]
        # Shuffle options to try random ones
        random.shuffle(options)
        
        success = False
        for opt in options:
            if opt():
                success = True
                break
        
        return self.corrupted_blocks, self.modifications

    def break_redstone_dust(self):
        # Find all redstone wire
        candidates = []
        for i, (x, y, z, state, nbt) in enumerate(self.corrupted_blocks):
            if "minecraft:redstone_wire" in state:
                candidates.append(i)
        
        if not candidates:
            return False

        idx = random.choice(candidates)
        x, y, z, state, nbt = self.corrupted_blocks[idx]
        
        # Action: Remove it (Set to Air)
        self.corrupted_blocks[idx] = (x, y, z, "minecraft:air", None)
        self.modifications.append({
            "type": "break_wire",
            "pos": (x, y, z),
            "original": state,
            "new": "minecraft:air"
        })
        return True

    def rotate_repeater(self):
        # Find repeaters or comparators or observers
        candidates = []
        target_types = ["repeater", "comparator", "observer", "piston", "dropper", "dispenser", "hopper"]
        for i, (x, y, z, state, nbt) in enumerate(self.corrupted_blocks):
            if "facing=" in state and any(t in state for t in target_types):
                candidates.append(i)
        
        if not candidates:
            return False
            
        idx = random.choice(candidates)
        x, y, z, state, nbt = self.corrupted_blocks[idx]
        
        # Parse facing
        match = re.search(r"facing=([a-z]+)", state)
        if not match:
            return False
        
        current_facing = match.group(1)
        directions = ["north", "east", "south", "west", "up", "down"]
        
        # Filter valid directions based on block type
        # Most redstone components are horizontal only
        is_horizontal = any(t in state for t in ["repeater", "comparator"])
        if is_horizontal:
            valid_dirs = ["north", "east", "south", "west"]
        else:
            valid_dirs = directions

        possible_dirs = [d for d in valid_dirs if d != current_facing]
        if not possible_dirs:
            return False
            
        new_facing = random.choice(possible_dirs)
        
        new_state = state.replace(f"facing={current_facing}", f"facing={new_facing}")
        self.corrupted_blocks[idx] = (x, y, z, new_state, nbt)
        
        self.modifications.append({
            "type": "rotate_component",
            "pos": (x, y, z),
            "original": state,
            "new": new_state
        })
        return True

    def remove_power_source(self):
        # Find torches, levers, blocks of redstone
        candidates = []
        target_blocks = ["redstone_torch", "lever", "redstone_block", "target"]
        for i, (x, y, z, state, nbt) in enumerate(self.corrupted_blocks):
            if any(t in state for t in target_blocks):
                candidates.append(i)
                
        if not candidates:
            return False
            
        idx = random.choice(candidates)
        x, y, z, state, nbt = self.corrupted_blocks[idx]
        
        self.corrupted_blocks[idx] = (x, y, z, "minecraft:air", None)
        self.modifications.append({
            "type": "remove_source",
            "pos": (x, y, z),
            "original": state,
            "new": "minecraft:air"
        })
        return True

