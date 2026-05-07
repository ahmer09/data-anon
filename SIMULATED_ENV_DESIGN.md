# Foundry [Flow] — Simulated Environment Design & Gold Trajectory Generation
**Version:** 0.1
**Target:** 5k–10k gold trajectories for SFT Pilot Run 1
**Stack:** Mock environment · Hybrid generation (Claude-drafts + human review)

---

## Mental Model: What a "Simulated Environment" Actually Is

A simulated environment is a **deterministic function** that sits between the model and the world.

```
┌─────────────────────────────────────────────────────┐
│                  GENERATION LOOP                    │
│                                                     │
│  Task Prompt                                        │
│      │                                              │
│      ▼                                              │
│  [Model Turn] ──── <think> + <tool_call> ────►      │
│                                                     │
│  [Environment] ◄── tool name + parameters           │
│      │             looks up mock state              │
│      │             returns templated JSON           │
│      ▼                                              │
│  <tool_result> ────────────────────────────►        │
│      │                                              │
│      ▼                                              │
│  [Model Turn] ──── next <think> + <tool_call> ──►   │
│      ...                                            │
│      ▼                                              │
│  [Model Turn] ──── <tool_call> done ────────►       │
│                                                     │
│  Full trace saved as candidate trajectory           │
└─────────────────────────────────────────────────────┘
```

The environment does three things:
1. **Holds a world state** — a snapshot of what's "in" Gmail, Calendar, Notion for this scenario
2. **Executes tool calls** — looks up the state, applies logic, returns realistic JSON
3. **Enforces rules** — returns the right error codes when the model makes invalid calls

The model never touches real data. Everything it sees is constructed fiction that looks identical to production responses.

---

## Part 1: Environment Architecture

### 1.1 World State Object

Every scenario starts with a **World State** — a JSON object that represents the fake user's data for that scenario. The environment reads from this object when responding to tool calls.

```json
{
  "scenario_id": "sc_042",
  "user": {
    "name": "Alex Rivera",
    "email": "alex.rivera@foundry.ai",
    "timezone": "America/New_York"
  },
  "current_datetime": "2026-04-28T09:00:00Z",
  "email": {
    "threads": [
      {
        "thread_id": "t_8f3k1",
        "subject": "RE: Q2 Renewal — Pricing Concern",
        "from": "sarah.chen@acme.com",
        "snippet": "Hi, one concern before Thursday — tier 3 is about 20% above what...",
        "date": "2026-04-25T14:30:00Z",
        "unread": true,
        "message_count": 2,
        "messages": [
          {
            "message_id": "m_002",
            "from": "sarah.chen@acme.com",
            "to": ["alex.rivera@foundry.ai"],
            "cc": [],
            "date": "2026-04-25T14:30:00Z",
            "body": "Hi Alex, one concern before Thursday — tier 3 is about 20% above what Vendor B is quoting. We'd love to stay with you but need this addressed. Can you come with a revised proposal?"
          }
        ]
      }
    ],
    "drafts": [],
    "sent": []
  },
  "calendar": {
    "events": [
      {
        "event_id": "ev_881",
        "title": "Q2 Partnership Review",
        "start": "2026-04-28T14:00:00Z",
        "end": "2026-04-28T15:00:00Z",
        "attendees": ["sarah.chen@acme.com", "mike.patel@acme.com"],
        "location": "Zoom",
        "notes": "Discuss Q2 renewal terms",
        "organizer": "alex.rivera@foundry.ai"
      }
    ]
  },
  "notion": {
    "databases": {
      "partnerships": [
        {
          "row_id": "nr_044",
          "title": "Acme Corp — Q2 Renewal",
          "status": "in_progress",
          "owner": "alex.rivera@foundry.ai",
          "due_date": "2026-05-01",
          "properties": {
            "deal_value": "$120k",
            "action_items": ["Send updated SLA doc", "Confirm pricing tiers"]
          }
        }
      ]
    }
  },
  "slack": {
    "channels": {
      "#partnerships": [
        {
          "ts": "1714250000.000100",
          "user": "mike.patel",
          "text": "Acme call confirmed for 2pm Thursday"
        }
      ]
    }
  }
}
```

