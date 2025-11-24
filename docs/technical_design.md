# Technical Design Document: Project MIRA

## 1\. Executive Summary

**MIRA** is an AI system designed to generate, debug, and optimize Minecraft Redstone circuits. Unlike traditional "text-to-schematic" models which attempt to generate a finished product in one pass (often hallucinating logic), MIRA operates as an **Iterative Agent**.

It utilizes a **Hierarchical Planning Architecture** to break complex circuits into modules, a **Headless Simulation Environment** (Carpet Mod) to verify physics, and a **"Bedrocking" Workflow** to freeze working components while debugging others. The model is trained via **Reinforcement Learning from Execution Feedback (RLEF)**, using a synthetic dataset of "broken-to-fixed" circuit trajectories.

## 2\. The "Meta" Ecosystem & Tools

To build the training environment, we utilize specific domain tools.

### A. Data Sources: The Discord Crawler

Technical Minecraft knowledge is concentrated in Discord servers.

  * **Target Servers:** The Redstone Archive, Storage Tech, SciCraft, TMC (Technical Minecraft Community).
  * **Crawler Logic:**
      * Connects via Discord API.
      * Scrapes channels for attachments ending in `.litematic` or `.schem`.
      * **Context Extraction:** Captures message content to categorize builds (e.g., "fast," "compact," "shulker loader").
  * **Output:** A database linking `schematic_file` \<-\> `natural_language_description` \<-\> `metadata_tags`.

### B. The Standard: Litematica

  * **What it is:** The modern standard for sharing Minecraft structures. It uses a custom NBT format to store 3D block data, states (e.g., `facing=north`, `delay=2`), and sub-regions.
  * **The Library:** We use `litemapy` (Python) to parse these binary files into NumPy arrays or Python objects.

### C. The Simulator: Carpet Mod & Scarpet

  * **Carpet Mod:** A Fabric mod allowing deep control over the game engine.
      * **Key Capabilities:** `/tick rate` (speed up training), `/tick freeze` (stop time), and `/player` (spawn fake players).
  * **Scarpet:** A scripting language built into Carpet Mod.
      * **Role:** Our API layer. We write Scarpet scripts (`.sc`) to load schematics and verify block states.
  * **Headless Server:** A Minecraft server running with `-nogui`, allowing parallel instances for training.

## 3\. The Model's "Mental" Output

MIRA outputs a **Structured Engineering Plan**, not just raw blocks. It operates in two modes:

### A. The Architect Mode (Manifest Generation)

The model breaks a prompt into a spatial plan, claiming bounding boxes for modules.

  * **User Prompt:** "Build a 3x3 Piston Door."
  * **Output (JSON):**
    ```json
    {
      "project": "3x3_piston_door",
      "modules": [
        {
          "id": "module_A",
          "type": "double_piston_extender",
          "bounds": {"p1": [1, 0, 1], "p2": [3, 2, 1]},
          "description": "Push the center block up two spaces"
        }
      ]
    }
    ```

### B. The Contractor Mode (Python Code Generation)

The model generates Python code to build one module at a time, using Decorators for constraints and Assertions for physics.

  * **Output (Python):**
    ```python
    @RedstoneModule(id="module_A", bounds=((1,0,1), (3,2,1)))
    def build_double_extender(ctx: TestContext):
        # Placement
        ctx.set_block((1, 0, 0), "minecraft:sticky_piston[facing=up]")
        # Instrumentation (The Contract)
        ctx.assert_state(tick=3, pos=(1, 2, 0), state="minecraft:piston_head")
        return ctx
    ```

## 4\. The Data Pipeline (Synthetic Training)

We train MIRA to **finish and fix** schematics. This involves a pipeline to generate "Reasoning Traces" (Chain-of-Thought) alongside the code fixes.

#### Phase 4: Dataset Generation (The "Reasoning" Factory)

**Task 4.1: The Contract Generator (The "Golden Run")**
We must first establish what the schematic *does* before we break it.

  * **Input:** Valid Schematic (Python representation) + Discord Metadata.
  * **Action:** Teacher Model analyzes the blocks to identify Input/Output.
  * **Output:** `verify_schematic.py` (The Test Contract).
  * **Validation:** **Crucially**, we run this test against the *Working* schematic in the Simulator.
      * *If Pass:* Contract is valid. Proceed to corruption.
      * *If Fail:* Teacher hallucinated. Discard schematic.

