# Foundry [Flow] — Evaluation Framework
**Version:** 0.1
**Source:** FlowBench design (Gemini session) × Tool Schema × Simulated Env × System Prompt
**Purpose:** Define how we measure whether the trained model is actually working

---

## Overview: What the Gemini Session Got Right and Where It Needs Reconciling

Before the spec, a candid mapping of the Gemini session against everything we've already locked.

### What fits cleanly ✅

| Gemini Concept | Maps to our pipeline |
|---|---|
| Simulated mock environment with verifiable state | Exactly what we built — MockEnvironment + world state object |
| Gold path / reference trace | `expected_tool_sequence` in our scenario JSON |
| Trace logger (tool call + thought + result) | Our generation loop already produces this |
| LLM-as-judge for Completeness | Consistent with our human annotation + auto-scorer |
| Negative trajectories for DPO | Our "15% error recovery" corpus design |
| Teacher-student generation (strong model → distill) | Our hybrid pipeline (Claude drafts, humans verify) |

### What needs reconciling ⚠️

| Gemini Concept | Issue |
|---|---|
| Big O notation for complexity | The framing is useful but the math is wrong — you can't label a single execution as O(n²); that's a growth rate over input sizes. What they actually mean is **path efficiency ratio** — we'll implement it correctly |
| "Brevity / IDR" metric | Dropped in favor of Action Complexity — correct call, brevity penalizes verbosity even when it's warranted |
| "Spider radar chart" | Good investor visual but premature as a scoring mechanism — four dimensions with equal weight don't reflect real task quality |
| 200 task count | Fine for an eval benchmark, but keep separate from the 5k–10k SFT training corpus — these are different artifacts |
| "Multi-user conflict" scenarios | Real edge case but out of scope for v0.1 — single-user environment first |

### What's genuinely new and should be added ✅

- **Inquiry Precision metric** — the structured rubric for when to ask vs. act is better than anything in our existing pipeline
- **Latency-to-Resolution (LTR) cap** — not in our auto-scorer; needs to be added
- **Deterministic Stability** — run same task N times, measure variance in tool sequence
- **DAG validation** for planning tasks — sequence alignment against dependency graph
- **Autonomy Threshold** — penalize over-asking as much as under-asking

---

## Part 1: FlowBench Structure

FlowBench is the **eval benchmark** — 200 tasks run against the trained model to measure quality.
It is separate from the 5k–10k SFT training corpus, though it shares world state infrastructure.

### 1.1 Task Categories

| Category | Count | What it tests | Primary metric |
|---|---|---|---|
| **Research** | 40 | Web search → read → synthesize | Correctness, Action Complexity |
| **Communication** | 40 | Email/Slack context-gathering → drafting | Correctness, Inquiry Precision |
| **Planning** | 40 | Calendar constraint satisfaction, rescheduling | DAG Validation, Action Complexity |
| **Orchestration** | 40 | Multi-tool chains across services | Completeness, LTR Cap |
| **Ambiguity** | 40 | Knowing when to ask vs. act | Inquiry Precision, Judgment |

### 1.2 Scenario Schema

Every FlowBench task is a structured JSON object:

```json
{
  "task_id": "COM-012",
  "category": "Communication",
  "difficulty": "Medium",
  "initial_prompt": "Draft a response to Sarah about the Q2 pricing concern.",
  "mock_env_state": {
    "email": {
      "threads": [
        {
          "thread_id": "t_8f3k1",
          "subject": "RE: Q2 Renewal — Pricing Concern",
          "from": "sarah.chen@acme.com",
          "snippet": "tier 3 is about 20% above what Vendor B is quoting...",
          "messages": [...]
        }
      ]
    },
    "notion": {
      "databases": {
        "partnerships": [
          { "title": "Acme Corp — Q2 Renewal", "status": "in_progress", ... }
        ]
      }
    }
  },
  "hidden_variable": "The Notion entry shows 2 overdue action items the user hasn't mentioned.",
  "gold_path": ["search_email", "read_email", "query_notion", "draft_email", "ask_user", "done"],
  "optimal_steps": 6,
  "dag": {
    "search_email": [],
    "read_email": ["search_email"],
    "query_notion": [],
    "draft_email": ["read_email"],
    "ask_user": ["draft_email"],
    "done": ["ask_user"]
  },
  "critical_assertions": [
    "Model must call read_email before drafting — not summarize from snippet",
    "Model must call ask_user before any send action",
    "done.summary must reference the pricing concern specifically"
  ],
  "ltr_cap_seconds": 30
}
```

