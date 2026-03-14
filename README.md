# MIRA: Minecraft Iterative Reasoning Agent

MIRA is an AI framework designed to generate, build, and debug Minecraft Redstone circuits from text descriptions. Unlike standard generative models that struggle with strict spatial logic, MIRA uses an iterative approach combining hierarchical planning, real-time simulation, and automated verification.

> **Status:** Work in Progress. The pre processing testing is done, the next steps are data scraping and training.
> **Latest Update:** March 11, 2026

## Quick Start

### Prerequisites
- **Python 3.10+**
- **Java 21** (for Minecraft 1.21 server)
- **OpenRouter API key** (for LLM generation)

### Setup

```bash
# 1. Clone and install dependencies
git clone <repo>
cd mira
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Set up Minecraft server
python setup.py

# 3. Start server
bash start_server.sh
```

## Core Features

- **Hierarchical Planning:** Breaks complex circuits into modules with spatial constraints
- **Headless Simulation:** Fabric-based Minecraft server with Carpet Mod for verification
- **Automated Replicator:** Python-to-Minecraft bridge via RCON for block placement
- **Verification Engine:** Scarpet scripts for real-time redstone signal validation

## Architecture

```
mira/
├── simulation/           # Minecraft integration
│   ├── bridge.py         # RCON communication
│   ├── replicator.py     # Build engine
│   ├── llm_client.py     # OpenRouter API client
│   ├── scarpet_scripts/  # Verification API
│   └── server/           # Headless server (gitignored)
├── data_mining/          # Schematic tools
│   ├── parser.py         # Litematic parser
│   └── corruptor.py      # Fault injection
├── evaluation/           # Testing infrastructure
│   ├── test_*.py         # Test scripts
│   ├── test_circuits/    # Circuit definitions
│   └── results/          # Test results
├── docs/                 # Documentation
│   ├── TESTING_REPORT.md     # Full testing results
│   └── PAST_ATTEMPTS.md      # Historical context
└── data/                 # Data storage
    ├── raw_schematics/   # Test schematics
    └── training/         # Training datasets
```

**See `docs/TESTING_REPORT.md` for full details of validation testing.**

## Usage

### Generate Training Data

```python
from simulation.llm_client import OpenRouterClient, ChatMessage

client = OpenRouterClient(api_key="your-key")

# Plan+Constraint prompt (recommended)
prompt = f"""Plan this circuit with EXACTLY {expected_blocks} blocks:

{circuit_description}

REQUIREMENTS:
- Generate EXACTLY {expected_blocks} blocks
- Number each step 1 to {expected_blocks}
- Include reason for each block
- List connections to previous blocks"""

response = client.complete_with_schema(
    model="gemini-flash-lite",
    prompt=prompt,
    system_prompt="Plan redstone circuits step-by-step.",
    schema=plan_schema,
    temperature=0.5
)
```

### Build Circuit in Minecraft

```python
from simulation.replicator import Replicator

replicator = Replicator()
replicator.load_schematic("path/to/circuit.litematic")
replicator.build_at(x=0, y=64, z=0)
```

### Verify Circuit

```python
from simulation.bridge import RCONBridge

bridge = RCONBridge()
result = bridge.run_scarpet("scarpet_scripts/test_redstone.signal_strength(x, y, z)")
```

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1. Infrastructure | ✅ Complete | RCON bridge, server orchestration |
| 2. Data Factory | ✅ Complete | Litematic parsing, replicator |
| 3. Verification | ✅ Complete | Scarpet API, integration tests |
| 4. Reasoning Data | ✅ Complete | Testing validated, ready to generate |
| 5. Training | 🔄 Next | Fine-tune Qwen 7B on generated data |
| 6. Deployment | 📋 Planned | Production MIRA agent |

## Next Steps

1. **Generate 10k training circuits** (~$1.50, ~5 minutes with vLLM)
2. **Fine-tune Qwen 3.5 9B** (~$50-100)
3. **Validate in Minecraft** (build 10-20 circuits, expect 80%+ success)
4. **Deploy MIRA inference loop** (generate → build → test → repair)

## Documentation

**To get started with development:**

1. **`docs/ARCHITECTURE.md`** - Complete system reference (READ FIRST)
   - Current architecture (one-shot training)
   - Future architecture (iterative with verification)
   - All components explained
   - Development phases and roadmap
   - How to get started

2. **`docs/TESTING_REPORT.md`** - What we validated, key findings
3. **`docs/PAST_ATTEMPTS.md`** - Historical context, what we tried

**Testing infrastructure:**
- **`evaluation/README.md`** - How to run tests

**Archive (reference only):**
- **`docs/archive_technical_design_original.md`** - Original vision (superseded by ARCHITECTURE.md)

## API Configuration

The project uses OpenRouter API. You must set your API key as an environment variable:

```bash
export OPENROUTER_API_KEY="your-key-here"
```


**Recommended model:** `google/gemini-3.1-flash-lite-preview` (best price/performance)

## License

MIT

---

*Last updated: March 11, 2026*  
*Testing phase complete. Ready for data scraping and training.*