This world state is the **ground truth**. Every tool call in the trajectory is answered by reading from it.

---

### 1.2 Mock Environment Functions

Each tool in the schema maps to a mock function. The function takes `(parameters, world_state)` and returns a `tool_result` JSON.

Below is the implementation design for each tool (Python pseudocode):

```python
class MockEnvironment:
    def __init__(self, world_state: dict):
        self.state = world_state
        self.draft_counter = 0
        self.sent_log = []

    def execute(self, tool_name: str, parameters: dict) -> dict:
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return self._error("UNKNOWN_TOOL", f"No tool named {tool_name}")
        try:
            return handler(parameters)
        except Exception as e:
            return self._error("INTERNAL_ERROR", str(e))

    # ── EMAIL ──────────────────────────────────────────────────

    def _tool_search_email(self, p):
        query = p.get("query", "").lower()
        max_results = min(p.get("max_results", 5), 20)
        from_filter = p.get("from", "").lower()

        results = []
        for thread in self.state["email"]["threads"]:
            match = query in thread["subject"].lower() or query in thread["snippet"].lower()
            sender_match = not from_filter or from_filter in thread["from"].lower()
            if match and sender_match:
                results.append({k: v for k, v in thread.items() if k != "messages"})

        return self._success({
            "threads": results[:max_results],
            "total_matches": len(results)
        })

    def _tool_read_email(self, p):
        thread_id = p.get("thread_id")
        thread = next((t for t in self.state["email"]["threads"]
                       if t["thread_id"] == thread_id), None)
        if not thread:
            return self._error("THREAD_NOT_FOUND", f"No thread with id {thread_id}")
        if p.get("mark_as_read", True):
            thread["unread"] = False
        return self._success({
            "thread_id": thread["thread_id"],
            "subject": thread["subject"],
            "messages": thread["messages"]
        })

    def _tool_draft_email(self, p):
        self.draft_counter += 1
        draft_id = f"d_{self.draft_counter:04d}"
        draft = {
            "draft_id": draft_id,
            "to": p["to"],
            "subject": p["subject"],
            "body": p["body"],
            "cc": p.get("cc", []),
            "reply_to_thread_id": p.get("reply_to_thread_id"),
            "sent": False
        }
        self.state["email"]["drafts"].append(draft)
        return self._success({"draft_id": draft_id, "preview_url": f"gmail://drafts/{draft_id}"})

    def _tool_send_email(self, p):
        draft_id = p.get("draft_id")
        draft = next((d for d in self.state["email"]["drafts"]
                      if d["draft_id"] == draft_id), None)
        if not draft:
            return self._error("DRAFT_NOT_FOUND", f"No draft with id {draft_id}")
        if draft["sent"]:
            return self._error("DRAFT_ALREADY_SENT", "This draft was already sent")
        draft["sent"] = True
        sent_record = {**draft, "message_id": f"m_sent_{self.draft_counter:04d}",
                       "sent_at": self.state["current_datetime"]}
        self.state["email"]["sent"].append(sent_record)
        return self._success({"message_id": sent_record["message_id"],
                               "sent_at": sent_record["sent_at"]})

    # ── CALENDAR ───────────────────────────────────────────────

    def _tool_check_calendar(self, p):
        date = p.get("date", self.state["current_datetime"][:10])
        events = [e for e in self.state["calendar"]["events"]
                  if e["start"].startswith(date)]
        return self._success({"events": events})

    def _tool_create_event(self, p):
        # Check for conflicts
        new_start = f"{p['date']}T{p['start_time']}:00Z"
        for e in self.state["calendar"]["events"]:
            if e["start"][:10] == p["date"]:
                # Simplified overlap check
                if e["start"] == new_start:
                    return self._error("CONFLICT",
                        f"Conflicts with existing event: {e['title']}")
        event_id = f"ev_{len(self.state['calendar']['events']) + 900}"
        new_event = {
            "event_id": event_id,
            "title": p["title"],
            "start": new_start,
            "end": new_start,  # simplified
            "attendees": p.get("attendees", []),
            "location": p.get("location", ""),
            "notes": p.get("notes", ""),
            "organizer": self.state["user"]["email"]
        }
        self.state["calendar"]["events"].append(new_event)
        return self._success({"event_id": event_id,
                               "calendar_link": f"https://calendar.google.com/ev/{event_id}"})

    # ── NOTION ─────────────────────────────────────────────────

    def _tool_query_notion(self, p):
        db_name = p.get("database")
        db = self.state["notion"]["databases"].get(db_name)
        if db is None:
            return self._error("DATABASE_NOT_FOUND", f"No database named {db_name}")
        filter_val = p.get("filter", "").lower()
        status_filter = p.get("status")
        limit = min(p.get("limit", 10), 20)
        rows = [r for r in db
                if (not filter_val or filter_val in r["title"].lower())
                and (not status_filter or r.get("status") == status_filter)]
        return self._success({"rows": rows[:limit], "total_rows": len(rows)})

    # ── CONTROL FLOW ───────────────────────────────────────────

    def _tool_ask_user(self, p):
        # In generation mode, a separate "user simulator" provides the response
        # This is injected by the generation pipeline, not the environment
        raise NotImplementedError("ask_user responses are injected by user simulator")

    def _tool_done(self, p):
        return self._success({"acknowledged": True})

    # ── HELPERS ────────────────────────────────────────────────

    def _success(self, data: dict) -> dict:
        return {"status": "success", "data": data}

    def _error(self, code: str, message: str) -> dict:
        return {"status": "error", "error": {"code": code, "message": message}}
```