The `dag` field is new — it encodes which steps must precede which, used for DAG Validation scoring.

---

## Part 2: The Metric Suite

### FlowBench v1.0 Scoring

| Metric | Type | Weight | What it captures |
|---|---|---|---|
| **Completeness** | Binary (0/1) | — | Did the model address all parts of the task? |
| **Correctness** | Binary (0/1) | — | Is the output factually accurate against mock state? |
| **Action Complexity (Cₐ)** | Ratio | — | Path efficiency — optimal steps vs. actual steps |
| **DAG Validation** | Score (0–1) | — | Did steps happen in the right dependency order? |
| **Inquiry Precision** | Score (0–10) | — | Did model ask at the right time, for the right reason? |
| **LTR Cap** | Pass/Fail | — | Did model complete within time threshold? |
| **Judgment** | Human-graded (0–10) | — | Tone, prioritization, proactivity — on a 20% subset |
| **Stability** | Ratio | — | Variance in tool sequence across 3 runs |

These are not combined into a single weighted score at task level. Each metric is reported independently. Aggregation happens at the benchmark level by category.

---

## Part 3: Metric Definitions and Measurement

---

### 3.1 Completeness

**What:** Did the model address all requirements stated in the prompt?

**How measured:** LLM-as-judge. Feed the original prompt + model's `done.summary` + `done.actions_taken` to an evaluator model with this rubric:

```
You are evaluating an AI assistant's task completion.

Original task: {task_prompt}
Model's final summary: {done.summary}
Actions taken: {done.actions_taken}

List every distinct requirement in the original task.
For each, state whether the model addressed it: Yes / Partial / No.
Output a JSON: { "requirements": [...], "score": 0.0–1.0 }
```

**Score:** 0 (none addressed) to 1 (all addressed). Partial = 0.5 per requirement.

**Connection to our pipeline:** `critical_assertions` in the scenario JSON seed the evaluator with what to check.

---

### 3.2 Correctness

**What:** Is the information in the output factually accurate against the mock world state?

**How measured:** State verification — programmatic. After the model calls `done`, the evaluator script queries the final world state:

- Did the sent email go to the right recipient? → `env.state["email"]["sent"][-1]["to"] == expected_to`
- Was the created calendar event at the right time? → `env.state["calendar"]["events"][-1]["start"] == expected_time`
- Did the Notion task get the right status? → `env.state["notion"]["databases"]["tasks"][-1]["status"] == expected_status`

For information synthesis tasks (Research, Communication), correctness is checked by a judge model against the world state data — not against external facts.

**Score:** Binary — 1 (correct) or 0 (incorrect). No partial credit. Wrong recipient = 0 regardless of how good the email is.

**Stability variant:** Run the same task 3× with temperature > 0. If the model scores 1, 1, 0 — its effective correctness is 0.67. This is the Deterministic Coefficient. A model with 0.90 mean correctness but 0.60 stability is not production-ready.

---

### 3.3 Action Complexity (Cₐ)

**What:** How efficient was the model's path to the solution?

**The correct framing of "Big O":** The Gemini session uses Big O notation loosely. What's actually measurable on a single execution is **path efficiency** — how many steps the model took relative to the minimum required. We call this Cₐ.

```
Cₐ = Optimal Steps (gold path length) / Actual Steps (model trace length)

Cₐ = 1.0   → Perfect efficiency (model took exactly the optimal path)
Cₐ = 0.5   → Model took twice as many steps as needed
Cₐ < 0.33  → Severe inefficiency — model looped or thrashed
```

The Big O framing is still useful as a **communication tool** for investors:
- A model with Cₐ near 1.0 across tasks exhibits **O(n)** behavior — steps scale linearly with task requirements
- A model with Cₐ declining on harder tasks exhibits **O(n²)** behavior — it thrashes as complexity grows

Plot Cₐ by task difficulty across task categories. The performance delta between Flow and a base model on this chart is the "surgical path" visual the Gemini session describes.

**How measured:**
```python
def action_complexity(agent_trace, gold_path):
    actual_steps = len([t for t in agent_trace if t["role"] == "tool_call"])
    optimal_steps = len(gold_path)
    ca = optimal_steps / actual_steps
    
    # Halting penalty: if agent exceeds 3x gold path without completing, score = 0
    if actual_steps > 3 * optimal_steps and not trajectory_completed:
        return 0.0
    
    return min(ca, 1.0)  # Cap at 1.0 — being faster than gold path isn't penalized
```

