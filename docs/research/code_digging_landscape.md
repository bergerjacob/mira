# Code digging for technical Minecraft: landscape brief

Date: 2026-11-02. Scope: what TMC "code digging" looks like, what tooling exists, and which Lean/code-search ideas transfer to a
tree-search agent that uses the real game as its checker. Written by a fast-tier research pass.

**Verification legend:** `[V]` = read in a fetched or search-returned source this session. `[U]` = unverified (from memory or
not reached). Search attempts that failed are listed in section 7.

## 0. Bottom line

- The TMC bug-to-feature pipeline is public and well-documented (Mojira, Minecraft Wiki, Reddit). Quasi-connectivity (QC) is the
  canonical example: a Mojira-reported behaviour that Mojang kept as "works as intended" `[V]`.
- Redstone quirks are found by reading the game code and by brute-force testing in-game, then published as minimal repros.
  Automated, LLM-driven code digging on Minecraft is **sparse** in the sources I could reach. One 2025 paper (SICE) and one
  2026 benchmark (SciCrafter) use redstone as a task, not as a bug-finding target `[V]`.
- The closest working analogue for our setup is **Lean-style tree search with a deterministic checker**, plus **FunSearch /
  AlphaEvolve-style evaluator loops**. Both are documented `[V]`. The vanilla oracle (`tools/oracle/`) is already a checker.
- Legal and ethical risk is mainly about **redistributing decompiled Mojang code** and **publishing exploits that crash or
  grief public servers**. Single-player contraption quirks are low concern.

## 1. How TMC people find bugs and quirks

### 1.1 Methods
- **Decompiled source reading.** Mojang mappings (official names released by Mojang) are the common base for decompiling
  Minecraft Java Edition. Tools: `hube12/DecompilerMC` (remaps via ProGuard-to-TSRG, SpecialSource, CFR/Fernflower) `[V]`,
  `ThreadMC/decompile-tools` `[V]`. Yarn and Parchment (Fabric-side names) are `[U]` in this session; I did not fetch their docs.
- **Brute-force in-game testing.** Build a minimal circuit, tick it, observe. Most quirks are found this way. `[U]` for the
  history of specific examples beyond QC.
- **Mojira reports.** Mojira (`bugs.mojang.com/browse/MC`) is the public tracker. Example cited in search: MC-80851 about
  quasi-connectivity across multiple pistons `[V]`.
- **Carpet mod introspection.** Carpet (`gnembon/fabric-carpet`) adds `/tick warp`, hopper counters and a `commandLog` setting
  (the `/log` command), plus `/script` (Scarpet) with `block(...)` and world access `[V]`. Carpet is a Fabric mod for Java,
  so it observes the running game; it is not a simulator.

### 1.2 Mechanics that show up repeatedly in TMC
- **Quasi-connectivity** (pistons, droppers, dispensers respond to power above them): officially "works as intended" per the
  Minecraft Wiki tutorial; the Reddit thread shows it is still contested `[V]`.
- **BUD switches** and **observer clocks**: explained as QC exploitation and observer timing `[V]` (syntaxmine article, 1.21.4 era).
- **Update suppression**, **zero-tick** behaviour, **dropper/observer ordering**, **chunk-loading** and **entity cramming**:
  named in the brief and in community usage `[U]`. I did not fetch primary repros for these in this pass.
- Known simulator divergences recorded in this repo (`docs/ORACLE.md`, `docs/ENV.md`): comparator with lever side input,
  dropper-into-dropper false trigger, torch-burnout clock `[V: repo docs]`.

### 1.3 Who publishes
- **Minecraft Wiki** (tutorials, mechanics pages) `[V]`.
- **Mojira** (bug reports and resolutions) `[V]`.
- **Reddit r/redstone** (discussion, including the QC "feature or bug" debate) `[V]`.
- **Technical Minecraft YouTube channels, Discord archives, Carpet/Fabric/Paper communities**: these are the intended sources
  for the brief; I did not retrieve them `[U]`.

### 1.4 Typical deliverable
- A **minimal world or schematic** that reproduces the behaviour, a short explanation, and the **versions affected** (Java
  version or snapshot). This maps directly onto our `tools/oracle` capture plus a `Plan` (block list + ticks) format.

## 2. Version-specific mechanic changes

- **Changelogs and fix notices:** Mojang publishes patch notes and snapshot notes; bug resolutions appear on Mojira with a
  "Fixed in" field `[U]` for the field name, `[V]` for Mojira existing.
- **Decompiled-source diffs:** possible by decompiling two versions with the same mappings and diffing. `[U]` that Mojang mappings
  exist for each release (the Mappings site lists per-version licences `[V]`: mappings.dev/1.21.1/licenses.html).
- **Machine-readable datasets of version differences:** I did **not** find one. Mojira is the closest and it is not an official
  export. Treat "no such dataset found" as unverified for the whole web.