---

### 1.3 The User Simulator

`ask_user` is special — the environment can't answer it alone. You need a **user simulator** that plays the role of the human responding to confirmation prompts.

The user simulator is a small Claude prompt that receives:
- The original task
- The question being asked
- The options presented

And returns a realistic user reply.

```python
USER_SIMULATOR_PROMPT = """
You are simulating a busy professional named {user_name}.
They gave this original task: "{original_task}"

The assistant is now asking them:
Question: "{question}"
Context: "{context}"
Options: {options}

Reply as {user_name} would — naturally, briefly.
Pick one of the options or give a short freeform reply.
Do NOT explain your choice. Just reply.
"""
```

For gold trajectories, human annotators also review and override user simulator responses to ensure they're natural and non-trivial.

---

## Part 2: Scenario Design

The environment is the container. **Scenarios** are the content that fills it — the world state + the task prompt that kicks off a trajectory.

### 2.1 Scenario Taxonomy

To hit 5k–10k diverse trajectories without repetition, you need a **taxonomy** that systematically varies along three axes:

```
AXIS 1: TASK TYPE (what the user wants)
├── Single-tool (1-2 tool calls)           ~15% of corpus
├── Multi-tool, linear (3-5 calls)         ~40% of corpus
├── Multi-tool, branching (uses ask_user)  ~30% of corpus
└── Error recovery (environment returns    ~15% of corpus
    an error mid-trajectory)

AXIS 2: DOMAIN (which tool categories)
├── Communication only
├── Calendar only
├── Notion only
├── Communication + Calendar
├── Communication + Notion
├── Calendar + Notion
└── All three

AXIS 3: DIFFICULTY MODIFIERS
├── Ambiguous request (model must ask_user to clarify)
├── Conflicting information (email says one thing, Notion another)
├── Missing information (model must search before acting)
├── High-stakes action (requires confirmation before send/create)
└── Error state (environment returns CONFLICT, NOT_FOUND, etc.)
```

Crossing these axes gives you the combinatorial space to generate unique, non-repetitive scenarios.

---

### 2.2 Scenario Template

Each scenario is a structured object — not just a task prompt:

```json
{
  "scenario_id": "sc_042",
  "task_type": "multi_tool_branching",
  "domain": "communication+calendar",
  "difficulty_modifiers": ["high_stakes_action", "missing_information"],
  "task_prompt": "Prep me for my 2pm Acme meeting and send Sarah a quick note that I've reviewed her pricing concern.",
  "world_state": { ... },
  "expected_tool_sequence": [
    "check_calendar",
    "search_email",
    "read_email",
    "query_notion",
    "draft_email",
    "ask_user",
    "send_email",
    "done"
  ],
  "critical_assertions": [
    "Model must call ask_user before send_email (external recipient)",
    "Model must read the email thread, not just the snippet",
    "done.summary must mention the pricing concern",
    "done.actions_taken must include the sent email"
  ],
  "known_failure_modes": [
    "Model sends email without confirmation",
    "Model invents Notion data without calling query_notion",
    "Model skips read_email after search_email"
  ]
}
```

`critical_assertions` and `known_failure_modes` are used by human annotators and the auto-scorer during quality filtering.

---

### 2.3 Scenario Count Planning

To reach 10k trajectories from ~2k–3k unique scenarios:

| Strategy | Scenarios | Trajectories Generated |
|----------|-----------|----------------------|
| Unique world states | 2,000 | 2,000 (1 per state) |
| World state variants (same state, different task prompt) | 2,000 | 4,000 |
| User simulator variation (same scenario, different `ask_user` response path) | — | +2,000 |
| Error injection (same scenario, environment returns an error once) | — | +1,500 |
| Negative examples (model makes a mistake, trajectory shows correction) | — | +500 |
| **Total** | | **~10,000** |

The key insight: you don't need 10k unique world states. You need 2k well-designed ones, with systematic variation layered on top.

---

## Part 3: Gold Trajectory Generation Pipeline

This is the hybrid pipeline — model drafts, humans verify and fix.

### 3.1 Full Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TRAJECTORY GENERATION PIPELINE                   │
│                                                                     │
│  STAGE 1: SCENARIO GENERATION                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Script generates scenario JSON from taxonomy + templates    │   │
│  │ World states populated with realistic fake entity names,    │   │
│  │ email content, calendar events, Notion rows                 │   │
│  │ Output: 2,000–3,000 scenario JSON files                     │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                          │                                          │
│                          ▼                                          │
│  STAGE 2: MODEL DRAFT GENERATION                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ For each scenario:                                          │   │
│  │   - Initialize MockEnvironment with world_state            │   │
│  │   - Run generation loop:                                    │   │
│  │       Model receives task_prompt                           │   │
│  │       Model emits <think> + <tool_call>                    │   │
│  │       Environment executes call, returns <tool_result>     │   │
│  │       Repeat until <done> or max_steps exceeded            │   │
│  │   - Save full trace as candidate trajectory                │   │
│  │ Output: candidate_trajectories/                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                          │                                          │
│                          ▼                                          │
│  STAGE 3: AUTO-SCORING (first-pass filter)                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ For each candidate trajectory, run rule-based checks:       │   │
│  │   ✅ Ends with <done>                                       │   │
│  │   ✅ All IDs chain from prior tool_results                  │   │
│  │   ✅ No tool called after done                              │   │
│  │   ✅ Every tool_call preceded by <think>                    │   │
│  │   ✅ ask_user called before irreversible actions            │   │
│  │   ✅ Expected tool sequence matches (fuzzy)                 │   │
│  │   ✅ done.summary is non-empty and > 50 chars               │   │
│  │   ✅ Step count within acceptable range (2–12)              │   │
│  │                                                             │   │
│  │ Score: 0–8. Threshold ≥ 6 passes to human review           │   │
│  │ Score < 6: flagged for regeneration or discard             │   │
│  │ Output: passed/ and flagged/ buckets                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                          │                                          │
│                          ▼                                          │
│  STAGE 4: HUMAN ANNOTATION                                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Annotators see: task_prompt | world_state | candidate trace │   │
│  │ Annotation tasks (one per trajectory):                      │   │
│  │                                                             │   │
│  │   A. VERIFY: Is the final answer correct and complete?      │   │
│  │   B. VERIFY: Did model check before acting (no shortcuts)?  │   │
│  │   C. VERIFY: Are <think> blocks substantive?                │   │
│  │   D. EDIT: Fix any incorrect tool parameters                │   │
│  │   E. EDIT: Fix any missing ask_user confirmations           │   │
│  │   F. EDIT: Improve done.summary if shallow or incomplete    │   │
│  │   G. VERDICT: Accept / Edit-and-accept / Discard           │   │
│  │                                                             │   │
│  │ Target annotation time: 4–7 min per trajectory              │   │
│  │ Output: gold_trajectories/ (annotator-verified)            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                          │                                          │
│                          ▼                                          │
│  STAGE 5: FORMATTING FOR SFT                                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Convert gold trajectories → training format                 │   │
│  │ Each multi-turn trajectory becomes:                         │   │
│  │   messages: [                                               │   │
│  │     {role: "system", content: SYSTEM_PROMPT},               │   │
│  │     {role: "user", content: task_prompt},                   │   │
│  │     {role: "assistant", content: <think>+<tool_call>},      │   │
│  │     {role: "tool", content: <tool_result>},                 │   │
│  │     {role: "assistant", content: <think>+<tool_call>},      │   │
│  │     ... (n turns)                                           │   │
│  │     {role: "assistant", content: <think>+done}              │   │
│  │   ]                                                         │   │
│  │ Output: sft_data.jsonl                                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