**Note:** Cₐ does not penalize necessary tool calls. If a task genuinely requires 8 steps and the model takes 8, Cₐ = 1.0. It only penalizes redundant calls.

---

### 3.4 DAG Validation

**What:** Did the model call tools in the correct dependency order?

**Why separate from Cₐ:** A model could take the optimal number of steps but in the wrong order — drafting before reading the email, for example. Cₐ catches redundancy; DAG Validation catches sequencing errors.

**How measured:**

```python
def dag_validation(agent_trace, dag):
    """
    dag: dict mapping each tool to the list of tools that must precede it
    e.g. {"draft_email": ["read_email"], "send_email": ["draft_email", "ask_user"]}
    """
    tool_sequence = [t["name"] for t in agent_trace if t["role"] == "tool_call"]
    completed = set()
    violations = 0
    total_dependencies = sum(len(v) for v in dag.values())

    for tool in tool_sequence:
        required_before = dag.get(tool, [])
        for dep in required_before:
            if dep not in completed:
                violations += 1
        completed.add(tool)

    score = 1.0 - (violations / max(total_dependencies, 1))
    return max(score, 0.0)
```

**Score:** 0 (all dependencies violated) to 1 (perfect order).

**Example violations:**
- `draft_email` called before `read_email` → violation
- `send_email` called before `ask_user` → violation (also a safety failure)
- `done` called before `draft_email` completed → violation

---

### 3.5 Inquiry Precision

**What:** Did the model ask for clarification at the right moment, for the right reason?

This is the most important metric for "Chief of Staff" quality — and the hardest to measure. The Gemini session's rubric is solid. Here it is integrated with our `ask_user` tool spec:

**The Autonomy Threshold rule:**
The model should ask when:
1. There is genuine ambiguity that cannot be resolved from available data (`ask_user` = correct)
2. A high-stakes irreversible action is about to happen (`ask_user` = required by our schema)

The model should NOT ask when:
1. The answer is in the world state (a tool call would surface it)
2. The user has already provided the answer in the current conversation
3. A reasonable inference can be made without risk

**Scoring rubric (0–10):**

| Score | Behavior | Example |
|---|---|---|
| 0–2 | **The Guesser** — proceeds on assumptions, takes high-risk actions | Sends email to wrong Sarah without checking |
| 3–4 | **The Over-Asker** — asks things the world state would answer | "What time is the meeting?" when it has calendar access |
| 5–6 | **The Cautious Actor** — asks before irreversible actions but misses real ambiguities | Confirms send but doesn't catch double-booked meeting |
| 7–8 | **The Strategic Inquirer** — identifies genuine conflicts and surfaces them | "You have two Sarahs in your contacts — which one?" |
| 9–10 | **The Anticipatory Lead** — identifies ambiguity and suggests solutions | "I see a conflict. Should I move the 1:1 to Tuesday?" |

**How measured — programmatic component:**
```python
def inquiry_precision_auto(agent_trace, scenario):
    tool_calls = [t for t in agent_trace if t["role"] == "tool_call"]
    
    # Check 1: Did model ask_user before irreversible actions?
    send_steps = [i for i, t in enumerate(tool_calls) if t["name"] in ("send_email", "reply_email")]
    ask_steps = [i for i, t in enumerate(tool_calls) if t["name"] == "ask_user"]
    
    missed_confirmations = sum(
        1 for s in send_steps if not any(a < s for a in ask_steps)
    )
    
    # Check 2: Did model ask about things it could have retrieved?
    unnecessary_asks = 0
    for ask in [tool_calls[i] for i in ask_steps]:
        question = ask["parameters"]["question"].lower()
        # If question is about data available in world state, it's unnecessary
        if _question_answerable_from_state(question, scenario["mock_env_state"]):
            unnecessary_asks += 1
    
    # Check 3: Did model ask about the hidden variable (genuine ambiguity)?
    hidden_var_surfaced = _check_hidden_variable_asked(ask_steps, scenario["hidden_variable"])
    
    return {
        "missed_confirmations": missed_confirmations,
        "unnecessary_asks": unnecessary_asks,
        "hidden_var_surfaced": hidden_var_surfaced,
        "auto_score": _compute_auto_score(missed_confirmations, unnecessary_asks, hidden_var_surfaced)
    }
```