- **Frequency of redstone changes:** not measured here `[U]`. Anecdotally, redstone-relevant behaviour changes a few times per
  major release, but I have no source. Our own repo notes simulator/oracle disagreements, which are a concrete starting point
  for a version-diff experiment.

## 3. Automated approaches

### 3.1 Differential testing and simulators
- **MCHPRS** (Rust; redpiler optimises redstone; baseline correctness-focused) `[V]` (DeepWiki page).
- **Nucleation** (Rust/Python; includes a tick simulator, `TickSimulation`) `[V]` (github Schem-at/Nucleation). This repo pins it.
- **Differential testing between MCHPRS, Nucleation and the vanilla oracle**: the obvious pattern, and this repo already does
  it (`scripts/oracle_diff.py`) `[V: repo docs]`. I found no published study doing this at scale `[U]`.

### 3.2 GameTest
- Mojang's **GameTest framework** is documented for Bedrock (`minecraft.net` creator article, Microsoft Learn) `[V]`, and the
  Wiki page describes it as an E2E test system with structure + parameters + code `[V]`.
- For **Java**, the 26.2 jar's in-process GameTest server is what `tools/oracle` already uses `[V: repo docs]`. I did not
  verify the Java-side public API from Mojang docs `[U]`.

### 3.3 LLM-based Minecraft work
- **SciCrafter** (arXiv 2604.24697, 2026): a benchmark of parameterised redstone-circuit tasks (ignite lamps in patterns)
  for agents, testing the discovery-to-application loop `[V]`. Closest match to our task family; it asks agents to build,
  not to find bugs.
- **SICE 2025** paper on automated redstone circuit configuration with a multimodal model `[V]` (title/abstract only;
  method and numbers `[U]`).
- **Ghost in the Minecraft** (arXiv 2305.17144) is a general LLM agent for Minecraft, not for bugs `[V]`.
- **LLM agents reading decompiled Minecraft source to find bugs**: I found **no** paper or repo in this session `[U: search
  returned no parseable results]`. Treat as "not found, not ruled out."
- **LLM fuzzing of game engines / differential redstone testing with LLMs**: not found in this session `[U]`.

## 4. Lean and code-search analogues worth borrowing

| system | what it does | verified fact | design lesson for us |
|---|---|---|---|
| DeepSeek-Prover-V1.5 (ICLR 2025) | Online RL with Lean verification outcomes as reward; RMaxTS, an MCTS variant with intrinsic-reward exploration; tree over Lean tactic states from compiler messages | `[V]` | Use the checker's output as the state and as the reward; intrinsic reward keeps the tree diverse |
| Goedel-Prover-SFT (arXiv 2502.07640) | Generates the whole proof at once, **without feedback** | `[V]` | Whole-program sampling without feedback is the baseline to beat |
| Seed-Prover | Whole-proof reasoning with iterative refinement from Lean feedback, proved lemmas, and self-summaries | `[V]` (IMO 2025 claim from a secondary source `[U]`) | Keep a library of verified lemmas (our component library) and summarise failures |
| AlphaProof Nexus (arXiv 2605.22763, 2026) | Subagents search independently with Lean compiler feedback | `[V]` | Parallel subagents with a shared checker map onto our harness |
| FunSearch (Nature 2023) | LLM proposes programs; automated evaluator scores them; evolve population | `[V]` (paper and DeepMind blog) | An evaluator is the only trusted component; the oracle plays this role |
| AlphaEvolve (DeepMind, 2025; GA on Google Cloud) | Gemini-based coding agent with automated evaluators, staged validate/verify/evaluate gates | `[V]` (blog and Cloud docs) | The staged gate (validation -> verification -> evaluation) is directly reusable: cheap structural checks before the expensive oracle |
| Big Sleep (Project Zero / DeepMind) | LLM agent found an exploitable stack buffer underflow in SQLite (Nov 2024) | `[V]` | Agents can find real bugs in mature codebases when given tooling and a reproducer loop |

**Numbers I could not verify this session (`[U]`):** search budgets, verifier cost per call, pass@k curves, and miniF2F/ProofNet
rates for these systems. Do not quote numbers from this brief without checking the cited papers.

### Design transfers for a TMC code-digging tree
1. **Node = (hypothesis, minimal build, expected trace).** The "tactic state" analogue is the oracle trace at the divergent tick.
2. **Checker = `tools/oracle` capture + `first_divergence`.** Already fast (~200 captures/s on 16 cores per the repo) `[V: repo docs]`.
3. **Staged gates** (AlphaEvolve pattern): schema/validity check -> Nucleation simulation -> vanilla oracle only for survivors.
4. **Reward for exploration** (RMaxTS idea): reward new trace shapes, not only "divergent", to avoid re-finding the same quirk.
5. **Whole-program baseline** (Goedel-style): one-shot builds with no feedback are the control for measuring iteration lift.
   The repo's Experiment 01 already measures this shape `[V: repo docs]`.

## 5. Legal and ethical notes