### 3.2 Stage 2 in Detail: The Generation Loop

This is the core loop that runs for every scenario:

```python
def generate_trajectory(scenario: dict, model_client, max_steps=15) -> dict:
    env = MockEnvironment(scenario["world_state"])
    user_sim = UserSimulator(scenario["task_prompt"])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": scenario["task_prompt"]}
    ]

    trace = []
    steps = 0

    while steps < max_steps:
        # Model generates next turn
        response = model_client.complete(messages)
        assistant_turn = response.content  # <think>...</think><tool_call>...</tool_call>

        messages.append({"role": "assistant", "content": assistant_turn})
        trace.append({"role": "assistant", "content": assistant_turn})

        # Parse tool call from response
        tool_call = parse_tool_call(assistant_turn)  # extracts name + parameters

        if tool_call is None:
            # Model produced prose without a tool call — flag as format error
            return {"status": "format_error", "trace": trace}

        # Handle ask_user specially — inject user simulator response
        if tool_call["name"] == "ask_user":
            user_response = user_sim.respond(
                question=tool_call["parameters"]["question"],
                options=tool_call["parameters"].get("options", [])
            )
            tool_result = {
                "status": "success",
                "data": {"user_response": user_response}
            }
        else:
            # Normal tool call — execute against mock environment
            tool_result = env.execute(tool_call["name"], tool_call["parameters"])

        result_xml = f"<tool_result>\n{json.dumps(tool_result, indent=2)}\n</tool_result>"
        messages.append({"role": "tool", "content": result_xml})
        trace.append({"role": "tool", "content": result_xml})

        # Check if done
        if tool_call["name"] == "done":
            return {
                "status": "complete",
                "scenario_id": scenario["scenario_id"],
                "trace": trace,
                "final_world_state": env.state,
                "step_count": steps + 1
            }

        steps += 1

    # Exceeded max_steps without done
    return {"status": "timeout", "trace": trace}
```

---

### 3.3 Stage 3 in Detail: The Auto-Scorer

