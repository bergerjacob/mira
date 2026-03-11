# MIRA Evaluation Framework

Comprehensive testing infrastructure for evaluating LLM performance on Minecraft redstone circuit generation tasks.

## Quick Start

### Test API Connectivity

```bash
cd /home/bergerj/main/personal/minecraft-dev/mira
source .venv/bin/activate
python3 evaluation/test_api.py
```

### Run Quick Quality Test (No Server Required)

```bash
python3 evaluation/quick_test.py
```

### Run Full Validation Suite

```bash
# Test Plan+Constraint on complex circuits (24-96 blocks)
python3 evaluation/test_complex_circuits_strategies.py

# Run comprehensive 36-test suite
python3 evaluation/ultra_comprehensive_test.py
```

### Analyze Results

```bash
python3 evaluation/analyze_results.py
```

## Directory Structure

```
evaluation/
├── test_api.py                        # API connectivity tests (KEEP)
├── quick_test.py                      # Fast quality test, no server (KEEP)
├── test_complex_circuits_strategies.py # Main validation - Plan+Constraint (KEEP)
├── test_batch_strategies.py           # Strategy comparison (KEEP)
├── ultra_comprehensive_test.py        # 36-test comprehensive suite (KEEP)
├── manual_inspection.py               # Qualitative verification (KEEP)
├── analyze_results.py                 # Post-processing analysis (KEEP)
├── test_circuits/                     # 12 circuit definitions (3-96 blocks)
│   ├── simple_lamp.json
│   ├── piston_door.json
│   ├── 4bit_adder.json
│   └── ...
├── results/                           # Test output
│   ├── ultra_comprehensive/
│   ├── batch_strategies/
│   ├── complex_circuit_strategies/
│   └── manual_inspection_piston_door.json
└── archive/                           # Deprecated scripts (reference only)
    ├── benchmark.py
    ├── test_iterative_generation.py
    └── ...
```

## Active Test Scripts

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `test_api.py` | Verify API connectivity | Debugging, setup validation |
| `quick_test.py` | Fast quality check | Quick iteration, no server needed |
| `test_complex_circuits_strategies.py` | Validate Plan+Constraint on 24-96 block circuits | **Primary validation** |
| `test_batch_strategies.py` | Compare generation strategies | Research, strategy tuning |
| `ultra_comprehensive_test.py` | 36 tests across 12 circuits × 3 temps | Full regression testing |
| `manual_inspection.py` | Deep qualitative analysis | Verify circuit correctness |
| `analyze_results.py` | Generate statistics from results | Post-test analysis |

## Test Circuits

12 circuits ranging from 3 to 96 blocks:

| Circuit | Blocks | Difficulty |
|---------|--------|------------|
| simple_lamp | 3 | Beginner |
| hopper_transporter | 3 | Beginner |
| power_repeater | 6 | Beginner |
| observer_torch | 4 | Beginner |
| comparator_subtractor | 8 | Intermediate |
| piston_door | 15 | Intermediate |
| t_flip_flop | 24 | Expert |
| randomizer | 32 | Advanced |
| item_sorter | 54 | Expert |
| elevator | 64 | Advanced |
| automatic_farm | 87 | Expert |
| 4bit_adder | 96 | Expert |

## Adding New Test Circuits

Create a JSON file in `evaluation/test_circuits/`:

```json
{
  "id": "my_circuit",
  "name": "My Circuit",
  "description": "What this circuit does...",
  "difficulty": "beginner|intermediate|advanced|expert",
  "expected_blocks": 10,
  "hints": ["Optional hint 1", "Optional hint 2"],
  "verification": {
    "initial_state": "Description",
    "action": "What to do",
    "expected_result": "What should happen"
  }
}
```

## Archived Scripts

See `evaluation/archive/README.md` for deprecated scripts.

These represent **tested but rejected approaches**:
- Iterative generation (1 block per call)
- Reasoning trace generation from complete circuits
- Server-based benchmarking

**Do not use these for active development.** Findings are documented in:
- `docs/TESTING_REPORT.md` - Results summary
- `docs/PAST_ATTEMPTS.md` - Why approaches were rejected

## API Configuration

Uses OpenRouter API. Set your key in scripts or via environment:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

**Recommended model:** `google/gemini-3.1-flash-lite-preview`

## Test Results

All results saved to `evaluation/results/`:

- `ultra_comprehensive/` - 36-test suite results
- `batch_strategies/` - Strategy comparison
- `complex_circuit_strategies/` - Complex circuit validation
- `manual_inspection_piston_door.json` - Verified circuit

## Key Findings

**Plan+Constraint Strategy** (validated March 11, 2026):

- ✅ 100% accuracy on circuits 24-96 blocks
- ✅ 1 API call per circuit (batchable)
- ✅ ~$1.50 for 10k circuits
- ✅ Correct block types and positions (manual verification)

**Prompt:**
```
Plan this circuit with EXACTLY {N} blocks:

{description}

REQUIREMENTS:
- Generate EXACTLY {N} blocks
- Number each step 1 to {N}
- Include reason for each block
- List connections to previous blocks
```

**Parameters:**
- Model: `google/gemini-3.1-flash-lite-preview`
- Temperature: 0.5
- Max tokens: 8192 (for 50+ block circuits)

See `docs/TESTING_REPORT.md` for full details.

## Troubleshooting

### "Module not found" errors
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### API errors
- Check API key is valid
- Verify budget not exhausted
- Check network connectivity

### JSON parsing errors
- Some models put JSON in `reasoning` field
- `llm_client.py` handles this automatically

---

*Last updated: March 11, 2026*  
*Testing phase complete. Production ready.*
