// mira_api.sc
__config() -> {
   'scope' -> 'global',
   'commands' -> {
      'test' -> _() -> print('Command Working'),
      'check_block <pos> <text>' -> _(pos, block_str) -> check_block(pos, block_str),
      'update_block <pos>' -> _(pos) -> update(pos),
      'update_region <text>' -> _(args) -> (
          parts = split(' ', args);
          if (length(parts) == 6,
              x1 = number(parts:0);
              y1 = number(parts:1);
              z1 = number(parts:2);
              x2 = number(parts:3);
              y2 = number(parts:4);
              z2 = number(parts:5);
              
              pos1 = [x1, y1, z1];
              pos2 = [x2, y2, z2];
              
              print(format('w Updating region from ' + pos1 + ' to ' + pos2));
              scan(pos1, pos2, update(_))
          ,
              print(format('r Error: update_region requires 6 coordinates (x1 y1 z1 x2 y2 z2)'))
          )
      ),
      'get_state <pos>' -> _(pos) -> (
          // Return block state as string map
          print(str(block_state(pos)))
      ),
      'check_inv <pos> <string> <int> <text>' -> _(pos, slot, item, count) -> check_inventory(pos, slot, item, count),
      'check_entity <pos> <text>' -> _(pos, type) -> check_entity(pos, type),
      // redstone_update: Emit proper NC (Neighbor Changed) and PP (Post Placement) updates
      // at priority 0/2 using TIS Addition's manipulate command. This is the reliable way
      // to trigger redstone updates after setblock — setblock alone only emits priority 3
      // updates which redstone components (repeaters, comparators, pistons, torches, dust)
      // do NOT respond to. See MC-31100, MC-48804, MC-77180.
      'redstone_update <pos>' -> _(pos) -> redstone_update(pos),
      // toggle_lever: Simulate a player flipping a lever. Reads current powered state,
      // flips it, then emits proper redstone updates so the signal propagates correctly.
      'toggle_lever <pos>' -> _(pos) -> toggle_lever(pos),
      // press_button: Simulate a player pressing a button. Uses fake player interaction
      // which triggers proper priority -1 NC updates (buttons don't have a powered state
      // we can setblock — they need actual interaction).
      'press_button <pos>' -> _(pos) -> press_button(pos)
   }
};

// Emit NC (Neighbor Changed) update at priority 0 and PP (Post Placement) update at priority 2.
// This is what redstone components respond to. Uses TIS Addition's manipulate command.
// Must be called AFTER setblock changes the block state.
// Syntax: manipulate block <x1> <y1> <z1> <x2> <y2> <z2> emit <update_type>
redstone_update(pos) -> (
    run(str('manipulate block %d %d %d %d %d %d emit block_update', pos:0, pos:1, pos:2, pos:0, pos:1, pos:2));
    run(str('manipulate block %d %d %d %d %d %d emit state_update', pos:0, pos:1, pos:2, pos:0, pos:1, pos:2));
    'OK'
);

// Toggle a lever: read current powered state, flip it.
// This simulates a player right-clicking the lever.
// NOTE: Does NOT emit redstone updates — the caller must do:
//   1. mira_api toggle_lever <pos>     (this function — changes lever state)
//   2. tick step 5                      (let lever state propagate internally)
//   3. mira_api redstone_update <pos>  (emit NC update on lever)
//   4. mira_api redstone_update <attached_block_pos>  (emit NC update on attached block)
//   5. tick step 10                     (let torch/repeater re-evaluate)
// This sequence is required because set() doesn't propagate redstone signal
// immediately — the lever state needs a tick to propagate before block_update
// will cause neighbors to re-evaluate.
toggle_lever(pos) -> (
    b = block(pos);
    // Verify it's actually a lever
    if (str(b) != 'lever' && str(b) != 'minecraft:lever',
        print(format('r FAIL: Block at ' + pos + ' is not a lever (got ' + b + ')'));
        return('FAIL')
    );
    
    // Read current state — use get() for map access, not : operator
    state = block_state(pos);
    current_powered = get(state, 'powered');
    
    // Flip the powered state — handle both boolean and string values
    is_on = (current_powered == 'true' || current_powered == true);
    if (is_on,
        new_powered = 'false',
        new_powered = 'true'
    );
    
    // Reconstruct lever state preserving ALL properties (face, facing, powered)
    face = get(state, 'face');
    if (face == null, face = 'wall');  // fallback
    facing = get(state, 'facing');
    if (facing == null, facing = 'east');  // fallback
    
    // Place the lever with flipped state (does NOT emit redstone updates)
    set(pos, 'lever[face=' + face + ',facing=' + facing + ',powered=' + new_powered + ']');
    
    print(format('g Lever at ' + pos + ' toggled to powered=' + new_powered + ' (was ' + current_powered + ')'));
    'OK'
);

