# Foundry

**Goal:** Build an LLM that excels at research, planning, and business automation — a "chief of staff" model that powers OpenOri's desktop AI assistant. Using Gemma 4 as the foundation.

**First model:** Foundry [Flow] — research, design & business automation
**Company:** OpenOri LLC (https://www.openori.ai)
**Team:** 3 people | **Timeline:** 2–3 months | **License:** Proprietary

---

## Status: Ideation

---

## 1. Why Gemma 4?

- Strong open-weight foundation with competitive general reasoning
- Permissive licensing for commercial use
- Active community and ecosystem (LoRA adapters, quantization tooling)
- Google's continued investment in the Gemma line

## 2. Strategy: Chief of Staff Model

### Why research & business automation instead of coding?

- **Coding AI is a red ocean** — Cursor, Copilot, Devin, Augment, all backed by billions. Competing head-on is a losing game at our scale.
- **"Chief of staff" AI is wide open** — no dominant model specializes in research, planning, email triage, meeting prep, workflow orchestration.
- **We have distribution** — Ori is already a shipped desktop assistant with users. Foundry [Flow] becomes the brain that powers it. No need to build a product from scratch.
- **Investor story is stronger** — "The model that powers our shipped product" beats "a slightly better coding AI."
- **Tool use still transfers** — the core training approach (structured reasoning over tool calls) is the same. Only the tools and data change.

### What Foundry [Flow] should excel at

The model is a chief of staff for knowledge workers. It should:

1. **Research** — search the web, read articles/docs, synthesize findings into actionable summaries
2. **Email & communication** — draft replies, triage inbox, flag urgent items, compose outreach
3. **Calendar & scheduling** — check availability, suggest meeting times, prep agendas, summarize upcoming day
4. **Document creation** — draft memos, proposals, reports, slide outlines from rough notes
5. **Data lookup & analysis** — query spreadsheets/databases, summarize trends, create tables
6. **Multi-step planning** — break complex tasks into phased execution plans with dependencies
7. **Service orchestration** — coordinate across GitHub, Slack, Notion, Gmail, Calendar in one workflow
8. **Ambient monitoring** — check PRs, flag overdue tasks, surface what matters — proactively, not just when asked

### Revised Timeline (8–12 weeks)

#### Phase 1 — Infra & Baselines (Weeks 1–3)
- Set up training infra (Axolotl/TRL + cloud GPUs)
- Build eval harness for chief-of-staff tasks (custom benchmarks — see Eval section)
- Baseline Gemma 4 31B on research/planning/tool-use tasks
- Define tool-use schema (function calling format, action space for business tools)
- Map Ori's existing MCP connectors to Foundry's tool schema

#### Phase 2 — Tool-Use SFT (Weeks 3–6)
- Curate tool-ue training data:
  - Multi-step research trajectories (question → search → read → synthesize → answer)
  - Email/communication workflows (receive → triage → draft → send)
  - Planning & scheduling sequences (task → check calendar → propose plan → execute)
  - Cross-service orchestration (GitHub PR → Slack notification → Calendar block)
- LoRA fine-tune on tool-use + reasoning
- Evaluate, iterate on data quality

#### Phase 3 — Reasoning & Alignment (Weeks 6–9)
- DPO/RLHF on trajectory quality (did the model complete the task efficiently and correctly?)
- Chain-of-thought training for planning steps
- Prioritization training (what's urgent vs what can wait?)
- Eval against general-purpose models (Opus 4.6, GPT-5) on custom benchmarks

#### Phase 4 — Ori Integration & Demo (Weeks 9–12)
- Integrate Foundry [Flow] as a model option in Ori
- Quantize for local inference (Ori is local-first)
- Prepare investor materials: custom benchmarks, live Ori demo, platform roadmap
- Red-team for failure modes (privacy, hallucination, over-action)

### Team Allocation (3 people)

| Person | Focus | Key deliverables |
|--------|-------|-------------------|
| **P1 — Data & Training** | Training pipeline, data curation, experiment runs | Fine-tuned model checkpoints |
| **P2 — Eval & Infra** | Eval harness, cloud infra, experiment tracking | Reproducible benchmarks |
| **P3 — Ori Integration** | Tool-use schema, Ori connector mapping, demo | Foundry [Flow] running inside Ori |

## 3. Data Strategy

### Primary Focus: Chief-of-Staff Trajectories

The most valuable training data for Foundry [Flow] is **successful multi-tool workflow traces** — sequences like:

```
User: Prep me for my 2pm meeting with the Acme team
Model: [thinks] I need to check what the meeting is about, who's attending, and gather context.
Model: [calls] check_calendar(time="2pm today")
Tool:  → "Q2 Partnership Review with Acme Corp. Attendees: Sarah Chen, Mike Patel. Notes: discuss renewal terms."
Model: [calls] search_email(query="Acme Corp", recent=7d)
Tool:  → 3 threads: latest is Sarah asking about updated pricing tiers
Model: [calls] read_email(thread_id="...")
Tool:  → [email content with pricing discussion]
Model: [thinks] Sarah has concerns about tier 3 pricing. I should also check if there are open items in Notion.
Model: [calls] query_notion(database="partnerships", filter="Acme")
Tool:  → [Notion row with deal status, open action items]
Model: [calls] done(summary="**Meeting prep for Acme 2pm:**\n- Topic: Q2 renewal\n- Key concern: Sarah flagged tier 3 pricing is 20% above competitor\n- Open items: 2 action items overdue from last meeting\n- Suggestion: come with a revised tier 3 proposal")
```

### Data Sources

**For tool-use training (priority):**
- Hand-crafted gold trajectories of multi-service workflows (the most valuable)
- Self-generated trajectories: run base Gemma 4 on simulated business tasks, keep successful ones
- Reconstructed trajectories from real Ori usage patterns (anonymized)
- Open-source agent traces for general tool-use reasoning

**For domain knowledge (secondary):**
- Business communication datasets (email templates, meeting notes, project plans)
- Research synthesis examples (question → search → summarize patterns)
- Scheduling and prioritization datasets
- Public knowledge bases for general reasoning

### Quality Filters
- Deduplication (exact + near-duplicate)
- Trajectory success verification (did it complete the task correctly?)
- Step efficiency (prefer concise, purposeful tool use)
- Diverse workflow patterns (not just search → summarize loops)
- Privacy: no PII in training data — use synthetic/anonymized examples only

### Tooling (Pre-Investment Stack)

| Need | Tool | Cost |
|------|------|------|
| Data processing | Python scripts + HuggingFace `datasets` | Free |
| Storage | GCS bucket | ~$20/month |
| Training | Axolotl / TRL on GCP | Pay per run |
| Experiment tracking | Weights & Biases (free tier) | Free |
| Labeling (if needed) | Argilla or Label Studio (open-source) | Free |

**Not yet:** Scale AI (enterprise RLHF labeling, $50k+ contracts) and Databricks (managed infra, overkill for 3 people). Revisit post-investment when team grows past 5–6.

### What We Won't Do: Distillation

Distillation = using a frontier model (Opus, GPT-5) to generate training data. We're avoiding this because:
- Most frontier model ToS prohibit using outputs to train competing models
- Legal risk is a dealbreaker for investors
- It's not a defensible moat — anyone can distill
- We build our own data pipeline instead — harder but more valuable as IP

## 4. Tool-Use Schema

This is the "language" the model speaks when it wants to take actions. Same format approach (JSON-in-XML with `<think>` blocks), but the tools are designed for a chief-of-staff agent.

### Format: JSON function calls inside XML tags

```
<think>
The user wants meeting prep. I need to check the calendar, find recent emails about this contact, and summarize.
</think>

<tool_call>
{"name": "check_calendar", "args": {"time": "2pm today"}}
</tool_call>
```

The runtime (Ori) executes the tool via its MCP connectors and returns:

```
<tool_result>
{"name": "check_calendar", "output": "Q2 Partnership Review with Acme Corp. Attendees: Sarah Chen, Mike Patel."}
</tool_result>
```

The model continues thinking and calling tools until done.

**Why this format?**
- XML tags are easy for models to learn (clear start/stop boundaries)
- JSON inside keeps tool calls structured and parseable
- `<think>` blocks let us train chain-of-thought reasoning alongside tool use
- Maps directly to Ori's existing MCP connector architecture

### Action Space (available tools)

Designed around Ori's existing service connectors + general capabilities:

**Communication & Calendar**

| Tool | Purpose | Args |
|------|---------|------|
| `search_email` | Search inbox | `query`, `from?`, `recent?`, `unread?` |
| `read_email` | Read a specific email/thread | `thread_id` |
| `draft_email` | Compose an email draft | `to`, `subject`, `body`, `reply_to?` |
| `check_calendar` | View calendar entries | `time`, `range?` |
| `create_event` | Schedule a calendar event | `title`, `time`, `duration`, `attendees?` |
| `send_message` | Send a Slack/chat message | `channel`, `message` |

**Research & Knowledge**

| Tool | Purpose | Args |
|------|---------|------|
| `web_search` | Search the internet | `query` |
| `read_page` | Read a webpage/article | `url` |
| `query_notion` | Query a Notion database | `database`, `filter?` |
| `read_document` | Read a local file/doc | `path` |

**Task & Project Management**

| Tool | Purpose | Args |
|------|---------|------|
| `list_tasks` | List tasks/issues from a project tool | `project?`, `assignee?`, `status?` |
| `create_task` | Create a new task/issue | `title`, `description`, `assignee?`, `priority?` |
| `check_github` | Check PRs, issues, CI status | `repo`, `type`, `filter?` |

**General**

| Tool | Purpose | Args |
|------|---------|------|
| `ask_user` | Ask for clarification | `question` |
| `set_reminder` | Schedule a future reminder | `message`, `time` |
| `done` | Signal task completion | `summary` |

**Design principles:**
- Maps 1:1 to Ori's MCP connectors — no translation layer needed
- Each tool does one thing (no overlap)
- Minimal args (fewer things to get wrong)
- `web_search` + `read_page` are the research backbone
- `done` forces explicit task completion — important for eval
- `ask_user` teaches the model when to ask vs when to act

### Token Format in Training Data

```
<|system|>You are a chief of staff AI. Use tools to help the user manage their work. Think before acting. Be concise and actionable.<|end|>
<|user|>Prep me for my 2pm meeting<|end|>
<|assistant|><think>I need to check what the meeting is about and gather relevant context.</think>
<tool_call>{"name": "check_calendar", "args": {"time": "2pm today"}}</tool_call><|end|>
<|tool|><tool_result>{"name": "check_calendar", "output": "Q2 Review with Acme Corp. Attendees: Sarah Chen."}</tool_result><|end|>
<|assistant|><think>Let me check recent emails about Acme to surface anything relevant.</think>
<tool_call>{"name": "search_email", "args": {"query": "Acme Corp", "recent": "7d"}}</tool_call><|end|>
...continues...
```

## 5. Trajectory Data Generation

This is how we create training data at scale without distillation.

### Method 1: Simulated Environments (primary, automated)

Build simulated "office environments" — mock inboxes, calendars, Notion databases, Slack channels, and GitHub repos. Then:

1. Generate diverse task prompts ("Prep for my 2pm meeting", "Summarize this week's PRs", "Draft a follow-up to Sarah's email", "What's overdue on the Q2 roadmap?")
2. Run base Gemma 4 31B against the simulated environment with our tool schema
3. Evaluate success: did the model produce a correct, complete answer using the right tools?
4. **Keep successful trajectories, discard failures**
5. Iterate: train on successes, generate more, repeat (flywheel)

**Why simulated environments?** Unlike coding (where you can verify with test suites), business tasks need structured mock data to evaluate against. A simulated inbox with known contents lets us verify "did the model find the right email?" objectively.

### Method 2: Ori Usage Reconstruction (secondary, semi-automated)

Leverage real patterns from Ori's existing usage:

1. Identify common workflow patterns from Ori (anonymized/aggregated)
2. Reconstruct ideal trajectories: what tool calls should a perfect chief of staff make?
3. Generate synthetic versions with varied contexts (different companies, different people, different urgency levels)
4. Semi-automated — a script generates the skeleton, a human reviews/fixes

### Method 3: Hand-Crafted Gold Trajectories (small but high-value)

For critical patterns the model must nail:

- **Meeting prep** — calendar → email → docs → synthesize briefing
- **Inbox triage** — scan emails → categorize by urgency → draft replies for urgent ones
- **Research synthesis** — web search → read multiple sources → produce summary with citations
- **Cross-service coordination** — GitHub PR merged → Slack notification → update Notion → schedule follow-up
- **Ambiguity handling** — ask clarifying questions when the task is vague
- **Prioritization** — user has 10 tasks, model helps sequence them by urgency/importance

Each team member crafts ~50 gold trajectories. 150 total. Small dataset, but disproportionately valuable.

### Scale Targets

| Source | Est. trajectories | Quality | Effort |
|--------|-------------------|---------|--------|
| Simulated environments (automated) | 2,000–5,000 | Medium (verified against mock data) | Low (compute cost) |
| Ori usage reconstruction | 500–1,000 | High | Medium |
| Hand-crafted gold | 100–200 | Very high | High |
| **Total** | **~3,000–6,000** | Mixed | — |

3,000–6,000 trajectories is enough for LoRA SFT on tool use. It's a small dataset by LLM standards, but tool-use is a narrow skill — you don't need millions of examples.

## 6. Evaluation Plan

### The Challenge

There's no "SWE-bench for chief of staff" — we need to build our own. This is harder to benchmark than coding, but also means we define the playing field. We'll discuss eval design in detail separately, but here's the framework.

### Existing Benchmarks (sanity checks)

| Benchmark | What it measures | Why we still care |
|-----------|-----------------|-------------------|
| MMLU / MMLU-Pro | General knowledge & reasoning | Ensure we didn't regress base capabilities |
| BFCL (Berkeley Function Calling) | Structured function calling accuracy | Directly relevant — measures tool-use correctness |
| MT-Bench | Multi-turn conversation quality | Chief of staff is conversational by nature |
| IFEval | Instruction following | Must follow complex multi-part instructions |

### Custom Foundry Eval: FlowBench

Our primary benchmark. Built on simulated office environments with verifiable outcomes.

**Structure:** 200 tasks across 5 categories, each in a simulated environment with mock services.

| Category | Count | Example task | What it tests |
|----------|-------|-------------|---------------|
| **Research** | 40 | "What are the top 3 competitors to Acme's new product? Summarize with sources." | Web search → read → synthesize |
| **Communication** | 40 | "Draft a follow-up email to Sarah about the Q2 pricing discussion" | Email search → context gathering → drafting |
| **Planning** | 40 | "I have 8 meetings this week. Which can I skip? Prep me for the important ones." | Calendar → prioritization → multi-step prep |
| **Orchestration** | 40 | "The v2.1 release just shipped. Notify the team, update the roadmap, and draft the changelog." | Multi-service coordination (GitHub → Slack → Notion) |
| **Ambiguity** | 40 | "Handle my inbox" (intentionally vague) | Knows when to ask clarifying questions vs act autonomously |

**Scoring (per task):**
- **Completeness:** Did the model address all parts of the task? — 0 to 1
- **Correctness:** Is the information accurate (verified against mock data)? — 0 or 1
- **Efficiency:** How many tool calls? (fewer = better) — normalized score
- **Judgment:** Did it prioritize correctly? Did it ask when unsure? — human-graded on a subset

**Head-to-head comparison:** Run the same FlowBench tasks against Opus 4.6, GPT-5, and base Gemma 4 to show relative improvement.

**Why this matters for investors:**
- No existing benchmark measures "chief of staff" quality — we define the standard
- Live demo of Foundry [Flow] inside Ori solving real tasks is more compelling than any benchmark number
- FlowBench + live demo = the investor pitch

## 7. Model Size Decision

### Recommendation: Start with Gemma 4 31B Dense

**Why not the 26B MoE?** The MoE is efficient at inference (only 3.8B active params), but the dense 31B is easier to fine-tune, more predictable, and has stronger baseline scores.

**Why 31B for a chief-of-staff model?**
- Strong general reasoning (MMLU ~87%) — needed for research synthesis and prioritization
- Good instruction following out of the box
- 256K context window — critical for reading long email threads, documents, meeting histories
- Fits quantized on a single RTX 5090 for local Ori inference (aligns with Ori's local-first philosophy)

### Staged Approach

1. **Start with 31B + LoRA** — cheapest way to validate the approach
2. **Graduate to full fine-tune of 31B** — once LoRA results prove the data pipeline works
3. **Consider training a custom MoE** — only if investment comes through and you need inference efficiency
4. **Consider smaller distilled variant** — if Ori needs a lighter model for lower-end hardware

## 6. Compute & Infrastructure

### Local Machine

**Specs:** RTX 5070 Ti (16 GB VRAM) · Ryzen 7 9700x 8-Core · 32 GB RAM

| Task | Feasible locally? | Notes |
|------|--------------------|-------|
| Data processing, filtering, dedup | Yes | CPU/RAM bound — 32 GB is plenty |
| Agentic scaffold / tool runtime dev | Yes | No GPU needed |
| Eval harness development | Yes | Build & test locally, run at scale on GCP |
| QLoRA fine-tune on Gemma 4 E4B (4.5B) | Yes | Fits in 16 GB — use to validate training pipeline before spending cloud credits |
| Inference on Gemma 4 E4B | Yes | Fast, good for rapid iteration |
| Self-play sandbox orchestration | Yes (CPU side) | Docker containers are CPU/RAM bound; model inference calls out to GCP |
| Inference on quantized Gemma 4 31B | Marginal | 4-bit needs ~18 GB, would require CPU offloading — slow but possible for spot checks |
| 31B LoRA / full fine-tune | No → GCP | Needs 40–80+ GB VRAM |
| 31B eval runs at scale | No → GCP | Needs A100 for reasonable throughput |

**Strategy:** Do all development, data work, and pipeline validation locally. Only hit GCP when you need 31B.

### Recommended Starting Setup (Pre-Investment)

| Item | Spec | Est. Cost |
|------|------|-----------|
| **Phase 1: LoRA fine-tuning** | 1–2x A100 80GB (cloud) | $500–$2,000/run |
| **Phase 2: Full fine-tune** | 8x A100 80GB or 4x H100 | $10,000–$25,000/run |
| **Eval infrastructure** | 1x A100 for inference | $500–$1,000/month |
| **Data pipeline** | CPU instances + storage | $200–$500/month |
| **Total to first demo** | — | **~$15,000–$30,000** |

### Cloud Provider: GCP

Chosen for startup cloud credits and native Gemma/TPU support.

- **Training:** TPU v5e pods (cost-effective for Gemma, Google's own infra) or A100/H100 VMs
- **Eval/inference:** Single A100 VM or TPU v5e
- **Storage:** GCS buckets for datasets and checkpoints
- **Fallback:** RunPod spot instances for quick LoRA experiments if GCP credits run tight

### Framework Stack
- **Training:** Axolotl or TRL (Hugging Face) for SFT + LoRA
- **Alignment:** TRL for RLHF/DPO
- **Distributed:** DeepSpeed ZeRO-3 or FSDP for full fine-tune
- **Eval:** Eleuther lm-evaluation-harness + custom harness
- **Experiment tracking:** Weights & Biases

### Path to Attracting Investment

The **$15k–$30k range gets you to a credible demo** — a fine-tuned Foundry [Flow] running inside Ori, demonstrably better than base Gemma 4 at chief-of-staff tasks.

**What investors want to see:**
- Live demo: Foundry [Flow] inside Ori preparing for a meeting, triaging email, coordinating across services
- FlowBench results showing improvement over base Gemma 4 and competitive with frontier models
- The Ori + Foundry full-stack story (app layer + model layer)
- A differentiated data pipeline (your moat)
- Platform taxonomy showing what more funding unlocks (Foundry [Sentinel], [Statute], etc.)

**What serious investment unlocks:**
- $100k–$500k: full fine-tune iterations, larger dataset, small team, more service connectors
- $1M+: custom pre-training mix, RLHF at scale, enterprise Ori distribution, additional Foundry variants

## 7. Open Questions

- [x] What Gemma 4 model size to start with? → **31B Dense**
- [x] Compute budget — what's realistic? → **~$15k–$30k to first demo**
- [x] Solo effort or building a small team? → **Team of 3**
- [x] Timeline flexibility — hard deadline or open-ended? → **2–3 months**
- [x] Licensing goals — open-source the result or keep proprietary? → **Proprietary**
- [x] How to handle the "tool use" gap? → **Make it the primary focus, not secondary**
- [x] Distillation from frontier models? → **No. Build own data pipeline.**
- [x] Cloud provider → **GCP (startup credits + native Gemma/TPU support)**
- [x] Agentic tool schema → **17 tools across 4 categories, JSON-in-XML format, `<think>` blocks for CoT**
- [x] Eval design → **FlowBench (200 tasks, 5 categories) + BFCL/MT-Bench sanity checks**
- [x] Trajectory generation → **Simulated environments (bulk) + Ori reconstruction + hand-crafted gold**
- [x] GCP credits → **Apply to Google for Startups Cloud Program — up to $250k Year 1**
- [x] Sandboxing strategy → **Docker containers for simulated environments**
- [x] First model pivot → **Foundry [Flow] (chief of staff) instead of Foundry [Code] (coding)**
- [~] Team member assignment → Akshay handling internally
- [ ] FlowBench detailed design — simulated environment setup, task generation, scoring rubric
- [ ] Ori integration plan — which MCP connectors map to which Foundry tools?
- [ ] Simulated environment tooling — how to build mock inboxes/calendars/Notion at scale?

## 8. Conversation Log

### 2026-04-26 — Kickoff
- Decided on Gemma 4 as foundation
- Identified key pillars: data, eval, SFT, alignment, specialization
- **Model size decision:** Gemma 4 31B Dense
- **Strategy:** Start with LoRA, graduate to full fine-tune once pipeline is validated
- **Budget to first demo:** ~$15k–$30k

### 2026-04-26 — Strategic Pivot
- Team of 3, 2–3 month timeline, proprietary
- **Key pivot: focus on research + tool use first, not code generation**
- Rationale: cheaper to train, more differentiated, what investors actually care about
- No distillation from frontier models — build own data pipeline
- Explained distillation concept — decided against it for legal/IP reasons
- New open questions: cloud provider, tool schema design, trajectory data generation
- **Data tooling:** Scale AI and Databricks are overkill pre-investment. Use HF datasets + S3 + W&B + Argilla instead.

### 2026-04-26 — Core Design Decisions
- **Cloud:** GCP — startup credits + native Gemma/TPU support
- **Tool-use schema designed:** 8 tools (read_file, write_file, edit_file, grep, list_dir, run_command, ask_user, done), JSON-in-XML format with `<think>` blocks for chain-of-thought
- **Trajectory generation strategy:** 3 methods — self-play flywheel (bulk), reconstruction from SWE-bench (medium), hand-crafted gold (small but high-value). Target: 3k–6k trajectories.
- **Eval plan:** SWE-bench Verified as primary metric + custom ToolBench-Code eval (200 tasks across navigation, diagnosis, multi-step fix, recovery)
- All major open questions now resolved — remaining: GCP credit amount, sandboxing approach, team role assignment

### 2026-04-26 — Product Naming
- Defined Foundry brand taxonomy (see BRAND.md)
- Strategy: prove first model, show taxonomy as "platform vision" to investors

### 2026-04-26 — Pivot to Foundry [Flow]
- **Major pivot: first model changed from Foundry [Code] to Foundry [Flow]**
- Rationale: aligns with Ori's "chief of staff" positioning, less crowded market than coding AI, we already have distribution via Ori
- Focus: research, planning, email/calendar management, cross-service orchestration
- Tool schema redesigned: 17 tools across communication, research, task management, and general categories — maps directly to Ori's MCP connectors
- Data strategy: simulated office environments (mock inboxes, calendars, Notion) for trajectory generation
- Eval: custom FlowBench (200 tasks, 5 categories) — no "SWE-bench for chief of staff" exists, so we build the standard
- Investor story strengthened: "The model that powers our shipped product" + platform vision

### 2026-04-26 — Local Hardware
- **Local machine:** RTX 5070 Ti (16 GB) + Ryzen 7 9700x + 32 GB RAM
- Use locally for: data pipeline, scaffold dev, eval harness, QLoRA on E4B for pipeline validation
- GCP only for: 31B training and inference
- Saves significant cloud credits by keeping dev work local

---

*This is a living document. Updated as decisions are made.*