**Task 4.2: The Mechanical Corruptor (The "Breaker")**
A Python script that takes the valid code and creates "In-Progress" states.

  * **Level 1:** Randomly delete 1 logic component (Torch, Repeater).
  * **Level 2:** Delete a 3x3x3 volume (simulating an unfinished build).
  * **Level 3:** Alter properties (`delay=4` -\> `delay=1`).
  * **Validation:** Run the `verify_schematic.py` against this broken code.
      * *If Fail:* Good corruption. Keep it.
      * *If Pass:* Corruption was cosmetic (didn't break logic). Discard.

**Task 4.3: The Teacher Pipeline (The "Mind" Generator)**
We use a high-intelligence model (e.g., GPT-4o) to synthesize the training examples.

  * **Framing:** Do not frame this as "Fixing a bug." Frame it as **"Completing an unfinished engineering task."**
  * **Input:** The `broken_code` (Context), `runtime_error.log` (Evidence), and `working_code` (Goal).
  * **Output:** A structured training entry following the **Contract-First** token order.

**Target Output Format:**

```text
[CONTEXT]
Name: Compact 3x3 Door
Desc: Fast, observer-based design.
Current State: (The broken/partial code)

[MODEL_OUTPUT_START]
<TEST_CONTRACT>
# The model defines success FIRST
def verify_door(ctx):
    ctx.power((0,1,0))
    ctx.tick(20)
    assert ctx.is_block((3,2,1), "stone")
</TEST_CONTRACT>

<THOUGHT>
I have defined the test. Looking at the partial state, I see the input is connected but the bottom pistons are missing power.
I'll try standard dust wiring...
(Simulation: Fail -> "AssertionError: Block not pushed")
That didn't work. I need to handle the update order. I'll use an observer.
</THOUGHT>

<CODE>
# The Final Working Code
ctx.set_block(...)
</CODE>
[MODEL_OUTPUT_END]
```

## 5\. The Runtime Architecture (Inference Loop)

### A. The Bridge (Python \<-\> Minecraft)

1.  Model writes `script_v1.py`.
2.  Bridge parses schematic using custom Replicator (Python).
3.  Bridge sends RCON commands: `/setblock`, `/summon`.
4.  Bridge triggers test: `/script run verify_module()`.

### B. The "Bedrocking" (Checkpoint) System

1.  **Attempt:** Build Module A.
2.  **Test:** PASS.
3.  **Action:** Mark Module A coordinates as **"Bedrock"** (Immutable).
4.  **Next Step:** Build Module B. Agent can connect to A but cannot break it.

## 6\. Implementation Roadmap

### Phase 1: Infrastructure & "Hello World" (Completed)

  * Task 1.1: Local Infrastructure.
  * Task 1.2: Write `bridge.py`.

### Phase 2: The Data Factory & Replicator (Completed)

  * Task 2.1: Litematic Parser.
  * Task 2.2: The Replicator.

### Phase 3: The Simulator (Scarpet API) (Completed)

  * Task 3.1: Scarpet Setup.
  * Task 3.2: API Implementation.
  * Task 3.3: Integration.

### Phase 4: Dataset Generation (Current Focus)

  * **Task 4.1:** The Contract Generator (Golden Run Validation).
  * **Task 4.2:** The Mechanical Corruptor (Fault Injection).
  * **Task 4.3:** The Teacher Pipeline (Reasoning Trace Generation).

### Phase 5: Model Training (Future)

  * Task 5.1: SFT on Corrupted Dataset.
  * Task 5.2: RL Gym Environment.

## 7\. Directory Structure

```text
/mira
  /docs                 # Save this TDD here
  /data_mining
    crawler.py          # Discord scraper
    parser.py           # Litematic -> Python converter
    corruptor.py        # Generates broken/fixed training pairs
  /simulation
    server/             # The Minecraft Server instance (GitIgnored)
    bridge.py           # RCON/File bridge to server
    replicator.py       # Robust schematic builder
    test_generator.py   # LLM Client for creating contracts
    dataset_generator.py # Main orchestration script
    scarpet_scripts/    # In-game API scripts (.sc files)
  /agent
    model.py            # LLM Interface
    planner.py          # The "Architect"
    executor.py         # The "Contractor"
  /training
    dataset_loader.py
    fine_tune.py
```