// Press a button: use fake player interaction (works perfectly for buttons).
// Buttons don't have a settable powered state — they need actual interaction.
press_button(pos) -> (
    b = block(pos);
    if (str(b) != 'button' && str(b) != 'minecraft:button',
        print(format('r FAIL: Block at ' + pos + ' is not a button (got ' + b + ')'));
        return('FAIL')
    );
    
    // Use fake player to interact with the button
    // /player spawn creates a fake player, /player use once interacts
    // This triggers proper priority -1 NC updates
    run(str('execute positioned %d %d %d run player Steve use once', pos:0, pos:1, pos:2));
    
    print(format('g Button at ' + pos + ' pressed'));
    'OK'
);

check_block(pos, expected_input) -> (
   // Parse expected string "id[k=v,k2=v2]"
   expected_props = null;
   
   if (expected_input ~ '\\[',
       parts = split('\\[', expected_input);
       expected_id = parts:0;
       
       // Handle properties string "k=v,k2=v2]"
       raw_props = parts:1;
       // Remove trailing ]
       raw_props = split(']', raw_props):0;
       
       expected_props = {};
       pairs = split(',', raw_props);
       for (pairs,
           kv = split('=', _);
           if (length(kv) == 2,
               expected_props:(kv:0) = kv:1
           )
       )
   ,
       expected_id = expected_input
   );
   
   // Normalize Expected ID
   if (!(expected_id ~ ':'), expected_id = 'minecraft:' + expected_id);
   
   // Get Actual Block
   b = block(pos);
   actual_id = str(b);
   if (!(actual_id ~ ':'), actual_id = 'minecraft:' + actual_id);
   
   actual_props = block_state(pos);
   print(format('g DEBUG: Read Block at ' + pos + ': ID=' + actual_id + ', Props=' + actual_props));

   // 1. Check ID
   if (actual_id != expected_id,
       print(format('r FAIL: Block ID mismatch at ' + pos + '. Expected ' + expected_id + ', got ' + actual_id));
       return('FAIL')
   );
   
   // 2. Check Properties (if any expected)
   if (expected_props,
       actual_props = block_state(pos);
       
       // If block has no properties (null), but we expected some -> Fail
       if (!actual_props,
           print(format('r FAIL: Block ' + actual_id + ' has no properties, but expected ' + expected_props));
           return('FAIL')
       );
       
       mismatch = false;
       for (keys(expected_props),
           key = _;
           exp_val = expected_props:key;
           act_val = actual_props:key;
           
           // Convert everything to string for comparison to be safe
           if (str(act_val) != str(exp_val),
               print(format('r FAIL: Property ' + key + ' mismatch. Expected ' + exp_val + ', got ' + act_val));
               mismatch = true
           )
       );
       
       if (mismatch,
           print(format('w Full Actual State: ' + actual_props));
           return('FAIL')
       )
   );
   
   print(format('g PASS: Block at ' + pos + ' matches ' + expected_input));
   'PASS'
);

check_inventory(pos, slot, expected_item, expected_count) -> (
   // Strip 's' prefix if present
   if (slice(slot, 0, 1) == 's', slot = slice(slot, 1));
   
   slot = number(slot);
   count = number(expected_count);
   expected_item = str(expected_item);
   
       item = inventory_get(pos, slot);
       if (!item, 
           if (expected_item == 'air' || expected_item == 'minecraft:air',
               print(format('g PASS: Slot ' + slot + ' at ' + pos + ' is empty')); 'PASS',
               print(format('r FAIL: Slot ' + slot + ' at ' + pos + ' is empty, expected ' + expected_item)); 'FAIL'
           ),
           
           id = item:0;
           count = item:1;
           id = str(id);
           if (!(id ~ ':'), id = 'minecraft:' + id);
           
           if (id == expected_item && count == expected_count,
           print(format('g PASS: Slot ' + slot + ' at ' + pos + ' is ' + id + ' x' + count));
           'PASS',
           print(format('r FAIL: Slot ' + slot + ' at ' + pos + ' is ' + id + ' x' + count + ', expected ' + expected_item + ' x' + expected_count));
           'FAIL'
       )
   )
);

check_entity(pos, expected_type) -> (
   // Normalize expected_type
   if (!(expected_type ~ ':'), expected_type = 'minecraft:' + expected_type);
   
   // Use selector for robustness
    selector = '@e[type=' + expected_type + ',x=' + pos:0 + ',y=' + pos:1 + ',z=' + pos:2 + ',distance=..2]';
   e_list = entity_selector(selector);
   
   if (length(e_list) > 0,
       print(format('g PASS: Found ' + expected_type + ' at ' + pos));
       'PASS',
       print(format('r FAIL: Entity ' + expected_type + ' not found at ' + pos + '. Selector: ' + selector));
       'FAIL'
   )
);
