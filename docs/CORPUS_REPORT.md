# Corpus report

Generated 2026-11-02 06:38 UTC by `scripts/corpus_index.py` from `data/corpus/index.jsonl` (index and summary: `data/corpus/index_summary.json`).

- **4179** schematic files indexed, **4179** parsed, **0** failed, **0** parsed but empty (0 non-air blocks).
- **2948** unique non-empty builds after de-duplication (1231 files are duplicates by sha256 or normalised block content; the canonical copy prefers licence-clean sources, then the cleaned Discord set).
- 'sim_ok' means Nucleation's `TickSimulation.from_schematic(..., Placement)` accepts the build (`scripts/corpus_index.py`, nucleation 0.12.0). Refusal reasons come from `TickSimulation.last_error_detail()`.
- 'entity_dependent' = refused because the build contains an entity the simulator cannot model (item frames, mobs, boats, ...). 'small_logic' = 1..500 non-air blocks, no entities, at least one wire/repeater/comparator/torch/observer/piston/copper bulb.
- Components: 26-neighbour connectivity via `Schematic.split_connected_attach_nearby(1, 0)`.

Files that needed a loader fallback (Nucleation 0.12's plain loader rejects them): structure_nbt_converted 483, recompressed_raw_nbt 30, negative_regions_repaired 10, entities_lost 10. `entities_lost` builds have no entities/block entities in the index.

## Failures

None.

## By source

| source / group | ML use | files | parsed | unique | sim_ok (unique) | small logic (unique) |
|---|---|---|---|---|---|---|
| converted/claudestone | clean | 457 | 457 | 453 | 432 | 404 |
| converted/redstone-verify | clean | 178 | 178 | 130 | 130 | 53 |
| converted/rouge | clean | 37 | 37 | 37 | 34 | 32 |
| discord-clean | internal-only | 242 | 242 | 167 | 43 | 75 |
| discord-raw | internal-only | 2296 | 2296 | 1285 | 155 | 154 |
| litematicas-leroymilo | permission | 3 | 3 | 3 | 2 | 2 |
| local-raw | internal-only | 13 | 13 | 13 | 9 | 3 |
| local-simple | internal-only | 2 | 2 | 0 | 0 | 0 |
| local-sliced | internal-only | 1 | 1 | 0 | 0 | 0 |
| minecraft-computers | permission | 2 | 2 | 2 | 0 | 0 |
| redstone-cpu | clean | 24 | 24 | 24 | 16 | 3 |
| redstone-machine-schematics | clean | 1 | 1 | 1 | 0 | 0 |
| redstone-schematics-nacl | clean | 1 | 1 | 1 | 1 | 0 |
| redstone-verify/reda | clean | 29 | 29 | 29 | 2 | 10 |
| redstone-verify/redstone-compiler-candidates | clean | 70 | 70 | 55 | 55 | 55 |
| redstone-verify/redstone-compiler-tiles | clean | 405 | 405 | 344 | 344 | 344 |
| redstone-verify/rtl2mc | clean | 5 | 5 | 5 | 0 | 0 |
| scorpio-file-downloads | clean | 86 | 86 | 83 | 10 | 9 |
| storage-catalog-archive | permission | 327 | 327 | 316 | 201 | 161 |

## Spec-checked ground truth (converted)

Built from machine-readable specs by `scripts/corpus_convert.py` (settled at-rest, inputs off). claudestone: tick-tested truth tables / waves on vanilla 26.3. redstone-verify: ACCEPT = SAT/LRAT-certified equal to `spec.v`, REJECT = refuted mutant (negative examples). rouge: prose-described, not verified.

| group | files | ACCEPT | REJECT | unique | sim_ok | sim_ok after strip | <=500 blocks |
|---|---|---|---|---|---|---|---|
| converted/claudestone | 457 | 0 | 0 | 453 | 436 | 457 | 418 |
| converted/redstone-verify | 178 | 62 | 116 | 130 | 178 | 178 | 53 |

Differential check (`scripts/corpus_check_rsl.py`, single-bit specs only): Nucleation reproduces the Verilog truth table for **30/31** ACCEPT layouts and differs from it for **7/7** REJECT mutants.

## Unique builds (n=2948)

### Size tiers (non-air blocks)

| tier | files | share | simulatable |
|---|---|---|---|
| <=40 | 778 | 26.4% | 707 |
| <=150 | 513 | 17.4% | 317 |
| <=500 | 479 | 16.2% | 147 |
| <=5000 | 744 | 25.2% | 229 |
| >5000 | 434 | 14.7% | 34 |

### Simulatability

- simulatable (sim_ok): **1434** (48.6%)
- entity-dependent (refused by entity name): **737** (25.0%); of those 647 would simulate with entities removed and behaviour-less blocks deleted
- builds that contain any entity: 1040 (35.3%)
- refusal kinds (of the refused): {"unknown_block": 753, "entity": 737, "block_entity": 21, "too_large": 2, "other": 1}
- first refusing entity per entity-refused build (the simulator names only the first): {"minecraft:item_frame": 154, "minecraft:glow_item_frame": 75, "minecraft:iron_golem": 57, "minecraft:boat": 53, "minecraft:snow_golem": 36, "minecraft:zombie": 29, "minecraft:piglin": 24, "minecraft:pillager": 23, "minecraft:zombified_piglin": 21, "minecraft:wither": 20, "minecraft:chicken": 18, "minecraft:creaking": 18, "minecraft:oak_chest_boat": 16, "minecraft:armor_stand": 10, "(passengers)": 10}
- most common behaviour-less blocks (each makes the whole build refuse): {"minecraft:nether_portal": 435, "minecraft:soul_fire": 353, "minecraft:lava": 310, "minecraft:ender_chest": 176, "minecraft:soul_soil": 99, "minecraft:crafter": 94, "minecraft:polished_blackstone_button": 81, "minecraft:cactus": 64, "minecraft:lectern": 61, "minecraft:vine": 59, "minecraft:end_portal": 57, "minecraft:brewing_stand": 56, "minecraft:sculk_catalyst": 45, "minecraft:podzol": 44, "minecraft:wither_rose": 43, "minecraft:farmland": 41, "minecraft:daylight_detector": 39, "minecraft:lantern": 38, "minecraft:white_bed": 37, "minecraft:melon": 37}
- **simulatable core** (`sim_ok_stripped`: entities removed + behaviour-less blocks deleted, then accepted): **2795** (94.8%); of the originally refused, 937 lose <=5% of their blocks
- simulatable core per tier: {"<=40": 776, "<=150": 507, "<=500": 468, "<=5000": 690, ">5000": 354}
- of the core, 2476 lost no redstone-relevant block type (stripping buttons, crafters, daylight detectors, lecterns, note-block-like blocks changes behaviour; those are in the 'core' only as a structure)

### Small logic circuits (the training / module-library sub-corpus)

- <=500 blocks and no entities: **1374** (1132 simulatable)
- of those with at least one logic component (`small_logic`): **1305** (**1097** simulatable as-is, 1292 after stripping behaviour-less blocks)
- `small_logic` excluding the ~475 Redstone-Compiler candidate/tile files from redstone-verify: **906**; by ML status: {"internal-only": 232, "clean": 886, "permission": 187}

### Redstone density (redstone-relevant blocks / non-air blocks)

| density | files |
|---|---|
| 0 | 89 |
| (0,0.02] | 126 |
| (0.02,0.05] | 113 |
| (0.05,0.15] | 284 |
| >0.15 | 2336 |

Connected components: {"1": 2034, "2-3": 530, ">10": 138, "4-10": 246}

Files containing each redstone component: wire 2417, torch 1974, repeater 1778, lever 1723, comparator 1654, hopper 1589, observer 1561, sticky_piston 1291, piston 1256, dropper 1229, note_block 1205, dispenser 1158, rails 1028, redstone_block 913, lamp 733, button 588, plate 483, target 483, crafter 416, tripwire_hook 224, copper_bulb 208, daylight 41

### Top blocks

| block | total count | | block | files containing |
|---|---|---|---|---|
| minecraft:nether_portal | 1508305 | | minecraft:redstone_wire | 2415 |
| minecraft:white_stained_glass | 910316 | | minecraft:redstone_wall_torch | 1879 |
| minecraft:redstone_wire | 693854 | | minecraft:repeater | 1776 |
| minecraft:obsidian | 525847 | | minecraft:lever | 1723 |
| minecraft:water | 516758 | | minecraft:comparator | 1652 |
| minecraft:deepslate | 470497 | | minecraft:hopper | 1589 |
| minecraft:bubble_column | 422103 | | minecraft:observer | 1561 |
| minecraft:observer | 368456 | | minecraft:sticky_piston | 1289 |
| minecraft:stone | 345149 | | minecraft:piston | 1256 |
| minecraft:bedrock | 335872 | | minecraft:dropper | 1229 |
| minecraft:white_concrete | 278536 | | minecraft:note_block | 1205 |
| minecraft:hopper | 275386 | | minecraft:dispenser | 1158 |
| minecraft:cauldron | 247665 | | minecraft:chest | 1111 |
| minecraft:smooth_stone | 247475 | | minecraft:redstone_torch | 1029 |
| minecraft:repeater | 242157 | | minecraft:water | 1008 |
| minecraft:grass_block | 233909 | | minecraft:white_stained_glass | 936 |
| minecraft:magma_block | 222921 | | minecraft:redstone_block | 913 |
| minecraft:smooth_stone_slab | 221744 | | minecraft:composter | 892 |
| minecraft:red_stained_glass | 221497 | | minecraft:smooth_stone | 890 |
| minecraft:glass | 207554 | | minecraft:piston_head | 861 |
| minecraft:netherrack | 204723 | | minecraft:soul_sand | 849 |
| minecraft:black_stained_glass | 195519 | | minecraft:obsidian | 805 |
| minecraft:nether_bricks | 189675 | | minecraft:white_concrete | 734 |
| minecraft:powered_rail | 174289 | | minecraft:redstone_lamp | 731 |
| minecraft:chest | 172898 | | minecraft:powered_rail | 729 |

Data versions (top): {"5023": 620, "3955": 448, "4189": 375, "4671": 274, "3953": 192, "4556": 186, "3465": 145, "3700": 130, "2975": 92, "2730": 59, "-1": 58, "4082": 41}

## All parsed non-empty files (with duplicates) (n=4179)

### Size tiers (non-air blocks)

| tier | files | share | simulatable |
|---|---|---|---|
| <=40 | 952 | 22.8% | 824 |
| <=150 | 758 | 18.1% | 360 |
| <=500 | 683 | 16.3% | 192 |
| <=5000 | 1117 | 26.7% | 321 |
| >5000 | 669 | 16.0% | 39 |

### Simulatability

- simulatable (sim_ok): **1736** (41.5%)
- entity-dependent (refused by entity name): **1176** (28.1%); of those 1018 would simulate with entities removed and behaviour-less blocks deleted
- builds that contain any entity: 1658 (39.7%)
- refusal kinds (of the refused): {"unknown_block": 1238, "entity": 1176, "block_entity": 25, "too_large": 2, "other": 2}
- first refusing entity per entity-refused build (the simulator names only the first): {"minecraft:item_frame": 241, "minecraft:glow_item_frame": 133, "minecraft:iron_golem": 83, "minecraft:boat": 81, "minecraft:snow_golem": 64, "minecraft:zombie": 46, "minecraft:pillager": 42, "minecraft:zombified_piglin": 32, "minecraft:wither": 32, "minecraft:chicken": 31, "minecraft:piglin": 30, "minecraft:oak_chest_boat": 23, "minecraft:creaking": 22, "(passengers)": 20, "minecraft:armor_stand": 19}
- most common behaviour-less blocks (each makes the whole build refuse): {"minecraft:nether_portal": 774, "minecraft:soul_fire": 569, "minecraft:lava": 487, "minecraft:ender_chest": 250, "minecraft:soul_soil": 162, "minecraft:crafter": 144, "minecraft:polished_blackstone_button": 113, "minecraft:cactus": 102, "minecraft:end_portal": 100, "minecraft:vine": 89, "minecraft:lectern": 85, "minecraft:podzol": 82, "minecraft:melon": 79, "minecraft:white_bed": 77, "minecraft:brewing_stand": 72, "minecraft:sculk_catalyst": 68, "minecraft:farmland": 65, "minecraft:wither_rose": 64, "minecraft:daylight_detector": 62, "minecraft:carved_pumpkin": 55}
- **simulatable core** (`sim_ok_stripped`: entities removed + behaviour-less blocks deleted, then accepted): **3913** (93.6%); of the originally refused, 1440 lose <=5% of their blocks
- simulatable core per tier: {"<=40": 948, "<=150": 743, "<=500": 668, "<=5000": 1015, ">5000": 539}
- of the core, 3450 lost no redstone-relevant block type (stripping buttons, crafters, daylight detectors, lecterns, note-block-like blocks changes behaviour; those are in the 'core' only as a structure)

### Small logic circuits (the training / module-library sub-corpus)

- <=500 blocks and no entities: **1741** (1309 simulatable)
- of those with at least one logic component (`small_logic`): **1611** (**1249** simulatable as-is, 1585 after stripping behaviour-less blocks)
- `small_logic` excluding the ~475 Redstone-Compiler candidate/tile files from redstone-verify: **1136**; by ML status: {"internal-only": 456, "clean": 965, "permission": 190}

### Redstone density (redstone-relevant blocks / non-air blocks)

| density | files |
|---|---|
| 0 | 172 |
| (0,0.02] | 213 |
| (0.02,0.05] | 176 |
| (0.05,0.15] | 485 |
| >0.15 | 3133 |

Connected components: {"1": 2766, "2-3": 843, ">10": 220, "4-10": 350}

Files containing each redstone component: wire 3326, torch 2787, lever 2537, repeater 2511, hopper 2423, comparator 2351, observer 2313, piston 1926, sticky_piston 1902, dropper 1846, note_block 1755, dispenser 1727, rails 1585, redstone_block 1347, lamp 1058, button 907, plate 731, target 708, crafter 624, tripwire_hook 373, copper_bulb 260, daylight 65

### Top blocks

| block | total count | | block | files containing |
|---|---|---|---|---|
| minecraft:nether_portal | 3592736 | | minecraft:redstone_wire | 3322 |
| minecraft:white_stained_glass | 1410003 | | minecraft:redstone_wall_torch | 2668 |
| minecraft:obsidian | 1045887 | | minecraft:lever | 2537 |
| minecraft:deepslate | 939876 | | minecraft:repeater | 2507 |
| minecraft:redstone_wire | 905812 | | minecraft:hopper | 2423 |
| minecraft:water | 743779 | | minecraft:comparator | 2347 |
| minecraft:observer | 533945 | | minecraft:observer | 2313 |
| minecraft:bubble_column | 529044 | | minecraft:piston | 1926 |
| minecraft:smooth_stone | 515763 | | minecraft:sticky_piston | 1898 |
| minecraft:bedrock | 507459 | | minecraft:dropper | 1846 |
| minecraft:hopper | 432937 | | minecraft:note_block | 1755 |
| minecraft:white_concrete | 422204 | | minecraft:chest | 1743 |
| minecraft:nether_bricks | 398890 | | minecraft:dispenser | 1727 |
| minecraft:red_stained_glass | 389535 | | minecraft:water | 1577 |
| minecraft:stone | 387649 | | minecraft:white_stained_glass | 1387 |
| minecraft:glass | 384860 | | minecraft:redstone_torch | 1383 |
| minecraft:repeater | 332687 | | minecraft:obsidian | 1364 |
| minecraft:netherrack | 318641 | | minecraft:composter | 1357 |
| minecraft:grass_block | 317495 | | minecraft:redstone_block | 1347 |
| minecraft:smooth_stone_slab | 316048 | | minecraft:piston_head | 1341 |
| minecraft:powered_rail | 302173 | | minecraft:smooth_stone | 1323 |
| minecraft:magma_block | 278355 | | minecraft:soul_sand | 1309 |
| minecraft:black_stained_glass | 261734 | | minecraft:powered_rail | 1104 |
| minecraft:chest | 260863 | | minecraft:iron_trapdoor | 1097 |
| minecraft:cauldron | 257023 | | minecraft:slime_block | 1082 |

Data versions (top): {"5023": 672, "4189": 568, "3955": 553, "4671": 365, "3953": 317, "4556": 299, "3465": 249, "3700": 248, "2975": 135, "4440": 123, "2730": 76, "4325": 73}

## 20 example small circuits

Unique, simulatable, entity-free, 12-150 blocks, density >= 0.12, ranked by number of distinct logic components and available description (max 5 per source).

| # | title | source | blocks | dims | components used | path |
|---|---|---|---|---|---|---|
| 1 | Box Crafter Using Mixed Ingredient Boxes | storage-catalog-archive | 79 | 3x7x5 | dropper:7, observer:7, wire:7, hopper:6, note_block:6, comparator:5, dispenser:4, piston:3, crafter:2, lamp:2, torch:2, lever:1, redstone_block:1, repeater:1, target:1 | `data/external/storage-catalog-archive/Archive/AC_autocrafting/AC002_Box_Crafter_Using_Mixed_Ingredient_Boxes/attachments/AC002_box_material_loader_logs_slow.litematic` |
| 2 | 3wt 8x Mixed Box Loader | storage-catalog-archive | 92 | 5x7x3 | observer:20, hopper:19, dropper:10, rails:4, note_block:3, repeater:3, comparator:2, piston:2, sticky_piston:2, dispenser:1, lamp:1, torch:1, wire:1 | `data/external/storage-catalog-archive/Archive/ML_mixed-loaders/ML009_3wt_8x_Mixed_Box_Loader/attachments/ML009_3wt8xmixed_local_v2.litematic` |
| 3 | 3wt Fullspeed Full Clearance 6x Mixed Loader | storage-catalog-archive | 124 | 4x9x6 | hopper:26, observer:15, dropper:10, rails:6, note_block:5, comparator:4, torch:3, wire:3, repeater:3, sticky_piston:3, lever:2, piston:2, dispenser:1, redstone_block:1 | `data/external/storage-catalog-archive/Archive/ML_mixed-loaders/ML013_3wt_Fullspeed_Full_Clearance_6x_Mixed_Loader/attachments/ML013_3wt6xmixed_local_fsfc_v2.litematic` |
| 4 | Compact 2 Item Checker | storage-catalog-archive | 86 | 4x5x5 | observer:28, wire:6, repeater:6, dropper:5, hopper:5, comparator:4, note_block:4, piston:3, sticky_piston:2, dispenser:1, redstone_block:1, torch:1, target:1 | `data/external/storage-catalog-archive/Archive/OBP_other-box-processors/OBP001_Compact_2_Item_Checker/attachments/OBP001_ge2item_box_filter.litematic` |
| 5 | 1wt 1x locked input box unloader | storage-catalog-archive | 30 | 1x8x7 | comparator:4, hopper:4, observer:2, sticky_piston:2, dispenser:1, dropper:1, piston:1, torch:1, wire:1, repeater:1 | `data/external/storage-catalog-archive/Archive/BU_box-unloaders/BU012_1wt_1x_locked_input_box_unloader/attachments/BU012_1wt_1x_locked_input_box_unloader.litematic` |
| 6 | Wiske Chariot Reloaded [58 skulls/min] | discord-raw | 63 | 4x4x5 | note_block:8, observer:7, hopper:6, repeater:4, comparator:3, crafter:3, dispenser:2, piston:2, torch:2, dropper:1, lever:1, wire:1 | `discord_scraper/data/raw_schematics/1161803566265143306/1420258177684865098_1_04cf570d50a59be1.litematic` |
| 7 | Box loader from booty's armadillo wither rose farm | discord-raw | 53 | 4x9x6 | hopper:8, observer:8, dropper:6, torch:2, wire:2, sticky_piston:2, comparator:1, dispenser:1, lever:1, piston:1, repeater:1 | `discord_scraper/data/raw_schematics/1161803566265143306/1477526477049299125_1_booty_loader_fix.litematic` |
| 8 | simple bm glowberry farm (no foxes) | discord-raw | 38 | 5x6x3 | hopper:5, dispenser:4, dropper:4, observer:3, wire:2, comparator:1, note_block:1, button:1, sticky_piston:1, copper_bulb:1 | `discord_scraper/data/raw_schematics/1161803566265143306/1460299958295986318_1_Small_Glow-Berry.litematic` |
| 9 | Esthetic 1wt Honey Farm w/ Bottle Recycle (Hipster | discord-raw | 56 | 11x8x1 | comparator:6, observer:5, wire:5, hopper:4, repeater:4, dropper:3, torch:2, crafter:1, dispenser:1, note_block:1, copper_bulb:1 | `discord_scraper/data/raw_schematics/1161803566265143306/1460485035848110275_1_Honey_Farm_w_Recycle_1wt_Hipster.litematic` |
| 10 | Slime Cutter Output Challenge | discord-clean | 89 | 10x4x10 | observer:12, wire:9, sticky_piston:8, piston:6, comparator:5, repeater:4, lever:3, rails:1, dropper:1, note_block:1, target:1 | `discord_scraper/data/clean_schematics/1161803566265143306/1421032229710200854_1_Slime_Cutter_Output.litematic` |
| 11 | ref_obi_al003_thin_accessible_loader | converted/claudestone | 114 | 5x12x3 | hopper:27, observer:12, comparator:9, piston:6, sticky_piston:6, dispenser:3, note_block:3, wire:3, repeater:3 | `data/corpus/converted/claudestone/storage/ref_obi_al003_thin_accessible_loader.litematic` |
| 12 | ref_vktec_al001_accessible_loader | converted/claudestone | 108 | 3x9x7 | hopper:21, observer:21, piston:9, sticky_piston:9, comparator:6, dropper:6, dispenser:3, repeater:3 | `data/corpus/converted/claudestone/storage/ref_vktec_al001_accessible_loader.litematic` |
| 13 | 288k Comparator Duper | discord-clean | 135 | 6x9x15 | wire:42, observer:8, rails:7, sticky_piston:6, note_block:5, piston:4, copper_bulb:4, target:2, lever:1, repeater:1 | `discord_scraper/data/clean_schematics/1161803566265143306/1496321883182727188_1_288k_Comparator_Duper_No_Storage.litematic` |
| 14 | box_loader_2x_accessible | converted/claudestone | 30 | 4x4x3 | wire:6, hopper:4, repeater:2, comparator:1, dispenser:1, redstone_block:1, torch:1, sticky_piston:1 | `data/corpus/converted/claudestone/storage/box_loader_2x_accessible.litematic` |
| 15 | Comparator dupers | discord-clean | 71 | 6x4x4 | sticky_piston:8, wire:7, observer:5, repeater:5, note_block:4, target:4, torch:2, comparator:1, lever:1, redstone_block:1, tripwire_hook:1 | `discord_scraper/data/clean_schematics/1161803566265143306/1483151113862643853_1_36k_Comparator_Duper.litematic` |
| 16 | 3x3 Piston Door | converted/rouge | 63 | 3x10x7 | observer:10, sticky_piston:10, wire:6, dropper:2, lever:2, repeater:2, piston:1 | `data/corpus/converted/rouge/piston-door-3x3.litematic` |
| 17 | corner_cart_unloader_4x | converted/claudestone | 50 | 5x6x6 | wire:12, hopper:5, comparator:2, repeater:2, dispenser:1, piston:1, torch:1 | `data/corpus/converted/claudestone/storage/corner_cart_unloader_4x.litematic` |
| 18 | farm_stall_alarm | converted/claudestone | 110 | 9x3x9 | wire:10, comparator:4, hopper:4, repeater:3, torch:2, dropper:1, note_block:1, lamp:1 | `data/corpus/converted/claudestone/survival/farm_stall_alarm.litematic` |
| 19 | 1p 2d quad wiske farm (~2970/h) 26.1 | discord-raw | 115 | 6x9x9 | dropper:21, observer:18, rails:14, crafter:12, note_block:7, hopper:2, repeater:2, comparator:1, dispenser:1, piston:1, redstone_block:1 | `discord_scraper/data/raw_schematics/1161803566265143306/1503911370029138010_1_18x_Bone_Compactor_Loader.litematic` |
| 20 | dec2to4/cand_comparators | converted/redstone-verify | 27 | 6x2x4 | comparator:5, lamp:4, wire:3, lever:2, torch:1, repeater:1 | `data/corpus/converted/redstone-verify/dec2to4/cand_comparators.litematic` |