```python
def auto_score(trajectory: dict, scenario: dict) -> dict:
    trace = trajectory["trace"]
    tool_calls = extract_tool_calls(trace)      # list of {name, parameters, step}
    tool_results = extract_tool_results(trace)  # list of {status, data/error, step}
    think_blocks = extract_think_blocks(trace)  # list of strings
    ids_in_results = extract_all_ids(tool_results)

    scores = {}

    # Rule 1: Ends with done
    scores["ends_with_done"] = tool_calls[-1]["name"] == "done" if tool_calls else False

    # Rule 2: No tool call after done
    done_step = next((c["step"] for c in tool_calls if c["name"] == "done"), None)
    scores["no_calls_after_done"] = all(c["step"] <= done_step for c in tool_calls)

    # Rule 3: Every tool_call preceded by non-empty <think>
    scores["think_before_every_call"] = all(
        len(think_blocks[i].strip()) > 20
        for i in range(len(tool_calls))
        if i < len(think_blocks)
    )

    # Rule 4: No hallucinated IDs
    used_ids = extract_ids_from_parameters(tool_calls)
    scores["no_hallucinated_ids"] = all(
        id_val in ids_in_results or id_val == ""
        for id_val in used_ids
    )

    # Rule 5: ask_user before irreversible actions
    send_steps = [c["step"] for c in tool_calls if c["name"] in ("send_email", "reply_email")]
    ask_steps = [c["step"] for c in tool_calls if c["name"] == "ask_user"]
    scores["confirmation_before_send"] = all(
        any(a < s for a in ask_steps) for s in send_steps
    ) if send_steps else True

    # Rule 6: Expected tool sequence match (fuzzy — order-independent subset check)
    expected = set(scenario["expected_tool_sequence"])
    actual = set(c["name"] for c in tool_calls)
    scores["tool_sequence_match"] = len(expected & actual) / len(expected) >= 0.75

    # Rule 7: done summary quality
    done_call = next((c for c in tool_calls if c["name"] == "done"), None)
    scores["done_summary_quality"] = (
        len(done_call["parameters"].get("summary", "")) > 50
        if done_call else False
    )

    # Rule 8: Step efficiency
    scores["step_efficiency"] = 2 <= trajectory["step_count"] <= 12

    total = sum(scores.values())
    return {
        "total": total,
        "max": 8,
        "passed": total >= 6,
        "breakdown": scores
    }
```

---

### 3.4 Stage 4 in Detail: Annotator Interface Design

The annotation UI should show three panels side-by-side:

```
┌───────────────────┬─────────────────────────────┬──────────────────┐
│  SCENARIO CONTEXT │    CANDIDATE TRAJECTORY      │  ANNOTATION FORM │
│                   │                              │                  │
│  Task:            │  USER: "Prep me for the..."  │  [A] Final ans.  │
│  "Prep me for..." │                              │  ○ Correct       │
│                   │  ASSISTANT:                  │  ○ Wrong         │
│  World State      │  <think>                     │  ○ Incomplete    │
│  summary:         │  Two subtasks: prep + email  │                  │
│  - 1 email from   │  </think>                    │  [B] Model check │
│    Sarah re:      │  <tool_call>                 │  before acting?  │
│    pricing        │  check_calendar              │  ○ Yes           │
│  - 2pm meeting    │  </tool_call>                │  ○ No            │
│    with Acme      │                              │                  │
│  - 2 Notion items │  TOOL:                       │  [C] Think depth │
│    overdue        │  <tool_result>               │  ○ Substantive   │
│                   │  { events: [...] }           │  ○ Shallow       │
│  Expected tools:  │  </tool_result>              │                  │
│  check_calendar   │                              │  [D-F] Edit box  │
│  search_email     │  ASSISTANT:                  │  ┌─────────────┐ │
│  read_email       │  <think>...                  │  │ [editable   │ │
│  query_notion     │  </think>                    │  │  trajectory │ │
│  draft_email      │  <tool_call>                 │  │  text]      │ │
│  ask_user         │  search_email                │  └─────────────┘ │
│  send_email       │  </tool_call>                │                  │
│  done             │  ...                         │  [G] Verdict     │
│                   │  <tool_call>                 │  ○ Accept        │
│  Auto-score: 7/8  │  done { summary: "..." }     │  ○ Edit+accept   │
│  PASS             │  </tool_call>                │  ○ Discard       │
└───────────────────┴─────────────────────────────┴──────────────────┘
```

**Key annotator instructions:**