### 5.1 Decompiled code and mappings
- Mojang's EULA governs use and modification of Minecraft Java Edition code and the mappings `[V]` (mappings.dev licences page
  and Reddit/Minecraft Wiki discussion).
- The Minecraft Wiki's "Obfuscation map" page states that decompiled source may be distributed only **in a modified version or
  as part of a larger project** `[V]` (quoting the wiki, which paraphrases the licence; read the EULA text before relying on it).
- **Practical rule for this repo:** keep decompiled sources under gitignored paths (as `data/external/oracle_work/` already does)
  and never commit or publish them. Publish only our own repros and our own explanations, plus the jar SHA-1 for provenance.
  This matches the existing jar policy in `docs/ORACLE.md` `[V: repo docs]`.
- Mojang's Commercial Usage Guidelines and any terms on publishing **videos or tools** derived from the game were not checked `[U]`.

### 5.2 Publishing exploits
- **Single-player contraption quirks:** low concern. They change only the player's own world.
- **Server crash or griefing exploits:** higher concern. Publishing working crash methods for public servers can enable abuse.
  Standard practice in the community is to report to Mojira and to give repros that do not give a ready-made griefing or
  crash tool. Our policy should follow that: report to Mojang first, publish mechanics and repros, withhold server-exploit
  payloads. `[U]` for any formal Mojang disclosure policy; I did not find one.
- The agent should not produce working server-exploit tooling without a clear security-research context. Single-player and
  redstone-simulation quirks sit outside that concern.

## 6. Recommended next steps (for the parent, not done here)
1. Pick 3 known quirks with Mojira IDs (QC/MC-80851 is one) and check that the oracle reproduces them. This is a cheap validity test.
2. Build a version-diff experiment: decompile two Mojang-mapped versions and diff the redstone classes. Log the result under
   `data/external/` (gitignored), not in the repo.
3. Read the primary sources listed as `[U]` before relying on any number in this brief.

## 7. What I could not read
- DuckDuckGo search failed (invalid response) for: version-diff datasets, LLM Minecraft bug-finding, AlphaEvolve/FunSearch
  (retried on Exa, succeeded), Carpet `/log` details (retried on Exa, partial), and Carpet Mojira-specific details.
- Not fetched: Mojira pages beyond the search snippet, Technical Minecraft YouTube, Discord archives, Yarn/Parchment docs,
  Mojang's EULA full text, Java GameTest API docs. Each is marked `[U]` where it matters.

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Brief written to docs/research/code_digging_landscape.md with sections on bug-finding methods and publishers (sec 1), version-change tooling (sec 2), automated approaches (sec 3), Lean/code analogues (sec 4), legal/ethics (sec 5). Findings carry verification tags; the file has file/URL references and a severity-tagged risk list in sec 5.2 and sec 7."
    }
  ],
  "changedFiles": [
    "docs/research/code_digging_landscape.md"
  ],
  "testsAddedOrUpdated": [],
  "commandsRun": [
    {
      "command": "read docs/PLAN_2026-11.md, docs/ORACLE.md, docs/ENV.md, docs/VALIDITY.md (head)",
      "result": "passed",
      "summary": "Project context read; oracle, env and validity status recorded in the brief."
    },
    {
      "command": "web_search (DuckDuckGo, Exa) x 11 queries",
      "result": "failed",
      "summary": "About half of the DuckDuckGo queries returned unparseable results; the rest returned snippets used in the brief. Failed topics are listed in sec 7."
    }
  ],
  "validationOutput": [
    "Brief length is within the ~200-line budget. Unverified claims are marked [U]. No repo files were edited besides the single output file."
  ],
  "residualRisks": [
    "Numbers for AlphaProof, DeepSeek-Prover, FunSearch and AlphaEvolve (search budgets, pass rates) are unverified and must not be quoted from this brief.",
    "No version-diff dataset or LLM-based Minecraft bug-finding paper was found; absence is not proven.",
    "Mojang EULA and Commercial Usage Guidelines were not read in full; the legal notes rely on a wiki paraphrase and a Reddit thread.",
    "Carpet, Yarn, Parchment and Java GameTest API details are partly unverified."
  ],
  "noStagedFiles": true,
  "diffSummary": "Adds one new markdown brief (about 170 lines) at docs/research/code_digging_landscape.md. No other files changed.",
  "reviewFindings": [
    "major: docs/research/code_digging_landscape.md sec 5.1 - licence claim relies on a wiki paraphrase of the EULA; verify against the full Mojang EULA before any redistribution decision.",
    "minor: docs/research/code_digging_landscape.md sec 3.3 - LLM-based bug-finding literature search returned nothing; the 'not found' claim is weak evidence."
  ],
  "manualNotes": "Research tier pass. Search tool was unreliable (DuckDuckGo invalid responses). Recommend a standard-tier follow-up with working search for the [U] items, especially Mojira fix-version data and Carpet /log docs. No repo edits were made beyond the output file; no commits, no pushes."
}
```

