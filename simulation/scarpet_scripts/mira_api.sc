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
      'check_inv <pos> <string> <int> <text>' -> _(pos, slot, count, item) -> check_inventory(pos, slot, item, count),
      'check_entity <pos> <text>' -> _(pos, type) -> check_entity(pos, type)
   }
};

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
           if (expected_item == 'air', 
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
   selector = '@e[type=' + expected_type + ',x=' + pos:0 + ',y=' + pos:1 + ',z=' + pos:2 + ',distance=..1]';
   e_list = entity_selector(selector);
   
   if (length(e_list) > 0,
       print(format('g PASS: Found ' + expected_type + ' at ' + pos));
       'PASS',
       print(format('r FAIL: Entity ' + expected_type + ' not found at ' + pos + '. Selector: ' + selector));
       'FAIL'
   )
);