The most common issues to fix are:
1. **Model skips `ask_user` before sending** — annotator adds the confirmation step
2. **`<think>` block is trivial** ("I will now search for emails") — annotator deepens it
3. **`done.summary` is a raw data dump** instead of a synthesized insight — annotator rewrites
4. **Model references snippet instead of reading the full email** — annotator adds `read_email` step

---

### 3.5 Annotator Throughput Math

| Metric | Value |
|--------|-------|
| Target gold trajectories | 10,000 |
| Auto-scorer pass rate (estimated) | ~70% |
| Candidate trajectories needed | ~14,300 |
| Scenarios needed | ~3,000 (some with variants) |
| Annotation time per trajectory | 5 min average |
| Total annotation hours | ~833 hours |
| With 3 annotators in parallel | ~35 working days |
| With 6 annotators in parallel | ~17 working days |

Hitting 10k in under 4 weeks requires 6 annotators or starting with a 5k pilot using 3.

---

## Part 4: Negative Examples & Error Recovery Traces

15% of your corpus should be **negative examples** — trajectories where the model encounters an error or a bad path, and recovers.

These are the highest-signal training examples because they teach the model what to do when the real world doesn't cooperate.

### Types of Error Traces to Include

**Type 1: Tool returns error — model recovers**
```
search_email → THREAD_NOT_FOUND
<think> Thread ID not found. I should search again with broader query. </think>
search_email (broader query) → success
```

**Type 2: Calendar conflict — model surfaces to user**
```
create_event → CONFLICT: "Conflicts with existing event: Budget Review"
<think> There's a conflict at 2pm. I can't just override it — ask user. </think>
ask_user: "There's a conflict with your Budget Review at 2pm. Should I schedule for 3pm instead?"
user: "Yes, 3pm works"
create_event (3pm) → success
```

**Type 3: Ambiguous task — model asks before acting**
```
User: "Send a follow-up to the Acme team"
<think> "Acme team" is ambiguous — could be Sarah, could be Mike, could be both.
I should clarify before drafting. </think>
ask_user: "Who should I send this to — Sarah, Mike, or the whole team?"
user: "Just Sarah"
search_email → read_email → draft_email → ask_user → send_email → done
```

**Type 4: Model finds conflicting info — surfaces it**
```
read_email: Sarah says meeting is Thursday
check_calendar: event is on Wednesday
<think> Email says Thursday but calendar says Wednesday. This is a discrepancy
the user should know about. </think>
ask_user: "Heads up — Sarah's email references Thursday but your calendar shows Wednesday. Which is correct?"
```

---

## Part 5: Quality Filters for the Final Dataset

Before training, run these final filters on the gold corpus:

| Filter | Threshold | Action |
|--------|-----------|--------|
| Trajectory length | 2–12 steps | Discard outliers |
| Duplicate detection | Cosine sim > 0.92 on task_prompt | Deduplicate |
| Domain balance | No single domain > 35% of corpus | Resample |
| Task type balance | error_recovery ≥ 10% of corpus | Oversample if needed |
| Annotator agreement | < 2 edits per trajectory on average | Flag batches with high edit rate for prompt review |
| `ask_user` frequency | 25–40% of trajectories contain at least 1 `ask_user` | Check distribution |
| Average think block length | > 40 chars | Reject batch if avg drops below |

---

## Part 6: File Structure

```
data/
├── scenarios/
│   ├── sc_001.json
│   ├── sc_002.json
│   └── ...
├── world_states/
│   ├── ws_001.json
│   └── ...
├── candidates/
│   ├── auto_pass/
│   └── auto_flag/
├── gold/
│   ├── accepted/
│   └── edited/
├── sft/
│   └── sft_data.jsonl       ← final training file
└── scripts/
    ├── generate_scenarios.py
    ├── run_generation_loop.py
    ├── auto_score.py
    └── format_for_sft.py
```

---

*Next: Design the system prompt that the model sees at the start of every trajectory, and define how it describes the tool schema in-context.*
