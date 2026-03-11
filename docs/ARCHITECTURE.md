# MIRA Architecture Guide

**Purpose:** Complete reference for understanding MIRA's architecture, data flows, and development roadmap.

**Audience:** New developers, coding agents, collaborators.

---

## Quick Navigation

1. [System Overview](#system-overview)
2. [Current Architecture (Phase 4)](#current-architecture-phase-4)
3. [Future Architecture (Phase 5-6)](#future-architecture-phase-5-6)
4. [Data Pipeline](#data-pipeline)
5. [Component Reference](#component-reference)
6. [Development Phases](#development-phases)

---

## System Overview

MIRA (Minecraft Iterative Reasoning Agent) is an AI system that generates working Minecraft Redstone circuits from text descriptions.

### Core Problem

Standard LLMs struggle with:
- **Spatial reasoning:** Precise 3D coordinates
- **Circuit logic:** Signal flow, timing, dependencies
- **Verification:** No feedback loop to fix errors

### MIRA's Solution: Two-Phase Approach

**Phase 1 (Current - MIRA v1):** Train a one-shot generator with reasoning
- Generate training data: Text description → Block list with per-step reasoning
- Fine-tune Qwen 7B on this data
- Result: Model that generates complete circuits in one call
- **Why start here:** Fast to implement, validates the core idea, low cost (~$100)

**Phase 2 (Future - MIRA v2):** Add iterative verification and repair
- Generate circuit → Build in Minecraft → Test with Scarpet
- If fails: Get error → Generate repair → Retry
- Train on both success traces AND repair traces
- Optionally: RL training with execution feedback
- **Why add this later:** Requires Minecraft server integration, more complex, but handles edge cases better

**Key insight:** Both approaches use the same core infrastructure (Carpet, Scarpet, Replicator). The difference is:
- **v1:** Train on LLM-generated reasoning, no verification at inference
- **v2:** Train on verification loop traces, use verification at inference

If v1 achieves 80%+ success rate in Minecraft, we may not need v2 for many use cases.

---

## Current Architecture (Phase 4)

### What We Have Now (March 2026)

```
Text Description → LLM (Plan+Constraint) → Block List with Reasoning → Training Data
                                              ↓
                                         Fine-tune Qwen 7B
                                              ↓
                                    MIRA v1: One-Shot Generator
```

### Key Components

#### 1. LLM Client (`simulation/llm_client.py`)

**Purpose:** Unified interface to OpenRouter API

**What it does:**
- Calls multiple models (Gemini, GPT-4o, Claude, etc.)
- Enforces JSON schema outputs
- Handles structured responses

**Usage:**
```python
from simulation.llm_client import OpenRouterClient

client = OpenRouterClient(api_key="...")
result = client.complete_with_schema(
    model="gemini-flash-lite",
    prompt="Build a piston door...",
    schema=plan_schema,
    temperature=0.5
)
```

#### 2. Test Circuits (`evaluation/test_circuits/`)

**Purpose:** 12 reference circuits (3-96 blocks)

**What they are:**
- JSON definitions with description, expected blocks, verification steps
- Used to validate LLM generation quality
- Range from beginner (3 blocks) to expert (96 blocks)

**Example:**
```json
{
  "id": "piston_door",
  "description": "Lever-activated door with sticky pistons...",
  "expected_blocks": 15,
  "difficulty": "intermediate"
}
```

#### 3. Validation Suite (`evaluation/`)

**Purpose:** Test LLM generation without Minecraft server

**Key scripts:**
- `test_complex_circuits_strategies.py` - Validates Plan+Constraint (24-96 blocks)
- `ultra_comprehensive_test.py` - 36-test regression suite
- `manual_inspection.py` - Deep qualitative analysis

**What we validated:**
- ✅ 100% block count accuracy (24-96 blocks)
- ✅ Correct block types (manual verification)
- ✅ Logical positions (no overlaps)
- ✅ Working circuits (signal flow verified)

#### 4. Plan+Constraint Strategy

**The winning approach:**

```
Prompt: "Plan this circuit with EXACTLY N blocks..."

Requirements:
- Generate EXACTLY N blocks
- Number each step 1 to N
- Include reason for each block
- List connections to previous blocks
```

**Why it works:**
- Explicit constraint prevents under-generation
- Numbered steps force sequential thinking
- Per-block reasoning = training data value
- 1 API call = fully batchable

**Parameters:**
- Model: `google/gemini-3.1-flash-lite-preview`
- Temperature: 0.5
- Max tokens: 8192 (for 50+ block circuits)

### Current Output Format

```json
{
  "plan": [
    {
      "step": 1,
      "block_type": "minecraft:lever",
      "position": "0,0,0",
      "reason": "Power source for the circuit",
      "connects_to": []
    },
    {
      "step": 2,
      "block_type": "minecraft:redstone_wire",
      "position": "1,0,0",
      "reason": "Connects lever to repeater",
      "connects_to": [1]
    }
  ]
}
```

---

## Future Architecture (Phase 5-6)

### What We're Building Toward

```
┌─────────────────────────────────────────────────────────────┐
│                    MIRA v2: Iterative Agent                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  User: "Build a 4-bit adder"                               │
│       ↓                                                     │
│  ┌────────────────────────────────────────────────────┐    │
│  │  PHASE 1: Planning                                 │    │
│  │  - Break into modules (full adders, carry chain)  │    │
│  │  - Assign bounding boxes                          │    │
│  │  - Define interfaces between modules              │    │
│  └────────────────────────────────────────────────────┘    │
│       ↓                                                     │
│  ┌────────────────────────────────────────────────────┐    │
│  │  PHASE 2: Implementation                           │    │
│  │  For each module:                                  │    │
│  │  1. Generate blocks (fine-tuned model)            │    │
│  │  2. Build in Minecraft (Replicator)               │    │
│  │  3. Test with Scarpet (Verification API)          │    │
│  │  4. If fail → Debug → Retry                       │    │
│  └────────────────────────────────────────────────────┘    │
│       ↓                                                     │
│  ┌────────────────────────────────────────────────────┐    │
│  │  PHASE 3: Integration                              │    │
│  │  - Connect modules                                 │    │
│  │  - Test full circuit                               │    │
│  │  - Optimize (reduce blocks, improve timing)       │    │
│  └────────────────────────────────────────────────────┘    │
│       ↓                                                     │
│  Working circuit in Minecraft                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Components Not Yet Used (But Ready)

#### 1. Minecraft Server (`simulation/server/`)

**Purpose:** Headless Minecraft 1.21 with Carpet Mod

**Capabilities:**
- **Carpet Mod:** Freeze time, control tick rate, spawn fake players
- **Scarpet:** Query world state (signal strength, block states, entity positions)
- **RCON:** Remote command execution for block placement

**Status:** ✅ Set up, but not used in current one-shot approach

**Future use:** Verification loop for iterative agent

#### 2. Replicator (`simulation/replicator.py`)

**Purpose:** Build circuits in Minecraft from block lists

**What it does:**
- Converts block lists to RCON commands
- Handles NBT data (container contents, orientations)
- Manages coordinate transforms
- Prevents RCON packet overflow

**Current status:** ✅ Works (tested with litematics)

**Future use:** Build generated circuits for verification

#### 3. Scarpet Verification API (`simulation/scarpet_scripts/`)

**Purpose:** Test circuits in Minecraft

**Example scripts:**
- `test_redstone.scarpet` - Check signal strength at coordinates
- `test_container.scarpet` - Verify item flow in hoppers
- `test_piston.scarpet` - Confirm piston extension/retraction

**Status:** ✅ Scripts exist, integration tested

**Future use:** Automated testing in iterative loop

#### 4. Bridge (`simulation/bridge.py`)

**Purpose:** Python ↔ Minecraft communication

**What it does:**
- RCON connection management
- Scarpet script execution
- Result parsing

**Status:** ✅ Operational

**Future use:** Verification loop

#### 5. Litematic Parser (`data_mining/parser.py`)

**Purpose:** Parse `.litematic` schematic files

**What it extracts:**
- Block positions and states
- NBT data (container contents, signs)
- Entity placements

**Status:** ✅ Works (47MB reverse dataset generated)

**Future use:** 
- Import existing schematics
- Ground truth for verification

#### 6. Corruptor (`data_mining/corruptor.py`)

**Purpose:** Introduce faults into working circuits

**Fault types:**
- Remove blocks (torches, repeaters, dust)
- Rotate components (wrong facing)
- Change states (repeater delay)

**Status:** ⚠️ Exists but not fully tested

**Future use:** Generate repair training data

---

## Data Pipeline

### Phase 4: Current (One-Shot Training)

```
1. Circuit Description (text)
   ↓
2. LLM with Plan+Constraint
   ↓
3. Block List with Reasoning
   ↓
4. Format as Training Data
   {
     "input": "Build a piston door...",
     "output": [
       {"step": 1, "block": {...}, "reason": "..."},
       {"step": 2, "block": {...}, "reason": "..."}
     ]
   }
   ↓
5. Fine-tune Qwen 2.5 Coder 7B
   ↓
6. MIRA v1: One-shot generator
```

**Cost:** ~$1.50 for 10k circuits + ~$50-100 for fine-tuning

### Phase 5: Future (Iterative with Verification)

```
1. Circuit Description (text)
   ↓
2. MIRA generates blocks
   ↓
3. Build in Minecraft (Replicator)
   ↓
4. Test with Scarpet
   ↓
5. If PASS: Done!
   If FAIL: 
   - Get error from Scarpet
   - Feed to MIRA: "Fix this error..."
   - Generate repair
   - Go to step 3
   ↓
6. Working circuit + full reasoning trace
```

**Training data source:** 
- Success traces (generation → verification)
- Repair traces (error → fix)

### Phase 6: Future (RL from Execution)

```
Reward function:
+10: Circuit works
-5: Each failed verification
-1: Each extra block (encourage efficiency)
+2: Correct signal timing

Training:
- Policy: MIRA model
- Environment: Minecraft server
- Actions: Place blocks
- Observations: World state + test results
```

---

## Component Reference

### Directory Structure

```
mira/
├── simulation/                   # Minecraft integration
│   ├── bridge.py                 # RCON + Scarpet interface
│   ├── replicator.py             # Build engine
│   ├── llm_client.py             # OpenRouter API (ACTIVE)
│   ├── scarpet_scripts/          # Verification tests
│   │   ├── test_redstone.scarpet
│   │   └── ...
│   └── server/                   # Headless server (gitignored)
│
├── data_mining/                  # Schematic tools
│   ├── parser.py                 # Litematic → Python
│   └── corruptor.py              # Fault injection
│
├── evaluation/                   # Testing infrastructure (ACTIVE)
│   ├── test_api.py
│   ├── quick_test.py
│   ├── test_complex_circuits_strategies.py  # Main validation
│   ├── test_batch_strategies.py
│   ├── ultra_comprehensive_test.py
│   ├── manual_inspection.py
│   ├── analyze_results.py
│   ├── test_circuits/            # 12 circuit definitions
│   ├── results/                  # Test output
│   └── archive/                  # Deprecated scripts
│
├── data/                         # Data storage
│   ├── raw_schematics/           # 13 test litematics
│   └── training/                 # 47MB reverse dataset
│
├── docs/                         # Documentation
│   ├── ARCHITECTURE.md           # This file (COMPLETE REFERENCE)
│   ├── TESTING_REPORT.md         # Validation results
│   ├── PAST_ATTEMPTS.md          # Historical context
│   └── archive_technical_design_original.md  # Original vision (archived)
│
└── README.md                     # Project overview
```

### Key Files to Read

**For understanding the current system:**
1. `README.md` - Project overview
2. `docs/ARCHITECTURE.md` - This file (system design)
3. `docs/TESTING_REPORT.md` - What we validated
4. `simulation/llm_client.py` - API interface
5. `evaluation/test_complex_circuits_strategies.py` - Main test

**For understanding future capabilities:**
1. `simulation/bridge.py` - Minecraft communication
2. `simulation/replicator.py` - Building circuits
3. `simulation/scarpet_scripts/` - Verification tests
4. `data_mining/parser.py` - Litematic parsing
5. `docs/archive_technical_design_original.md` - Original vision (archived, for historical context)

---

## Development Phases

### ✅ Phase 1: Infrastructure (Complete)

**What was built:**
- Minecraft server orchestration
- RCON bridge for block placement
- Carpet Mod integration

**Key files:**
- `simulation/bridge.py`
- `start_server.sh`

### ✅ Phase 2: Data Factory (Complete)

**What was built:**
- Litematic parser
- Replicator for building schematics
- Coordinate normalization

**Key files:**
- `data_mining/parser.py`
- `simulation/replicator.py`

### ✅ Phase 3: Verification (Complete)

**What was built:**
- Scarpet API for state inspection
- Integration tests
- Signal strength queries

**Key files:**
- `simulation/scarpet_scripts/test_redstone.scarpet`
- `simulation/tests/test_integration.py`

### ✅ Phase 4: Reasoning Data (Complete - Testing Validated)

**What was done:**
- Tested 4 generation strategies on 42 circuits
- Validated Plan+Constraint achieves 100% accuracy
- Manually verified circuit correctness
- Ready to generate 10k training circuits

**Key findings:**
- Plan+Constraint works (100% on 24-96 block circuits)
- Cost: ~$1.50 for 10k circuits
- Batchable with vLLM (1 API call per circuit)

**Key files:**
- `docs/TESTING_REPORT.md`
- `evaluation/test_complex_circuits_strategies.py`
- `simulation/llm_client.py`

### 🔄 Phase 5: Training (Next)

**What's next:**
1. Generate 10k circuits with Plan+Constraint
2. Format as training data
3. Fine-tune Qwen 2.5 Coder 7B
4. Validate fine-tuned model
5. Build 10-20 circuits in Minecraft for verification

**Expected timeline:** 1-2 weeks
**Expected cost:** ~$52-102

### 📋 Phase 6: Deployment (Planned)

**What's planned:**
1. Deploy MIRA v1 (one-shot generator)
2. Implement verification loop (use Scarpet)
3. Add repair capability (use Corruptor for training)
4. RL training with execution feedback
5. Full iterative agent

---

## How to Get Started

### As a New Developer

1. **Read these in order:**
   - `README.md` - What is MIRA?
   - `docs/ARCHITECTURE.md` - How does it work? (this file)
   - `docs/TESTING_REPORT.md` - What's validated?

2. **Run the tests:**
   ```bash
   cd evaluation
   python3 test_api.py              # Verify API works
   python3 quick_test.py            # Quick quality check
   ```

3. **Understand the current approach:**
   - Read `evaluation/test_complex_circuits_strategies.py`
   - See how Plan+Constraint works
   - Check results in `evaluation/results/`

4. **Explore future components:**
   - Start server: `bash start_server.sh`
   - Look at Scarpet scripts: `simulation/scarpet_scripts/`
   - Try building a litematic: `python3 scripts/replicate_schematic.py ...`

### As a Coding Agent

**To understand the full project:**

1. Read `docs/ARCHITECTURE.md` (this file) - Complete system overview
2. Read `docs/TESTING_REPORT.md` - What works, what doesn't
3. Read `docs/PAST_ATTEMPTS.md` - Historical decisions
4. Read `simulation/llm_client.py` - Current active code
5. Read `evaluation/test_complex_circuits_strategies.py` - Validation logic

**To implement Phase 5 (training):**
1. Study Plan+Constraint prompt in `test_complex_circuits_strategies.py`
2. Generate 10k circuits using `llm_client.py`
3. Format as training data (input: description, output: blocks + reasoning)
4. Fine-tune Qwen 2.5 Coder 7B
5. Validate with `evaluation/` scripts

**To implement Phase 6 (iterative agent):**
1. Study `simulation/bridge.py` - Minecraft communication
2. Study `simulation/replicator.py` - Building circuits
3. Study `simulation/scarpet_scripts/` - Verification
4. Implement loop: generate → build → test → repair
5. Add RL training with execution feedback

---

## Glossary

| Term | Definition |
|------|------------|
| **Litematic** | `.litematic` file format for Minecraft schematics |
| **Carpet Mod** | Minecraft mod for server control and scripting |
| **Scarpet** | Carpet's built-in scripting language |
| **RCON** | Remote Console - protocol for server commands |
| **Plan+Constraint** | Winning generation strategy (explicit "EXACTLY N blocks") |
| **Reasoning Trace** | Step-by-step explanation of circuit construction |
| **One-shot** | Generate entire circuit in single LLM call |
| **Iterative** | Generate → test → repair loop |
| **vLLM** | High-throughput LLM inference library |

---

## Quick Reference

### Current Status (March 2026)

- ✅ Testing complete: Plan+Constraint validated on 42 circuits
- ✅ Infrastructure ready: Server, bridge, replicator all work
- ⏳ Next: Generate 10k circuits, fine-tune Qwen 7B
- 📋 Future: Iterative agent with verification loop

### Key Metrics

- **Block count accuracy:** 100% (24-96 blocks)
- **Block type correctness:** 100% (manual verification)
- **Cost for 10k circuits:** ~$1.50
- **Fine-tuning cost:** ~$50-100

### Best Practices

1. Use Plan+Constraint for circuits <100 blocks
2. Use `google/gemini-3.1-flash-lite-preview` model
3. Set temperature=0.5, max_tokens=8192
4. Validate block count after generation
5. Retry if <90% of expected blocks

---

*Last updated: March 11, 2026*  
*Phase 4 complete. Ready for Phase 5 (training).*
