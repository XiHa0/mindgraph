# MindGraph

**English** | [简体中文](README.md)

Turn one author's documents / lecture transcripts (100k–500k characters) into a
**structured knowledge graph (Neo4j) + a question-answering agent**, via an
**evaluation-driven loop**.

> In one line: naive RAG retrieves isolated text chunks — it never teaches the model
> *how to use* them. MindGraph extracts the author's **argumentative structure**
> (claims, evidence, methods, conditions, causes…) into shaped subgraphs, so retrieval
> returns a skeleton instead of fragments — and answers come out structured.

---

## Core ideas

1. **Two-layer schema** — don't chase a universal ontology.
   - **meta-layer (frozen)**: the *epistemic / argumentative structure* shared by all
     "ideas / teaching" documents (see below).
   - **domain-layer (induced per document)**: the author's own concepts, terms, aliases.
2. **Evaluation-driven loop** — build a test set → extract → measure coverage → diagnose
   gaps → targeted revision → re-extract, until it converges. What's *reusable* is not the
   schema, but **the loop that builds the schema**.
3. **Two models, clear division of labor**:

   | Model | Role | What it does | Needs a key |
   |---|---|---|---|
   | **Orchestrator = coding agent (Claude Code / Codex)** | decide | build/verify the test set, set/revise the schema, run extraction, read reports and **decide whether to re-extract / change the schema / change strategy** | No (it *is* the agent) |
   | **worker (DeepSeek V4 Pro recommended)** | do the work | two-stage **extraction** + runtime **answering** | **Yes (the only required key)** |
   | (optional) API judge | score | only "autonomous mode" rubric-scores reasoning questions | Optional |

4. **Two run modes**:
   - **Agent-driven (default)**: the orchestrator is your coding agent, driving a toolbox
     CLI step by step and reading JSON to decide. Needs only the worker key.
   - **Autonomous (optional)**: plug in an API judge and let `BuilderLoop` run unattended
     to convergence.

---

## Data flow

```
 document
  │  chunk (structural: filter noise → split by headings → ~900 chars → tag kind)
  ▼
 chunks ──► schema induction (domain concept table + aliases)
  │
  │  build ≥100 stratified gold test items → human-verify & freeze
  ▼
 extract (DeepSeek two-stage: nodes first → entity resolution → edges among known nodes) ──► Neo4j
  ▼
 evaluate (node/edge coverage, deterministic) + diagnose (missing node/edge / wrong / granularity, by type)
  ▼
 revise (inject targeted directives + type-level few-shot into next round) ──┐
  ▲                                                                          │
  └────────────── not converged → extract again ◄───────────────────────────┘
  ▼ converged (node≥0.85, edge≥0.75, answer≥0.80 and plateaued)
 knowledge graph + QA agent (retrieve subgraph → answer)
```

---

## Meta ontology (nodes / relations)

```
Nodes:     Concept · Claim · Principle · Method · Argument · Evidence · Condition ·
           Counterpoint · Analogy · MentalModel · Question · ReasoningPattern
Relations: SUPPORTS/REFUTES · USES · EXEMPLIFIES · APPLIES_WHEN · ANSWERS ·
           DEPENDS_ON/PART_OF/REFINES/GENERALIZES · CAUSES/ENABLES · CONTRASTS_WITH ·
           REALIZES · COMPOSED_OF
```

Two QA "spines" (sharing one node set, so a single graph serves both question types):

```
Explanatory: Question ←ANSWERS← Claim ←SUPPORTS← Argument →USES→ Evidence
Applied:     Condition ←APPLIES_WHEN← Method →REALIZES→ Principle
```

Full spec: [`mindgraph/SPEC.md`](mindgraph/SPEC.md) · full code map: [`wiki.md`](wiki.md)
(both currently in Chinese).

---

## Install

```bash
git clone https://github.com/XiHa0/mindgraph.git
cd mindgraph
python -m venv .venv && . .venv/Scripts/activate   # Windows; Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
# or install as a package (provides the `mindgraph` command): pip install -e .
```

You need a **Neo4j 5.x** instance (local or remote). Run the setup script once before first use:

```bash
cypher-shell -u neo4j -p <password> -f mindgraph/schema/constraints.cypher
```

## Configure

```bash
cp .env.example .env
# edit .env: at minimum set KG_WORKER_* (the worker model) and NEO4J_*
```

Only the worker key is required. Leave the judge (`KG_JUDGE_*`) empty — the default
agent-driven mode doesn't need it.

---

## Quickstart

### A. Agent-driven mode (recommended default)

The orchestrator is your coding agent (Claude Code / Codex). It runs the toolbox step by
step and reads JSON to decide:

```bash
# 1) chunk
python -m mindgraph.cli chunk    --doc doc.txt --doc-id authorX --out chunks.json

# 2) (agent reads chunks, writes schema.json: {"concepts":[...],"aliases":{...}}; builds & freezes gold.json)

# 3) extract into the graph (DeepSeek worker)
python -m mindgraph.cli extract  --chunks chunks.json --schema schema.json \
                                 --store neo4j --doc-id authorX

# 4) evaluate + diagnose (deterministic, emit JSON for the agent)
python -m mindgraph.cli coverage --gold gold.json --store neo4j --doc-id authorX --out coverage.json
python -m mindgraph.cli diagnose --gold gold.json --store neo4j --doc-id authorX --out diag.json

# 5) agent reads coverage.json + diag.json (dominant / missing_*_types / abstract edge patterns)
#    → decide: edit the concept table / add extraction directives / adjust chunking / re-extract → back to 3
```

`extract` also supports `--store memory --graph-out graph.json` to validate the pipeline
offline without Neo4j.

### B. Autonomous mode (optional, needs a second key)

After setting `KG_JUDGE_*`, use `BuilderLoop` to run unattended to convergence — wiring is
in [`mindgraph/SPEC.md`](mindgraph/SPEC.md) ("全真接线" / full wiring).

### Build a test set

```bash
# draft ≥100 stratified gold items with the worker (or have the agent write them), then human-verify & freeze
python -m mindgraph.testset.run generate --chunks chunks.json --target 150 --out-dir ./out
python -m mindgraph.testset.run freeze --draft ./out/testset.draft.json --out ./out/gold.json
```

---

## Design notes (why it works this way)

- **Chunk by argument unit, filter noise first**: an argument in an essay often spans
  several paragraphs, and fixed-length chunking splits a claim from its evidence; TOC rows,
  page numbers and OCR placeholders are the #1 source of retrieval noise, so drop them first.
- **Two-stage extraction**: extract nodes first and resolve them, then extract relations
  *among the known nodes* — better quality and lower cost.
- **Entity resolution**: an author refers to one concept by many surface forms; a union-find
  merges them into canonical + aliases, otherwise subgraphs fall apart.
- **No "teaching to the test"**: the reviser's few-shot uses only **type-level** abstract
  patterns, never the concrete gold answers.
- **Separate scoring from extraction**: the worker extracts, the judge scores — never let one
  model grade what it extracted.

## Docs

- [`wiki.md`](wiki.md) — full code structure (every module / class / interface / data flow).
- [`mindgraph/SPEC.md`](mindgraph/SPEC.md) — design spec (schema / coverage metrics / test-set
  format / loop orchestration).

## License

[MIT](LICENSE) © XiHa0