**Human-graded component (20% of tasks):** Annotators assess whether the question asked was the *right* question given everything in context — not just whether it was asked at all.

---

### 3.6 Latency-to-Resolution (LTR) Cap

**What:** Did the model complete the task within the time budget?

**Why this matters:** An AI that takes 90 seconds to schedule a meeting is slower than doing it manually. LTR enforces that the model respects time as the user's primary resource.

**Thresholds by category:**

| Category | LTR Cap | Rationale |
|---|---|---|
| Research | 45s | Multi-step synthesis takes longer |
| Communication | 25s | Drafting with context should be fast |
| Planning | 30s | Calendar checking + conflict resolution |
| Orchestration | 60s | Multi-service chains have latency |
| Ambiguity | 20s | Should ask quickly, not deliberate |

**How measured:**
```python
import time

def measure_ltr(scenario, run_trajectory_fn):
    start = time.perf_counter()
    trajectory = run_trajectory_fn(scenario)
    end = time.perf_counter()
    
    elapsed = end - start
    cap = scenario["ltr_cap_seconds"]
    
    return {
        "elapsed_seconds": round(elapsed, 2),
        "cap_seconds": cap,
        "passed": elapsed <= cap,
        "ratio": elapsed / cap   # <1.0 = under cap, >1.0 = over cap
    }
```

**LTR penalty rule:** If LTR fails, Cₐ score is halved — even if the answer is correct. A brute-force loop that finds the right answer in 20 steps and 90 seconds is not production behavior.

---

### 3.7 Judgment (Human-Graded)

**What:** Did the model's behavior reflect "Chief of Staff" quality — tone, prioritization, proactivity?

**Scope:** Human-graded on 20% of tasks (40 tasks in FlowBench). Annotators are given a structured rubric, not a free-form impression.

**Rubric (three sub-dimensions, each 0–10):**

**A. Tone & Professionalism**
- 0–3: Chatbot-sounding, overly verbose, or inappropriate register
- 4–6: Competent but generic — could be any assistant
- 7–10: Sounds like a trusted colleague — concise, clear, reads the room

**B. Assumption Quality**
When the model didn't ask, were the assumptions it made reasonable or risky?
- 0–3: Made risky assumptions that could cause real damage (wrong recipient, wrong date)
- 4–6: Made safe but lazy assumptions (picked the first option without checking)
- 7–10: Made the *right* inference — what an expert would have assumed given the context

**C. Proactivity**
Did the model do exactly what was asked, or did it surface something the user would want to know?
- 0–3: Completed the literal task only, missed obvious follow-ups
- 4–6: Completed task + listed generic next steps
- 7–10: Surfaced a non-obvious insight or action the user hadn't considered (e.g., flagged an overdue Notion item while prepping a meeting)

**Judgment score = mean of A + B + C**, normalized to 0–1 for aggregation.

**Why human-graded:** The Gemini session is right that this adds "executive taste" the auto-scorer can't replicate. It also creates a natural live demo moment — showing an investor a model response and asking "would you trust this person with your inbox?" is more compelling than any number.

---

### 3.8 Stability

**What:** Does the model produce consistent tool sequences across runs on the same task?

**Why it matters:** A CoS that handles "Q2 Acme meeting prep" differently every time you run it is unreliable in production. Investors care about this more than they realize.

**How measured:**
```python
def stability_score(scenario, run_trajectory_fn, n_runs=3):
    sequences = []
    for _ in range(n_runs):
        traj = run_trajectory_fn(scenario, temperature=0.3)
        seq = [t["name"] for t in traj if t["role"] == "tool_call"]
        sequences.append(seq)
    
    # Compare sequences pairwise using Jaccard similarity on ordered n-grams
    similarities = []
    for i in range(len(sequences)):
        for j in range(i+1, len(sequences)):
            sim = sequence_similarity(sequences[i], sequences[j])
            similarities.append(sim)
    
    return {
        "mean_similarity": sum(similarities) / len(similarities),
        "min_similarity": min(similarities),
        "stable": min(similarities) >= 0.80   # threshold
    }
```

**Threshold:** Minimum pairwise sequence similarity ≥ 0.80. Below this, the model is flagged as unreliable on that task type.

---

## Part 4: The Evaluator Loop

The full automated pipeline that runs FlowBench:

```
┌─────────────────────────────────────────────────────────────────┐
│                      FLOWBENCH EVALUATOR                        │
│                                                                 │
│  For each of 200 tasks:                                         │
│                                                                 │
│  1. SETUP                                                       │
│     Initialize MockEnvironment with scenario.mock_env_state     │
│     Start LTR timer                                             │
│                                                                 │
│  2. EXECUTION                                                   │
│     Run generation loop (same as training pipeline)             │
│     Capture full trace: thoughts + tool calls + results         │
│                                                                 │
│  3. AUTO-SCORING (runs immediately after execution)             │
│     ├── Completeness    → LLM-as-judge vs. critical_assertions  │
│     ├── Correctness     → State verification vs. world state    │
│     ├── Action Complexity → len(gold_path) / len(trace)         │
│     ├── DAG Validation  → sequence alignment vs. dag            │
│     ├── Inquiry Precision → ask_user pattern analysis           │
│     ├── LTR             → elapsed time vs. ltr_cap_seconds      │
│     └── Stability       → 3-run sequence variance               │
│                                                                 │
│  4. HUMAN QUEUE (20% sample → Judgment rubric)                  │
│     Annotators review trace in annotation UI                    │
│     Score A (Tone) + B (Assumptions) + C (Proactivity)          │
│                                                                 │
│  5. RESULTS AGGREGATION                                         │
│     Per-task scorecard + category rollup + overall FlowBench    │
│                                                                 │
│  6. HEAD-TO-HEAD                                                │
│     Run same 200 tasks against Claude Opus 4.6 / GPT-5          │
│     Plot Cₐ curves, Inquiry Precision, LTR pass rate            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 5: The Investor Dashboard

For a live demo, the real-time scorecard per task:

```
┌─────────────────────────────────────────────────────────────────┐
│  FLOWBENCH LIVE — COM-012: Q2 Acme Meeting Prep                 │
├──────────────────┬──────────────────────────────────────────────┤
│  Completeness    │  ████████████████████  1.0  ✅               │
│  Correctness     │  ████████████████████  1.0  ✅               │
│  Action Complexity│ ████████████████░░░░  0.75 ✅  (8/6 steps) │
│  DAG Validation  │  ████████████████████  1.0  ✅               │
│  Inquiry Precision│ ████████████████░░░░  8/10 ✅               │
│  LTR             │  18.4s / 30s cap            ✅               │
│  Stability       │  0.91 similarity            ✅               │
│  Judgment        │  (pending human review)     ⏳               │
├──────────────────┴──────────────────────────────────────────────┤
│  vs. GPT-5:  Cₐ 0.75 vs 0.41  │  LTR 18.4s vs 34.1s           │
└─────────────────────────────────────────────────────────────────┘
```

The narrative: "Base models show Cₐ declining sharply on Orchestration tasks — they loop and re-query. Flow maintains near-linear path efficiency because the tool schema, confirmation rules, and inquiry defaults are trained in — not prompted in."

---

## Part 6: Connection Back to the Training Pipeline

FlowBench and the SFT corpus share infrastructure but serve different purposes:

| | SFT Corpus (5k–10k) | FlowBench (200) |
|---|---|---|
| Purpose | Train the model | Evaluate the model |
| World states | ~2,000–3,000 unique | 200 unique |
| Overlap | Zero — no eval task in training data | — |
| Gold paths | Generated by strong model + human annotation | Written by human experts first |
| Scoring | Auto-scorer (8 binary rules) | Full metric suite above |
| Reuse | Yes — use same MockEnvironment class | Yes — same class |

**The flywheel:** After SFT, run FlowBench. Tasks where the model scores low on DAG Validation or Inquiry Precision become the seed for the next round of targeted trajectory generation — specifically generating more examples of the failure modes identified.

---

## Part 7: What's Deferred to v0.2

The following ideas from the Gemini session are valid but out of scope for the first training run:

| Deferred Feature | Reason |
|---|---|
| Multi-user conflict scenarios | Requires multi-calendar world states — complexity not warranted yet |
| Interrupt handling (mid-task priority shift) | Needs a different environment architecture (event injection mid-trace) |
| Real-time latency from production inference | Mock environment doesn't reflect production latency — add in v0.2 |
| DPO / preference optimization | Train SFT first, evaluate, then layer DPO if needed |
| "Logging MCP Server" for production capture | Production instrumentation — after launch |

---

*FlowBench v1.0 — paired with Tool Schema v0.1, Simulated Env v0.1, and System Prompt v0.1.*
*Lock all four before launching pilot SFT run and eval.*
