"""
generation_loop.py
------------------
Foundry [Flow] — Trajectory Generation Loop

Runs a scenario through a local Ollama model + MockEnvironment to produce
a candidate trajectory for SFT training.

Pipeline position:
    world_state_generator.py  →  [world_states/*.json]
    generation_loop.py        →  [candidates/*.json]

Usage:
    # Single scenario
    python generation_loop.py --world_state world_states/ws_0001.json

    # Batch (all world states in a folder)
    python generation_loop.py --batch world_states/ --task_prompts prompts.json

    # Dry run (no API calls, prints what would happen)
    python generation_loop.py --world_state world_states/ws_0001.json --dry_run
"""

import re
import os
import sys
import json
import time
import argparse
import textwrap
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import ollama
load_dotenv()

from mock_environment import MockEnvironment
from world_state_generator import WorldStateGenerator


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# Matches SYSTEM_PROMPT_DESIGN v0.1 — keep in sync with that document
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Flow, an AI executive assistant built by Foundry.

You work on behalf of a specific user — reading their email, checking their calendar, managing their tasks in Notion, and communicating on their behalf. Your job is to take tasks from start to finish, using the tools available to you, and surface a clear result when you're done.

You think before every action. You act with care. You never surprise your user with something they didn't ask for or wouldn't have approved.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every turn you produce must follow this exact structure:

<think>
Your reasoning about what to do next. What do you know? What are you uncertain about? Why are you choosing this tool? What matters here?
This block must not be empty or trivial.
</think>
<tool_call>
{
  "name": "<tool_name>",
  "parameters": { ... }
}
</tool_call>

You then receive a tool result:

<tool_result>
{ "status": "success" | "error", "data": { ... } }
</tool_result>

After which you produce your next <think> + <tool_call>.
When the task is complete, your final call is always `done`.

Rules:
- One tool call per turn. Never emit two <tool_call> blocks in one turn.
- Never produce prose outside a <think> block or done.summary.
- Never call any tool after done.
- Never emit a <tool_call> without a preceding <think> block.
- If a tool returns an error, reason about it in <think> and decide how to recover.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BEHAVIORAL DEFAULTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CONFIRM BEFORE ACTING when:
- Sending an email or Slack message to anyone outside the user's company
- Creating a calendar event with external attendees (invites go out immediately)
- Sending anything the user hasn't explicitly pre-approved in this conversation
Use `ask_user` to confirm. Include what you're about to do and why.

DO NOT CONFIRM when:
- Reading, searching, or querying (these are safe, reversible)
- The user has explicitly said "go ahead", "send it", "do it" in this conversation
- Creating internal drafts, tasks, or events with no external attendees

CHECK BEFORE ASSUMING:
- If the task references an email, search for it — don't assume what it says
- If the task references a meeting, check the calendar — don't use a time from memory
- If the task references a Notion item, query it — don't invent its status
Never fabricate data that a tool call would surface.

WHEN STUCK OR AMBIGUOUS:
- Use `ask_user` to ask the single most important clarifying question
- Never ask multiple questions in one `ask_user` call
- If you can make a reasonable inference without blocking the user, do so and note it in done.summary

EFFICIENCY:
- Don't make tool calls whose results you won't use
- Don't re-read data you already have in context
- A 4-step trace that answers the question well is better than an 8-step trace

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOOLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EMAIL
search_email(query, from?, date_range?, max_results?, folder?)
  Search the inbox. Returns thread summaries with thread_id.
  Does not return full body — use read_email for that.

read_email(thread_id, mark_as_read?)
  Read full content of a thread. Always read before summarizing or replying.

draft_email(to, subject, body, cc?, reply_to_thread_id?)
  Create a draft. Returns draft_id. Does not send.
  Always draft before sending.

send_email(draft_id)
  Send a previously created draft. Irreversible.
  Requires ask_user confirmation if recipient is external.

reply_email(thread_id, body, send_immediately?, cc?)
  Reply to an existing thread. send_immediately defaults to false.

SLACK
send_slack_message(channel, message, thread_ts?)
  Send to a channel (#name) or DM (@handle). Requires confirmation if external.

read_slack_thread(channel, thread_ts?, limit?)
  Read recent messages from a channel or thread.

CALENDAR
check_calendar(date?, time_range?, lookahead_days?, include_declined?)
  Fetch events for a date window. date defaults to today.

create_event(title, date, start_time, duration_minutes, attendees?, location?, notes?, send_invites?)
  Create a calendar event. On CONFLICT error, surface to user via ask_user.
  Requires ask_user confirmation if external attendees are included.

update_event(event_id, title?, date?, start_time?, duration_minutes?, notes?, notify_attendees?)
  Update an existing event using event_id from check_calendar.

NOTION
query_notion(database, filter?, status?, limit?)
  Query a Notion database. Known databases: "partnerships", "tasks", "projects".

create_task(title, database, due_date?, assignee?, notes?, priority?)
  Create a task in a Notion database.

update_task(task_id, status?, due_date?, notes?, assignee?, priority?)
  Update a task. notes are appended, not replaced.

CONTROL FLOW
ask_user(question, context?, options?)
  Pause and ask the user a question. One question per call.

done(summary, actions_taken?, follow_ups?)
  Signal task completion. Always the last call.
  summary: written for the user — clear, direct, synthesized.
  actions_taken: only things that actually happened.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ERRORS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When a tool returns "status": "error":
1. Read the error code in your <think> block
2. Decide: can you recover automatically, or do you need to ask the user?
3. If recoverable (e.g. broaden search, try different date), recover silently
4. If not (e.g. CONFLICT, critical NOT_FOUND), use ask_user to surface it
5. Never silently swallow an error and continue as if the call succeeded

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IDENTITY & LIMITS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are Flow. You work for one user, in their context, with their data.
Retrieve before you summarize. Draft before you send.
Confirm before you act on the world. Think before every step.
When the task is done, say so clearly."""


# ─────────────────────────────────────────────────────────────────────────────
# SAMPLE TASK PROMPTS
# Covers all 5 FlowBench categories and difficulty axes
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_TASK_PROMPTS = {
    "communication": [
        "What's the latest email from any external partners? Summarise the key ask.",
        "Draft a reply to the most recent unread email acknowledging their concern.",
        "Check if there's any unread email I should action today.",
    ],
    "planning": [
        "What does my calendar look like today and tomorrow?",
        "I need to schedule a 45-minute sync with the team. Find a free slot this week.",
        "Check my calendar for the next 2 days and flag any conflicts.",
    ],
    "orchestration": [
        "Prep me for my next meeting — pull context from email and Notion.",
        "Find the most urgent open task in Notion and draft a Slack update about it.",
        "Check my inbox and calendar, then give me a morning briefing.",
    ],
    "ambiguity": [
        "Handle my inbox.",
        "Send an update to the team.",
        "Follow up with the partner.",
    ],
    "notion": [
        "What open tasks are overdue in Notion?",
        "Show me the status of our active partnerships.",
        "Create a high-priority task to review the Q2 pricing proposal.",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# XML PARSER
# Extracts <think>, <tool_call>, and <tool_result> blocks from raw text
# ─────────────────────────────────────────────────────────────────────────────

class TraceParser:

    @staticmethod
    def extract_think(text: str) -> str | None:
        m = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
        return m.group(1).strip() if m else None

    @staticmethod
    def extract_tool_call(text: str) -> dict | None:
        """Extract and parse the first <tool_call> JSON block."""
        m = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)
        if not m:
            return None
        raw = m.group(1).strip()
        try:
            parsed = json.loads(raw)
            if "name" not in parsed:
                return None
            if "parameters" not in parsed:
                parsed["parameters"] = {}
            return parsed
        except json.JSONDecodeError:
            return None

    @staticmethod
    def format_tool_result(result: dict) -> str:
        return f"<tool_result>\n{json.dumps(result, indent=2)}\n</tool_result>"

    @staticmethod
    def has_multiple_tool_calls(text: str) -> bool:
        return len(re.findall(r"<tool_call>", text)) > 1


# ─────────────────────────────────────────────────────────────────────────────
# USER SIMULATOR
# Provides realistic responses to ask_user tool calls during generation
# ─────────────────────────────────────────────────────────────────────────────

class UserSimulator:

    def __init__(self, original_task: str, model: str = "qwen2.5:14b", dry_run: bool = False):
        self.original_task = original_task
        self.model = model
        self.dry_run = dry_run

    def respond(self, question: str, context: str = "", options: list = None) -> str:
        """
        Generate a realistic user reply to an ask_user question.
        In dry_run mode returns a canned response without an API call.
        """
        if self.dry_run:
            if options:
                return options[0]
            return "Yes, go ahead."

        options_text = ""
        if options:
            options_text = "\nOptions presented: " + ", ".join(f'"{o}"' for o in options)

        prompt = f"""You are simulating a busy professional who gave this task to their AI assistant:
"{self.original_task}"

The assistant is now pausing to ask:
Question: "{question}"
Context: "{context}"{options_text}

Reply naturally and briefly as this person would. If options are given, pick one or rephrase slightly.
Do NOT explain your choice. Just reply as the user would."""

        return self._call_api(prompt)

    def _call_api(self, prompt: str) -> str:
        response = ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048, "num_predict": 150, "temperature": 0.5},
        )
        return response.message.content.strip()


# ─────────────────────────────────────────────────────────────────────────────
# OLLAMA API CLIENT
# Thin wrapper — sends messages, returns raw assistant text
# ─────────────────────────────────────────────────────────────────────────────

class OllamaClient:

    def __init__(self, model: str = "qwen2.5:14b"):
        self.model = model

    def complete(self, messages: list, system: str = "") -> str:
        """Send messages to Ollama, return assistant text."""
        try:
            ollama_messages = [{"role": "system", "content": system}] + messages
            response = ollama.chat(
                model=self.model,
                messages=ollama_messages,
                options={
                    "num_ctx":     8192,   # CRITICAL: default is 2048 which truncates
                                           # system prompt + multi-turn context mid-trace
                    "num_predict": 2048,   # max tokens per response turn
                    "temperature": 0.3,    # low temp = consistent XML formatting
                },
            )
            return response.message.content.strip()
        except Exception as e:
            raise RuntimeError(f"Ollama error: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# GENERATION LOOP
# ─────────────────────────────────────────────────────────────────────────────

class GenerationLoop:

    def __init__(
        self,
        client:    OllamaClient,
        max_steps: int   = 15,
        verbose:   bool  = True,
        dry_run:   bool  = False,
        delay:     float = 0.0,
    ):
        self.client    = client
        self.max_steps = max_steps
        self.verbose   = verbose
        self.dry_run   = dry_run
        self.delay     = delay
        self.parser    = TraceParser()

    def run(self, world_state: dict, task_prompt: str) -> dict:
        """
        Run one complete trajectory.

        Returns a result dict with:
          status       : "complete" | "timeout" | "format_error" | "api_error"
          trace        : list of turn dicts
          step_count   : int
          final_world_state : snapshot of env after all tool calls
          metadata     : timing, token estimates, error details
        """
        env      = MockEnvironment(world_state)
        user_sim = UserSimulator(
            original_task=task_prompt,
            model=self.client.model,
            dry_run=self.dry_run
        )

        # Conversation history sent to the API each turn
        messages = [{"role": "user", "content": task_prompt}]

        # Structured trace saved to disk
        trace = [{
            "role":    "user",
            "content": task_prompt,
            "step":    0
        }]

        start_time  = time.perf_counter()
        step        = 0
        retry_count = 0   # tracks format-error retries per step

        self._log(f"\n{'='*60}")
        self._log(f"TASK: {task_prompt}")
        self._log(f"USER: {world_state['user']['name']}")
        self._log(f"TAGS: {world_state.get('scenario_tags', [])}")
        self._log(f"{'='*60}\n")

        while step < self.max_steps:
            step += 1
            self._log(f"── Step {step} ──────────────────────────────")

            # ── 1. Get model response ──────────────────────────────────────
            if self.dry_run:
                assistant_text = self._dry_run_turn(step, world_state, messages)
            else:
                try:
                    assistant_text = self.client.complete(messages, system=SYSTEM_PROMPT)
                except RuntimeError as e:
                    return self._result("api_error", trace, step, env,
                                        start_time, error=str(e))

            # ── 2. Parse think + tool_call ─────────────────────────────────
            think     = self.parser.extract_think(assistant_text)
            tool_call = self.parser.extract_tool_call(assistant_text)

            if self.parser.has_multiple_tool_calls(assistant_text):
                self._log("[WARN] Multiple tool_call blocks detected — taking first only")

            if tool_call is None:
                self._log(f"[FORMAT ERROR] No valid <tool_call> found in response")
                self._log(f"Response preview: {assistant_text[:300]}")
                trace.append({
                    "role": "assistant", "content": assistant_text,
                    "step": step, "parse_error": "no_tool_call"
                })
                if retry_count < 2:
                    retry_count += 1
                    step -= 1   # don't burn a step on a format retry
                    self._log(f"[RETRY {retry_count}/2] Nudging model back to format...")
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your response must use this exact format — no prose outside these tags:\n\n"
                            "<think>\nYour reasoning here.\n</think>\n"
                            "<tool_call>\n"
                            '{"name": "<tool_name>", "parameters": {...}}\n'
                            "</tool_call>"
                        )
                    })
                    continue   # retry this step
                else:
                    return self._result("format_error", trace, step, env, start_time,
                                        error="No <tool_call> after 2 retries")

            # Successful parse — reset retry counter for next step
            retry_count = 0
            self._log(f"THINK:  {(think or '')[:120]}{'...' if think and len(think)>120 else ''}")
            self._log(f"CALL:   {tool_call['name']}({json.dumps(tool_call['parameters'])[:120]})")

            # Record assistant turn in trace
            trace.append({
                "role":       "assistant",
                "content":    assistant_text,
                "think":      think,
                "tool_call":  tool_call,
                "step":       step,
            })
            messages.append({"role": "assistant", "content": assistant_text})

            # ── 3. Execute tool call ───────────────────────────────────────
            tool_name   = tool_call["name"]
            tool_params = tool_call["parameters"]

            if tool_name == "done":
                result_dict = env.execute("done", tool_params)
                result_xml  = self.parser.format_tool_result(result_dict)
                trace.append({
                    "role":    "tool",
                    "content": result_xml,
                    "result":  result_dict,
                    "step":    step,
                })
                self._log(f"RESULT: acknowledged=true")
                self._log(f"\n✓ COMPLETE in {step} steps "
                          f"({time.perf_counter()-start_time:.1f}s)\n")
                return self._result("complete", trace, step, env, start_time)

            elif tool_name == "ask_user":
                # Route to user simulator
                question = tool_params.get("question", "")
                context  = tool_params.get("context", "")
                options  = tool_params.get("options", [])

                self._log(f"  → ask_user: {question[:80]}")
                user_response = user_sim.respond(question, context, options)
                self._log(f"  ← user:     {user_response[:80]}")

                result_dict = {
                    "status": "success",
                    "data":   {"user_response": user_response}
                }

            else:
                result_dict = env.execute(tool_name, tool_params)

            result_xml = self.parser.format_tool_result(result_dict)
            status_str = result_dict["status"]
            if status_str == "error":
                self._log(f"RESULT: ERROR — {result_dict['error']['code']}: "
                          f"{result_dict['error']['message']}")
            else:
                # Compact preview of successful result
                preview = json.dumps(result_dict.get("data", {}))
                self._log(f"RESULT: {status_str} — {preview[:120]}")

            trace.append({
                "role":    "tool",
                "content": result_xml,
                "result":  result_dict,
                "step":    step,
            })
            messages.append({"role": "user", "content": result_xml})

        # Exceeded max_steps
        self._log(f"[TIMEOUT] Exceeded {self.max_steps} steps without done")
        return self._result("timeout", trace, step, env, start_time,
                            error=f"Exceeded max_steps={self.max_steps}")

    # ── PRIVATE HELPERS ───────────────────────────────────────────────────────

    def _result(
        self, status: str, trace: list, step_count: int,
        env: MockEnvironment, start_time: float, error: str = None
    ) -> dict:
        return {
            "status":            status,
            "trace":             trace,
            "step_count":        step_count,
            "final_world_state": env.snapshot(),
            "metadata": {
                "elapsed_seconds": round(time.perf_counter() - start_time, 2),
                "generated_at":    datetime.now(tz=timezone.utc).isoformat(),
                "error":           error,
            }
        }

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    def _dry_run_turn(self, step: int, world_state: dict, messages: list) -> str:
        """
        Produce scripted responses for dry-run mode — no API call needed.
        Cycles through a realistic mini-trajectory to verify the pipeline.
        """
        scripts = {
            1: (
                "I should start by checking what's in the calendar and inbox.",
                "check_calendar",
                {"date": world_state["current_datetime"][:10], "lookahead_days": 1}
            ),
            2: (
                "Calendar retrieved. Now let me check the inbox for relevant emails.",
                "search_email",
                {"query": "update", "max_results": 3}
            ),
            3: (
                "I have enough context. Let me deliver a summary.",
                "done",
                {
                    "summary": "Dry-run complete. Calendar and inbox checked.",
                    "actions_taken": [],
                    "follow_ups": ["Review flagged emails"]
                }
            ),
        }
        s = scripts.get(step, scripts[3])
        think_text, tool_name, params = s
        return (
            f"<think>\n{think_text}\n</think>\n"
            f"<tool_call>\n"
            f"{json.dumps({'name': tool_name, 'parameters': params}, indent=2)}\n"
            f"</tool_call>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TRAJECTORY SAVER
# Writes candidate traces to disk in a structure ready for auto-scoring
# ─────────────────────────────────────────────────────────────────────────────

class TrajectorySaver:

    def __init__(self, output_dir: str = "candidates"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, result: dict, world_state_id: str, task_prompt: str,
             category: str = "unknown") -> str:
        """Save a trajectory result to disk. Returns the file path."""
        ts      = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", world_state_id)
        fname   = f"{safe_id}_{ts}.json"
        fpath   = self.output_dir / fname

        payload = {
            "world_state_id": world_state_id,
            "task_prompt":    task_prompt,
            "category":       category,
            "status":         result["status"],
            "step_count":     result["step_count"],
            "metadata":       result["metadata"],
            "trace":          result["trace"],
            # Final world state snapshot included for state-verification scoring
            "final_world_state": result["final_world_state"],
        }

        with open(fpath, "w") as f:
            json.dump(payload, f, indent=2)

        return str(fpath)


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run the Foundry Flow generation loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          # Single world state, dry run (no API key needed)
          python generation_loop.py --world_state world_states/ws_0001.json --dry_run

          # Single world state, real API call
          python generation_loop.py --world_state world_states/ws_0001.json

          # Batch all world states
          python generation_loop.py --batch world_states/
        """)
    )
    parser.add_argument("--world_state", type=str,
                        help="Path to a single world state JSON file")
    parser.add_argument("--batch", type=str,
                        help="Path to a folder of world state JSON files")
    parser.add_argument("--task_prompt", type=str,
                        default="What does my calendar look like today? Also check if there are any urgent emails.",
                        help="Task prompt to use (single mode)")
    parser.add_argument("--category", type=str, default="orchestration",
                        help="Task category label for the trace metadata")
    parser.add_argument("--all_prompts", action="store_true",
                        help="Run every prompt in SAMPLE_TASK_PROMPTS across all categories (ignores --task_prompt and --category)")
    parser.add_argument("--output_dir", type=str, default="candidates",
                        help="Where to save candidate trajectories")
    parser.add_argument("--max_steps", type=int, default=15,
                        help="Max tool calls before timeout")
    parser.add_argument("--dry_run", action="store_true",
                        help="Skip API calls — use scripted responses to test pipeline")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds to wait between trajectories. Not needed for local Ollama (no rate limits). Use if running against a cloud API.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress step-by-step output")
    parser.add_argument("--model", type=str, default="qwen2.5:14b",
                        help="Local Ollama model (e.g., qwen2.5:14b, mistral-small3.1)")
    args = parser.parse_args()

    # ── Pre-flight: verify Ollama is running and the model is available
    print(f"Model:      {args.model}")
    try:
        models = [m.model for m in ollama.list().models]
        # Strip tag suffix for matching e.g. "qwen2.5:14b" in "qwen2.5:14b-instruct-q8_0"
        base = args.model.split(":")[0]
        matches = [m for m in models if base in m]
        if not matches:
            print(f"\n⚠  Model '{args.model}' not found in Ollama.")
            print(f"   Available models: {models or 'none'}")
            print(f"   Run: ollama pull {args.model}")
            print(f"   Or use: --model <one of the above>\n")
            sys.exit(1)
        print(f"Ollama:     ✓ running  ({len(models)} model(s) available)")
    except Exception as e:
        print(f"\n✗ Cannot connect to Ollama: {e}")
        print(f"  Make sure Ollama is running:  ollama serve")
        print(f"  Then pull your model:         ollama pull {args.model}\n")
        sys.exit(1)

    client  = OllamaClient(model=args.model)
    loop    = GenerationLoop(
        client=client,
        max_steps=args.max_steps,
        verbose=not args.quiet,
        dry_run=args.dry_run,
        delay=args.delay,
    )
    saver   = TrajectorySaver(output_dir=args.output_dir)

    # ── Collect world state files to process
    if args.world_state:
        ws_files = [Path(args.world_state)]
    elif args.batch:
        ws_files = sorted(Path(args.batch).glob("*.json"))
    else:
        print("ERROR: Provide --world_state or --batch")
        sys.exit(1)

    print(f"\nFoundry Flow — Generation Loop")
    print(f"Mode:       {'DRY RUN' if args.dry_run else f'LIVE (Ollama)'}")
    print(f"Files:      {len(ws_files)} world state(s)")
    print(f"Output:     {args.output_dir}/\n")

    # Build the list of (task_prompt, category) pairs to run
    if args.all_prompts:
        prompt_pairs = [
            (prompt, category)
            for category, prompts in SAMPLE_TASK_PROMPTS.items()
            for prompt in prompts
        ]
        print(f"Prompts:    {len(prompt_pairs)} total ({len(SAMPLE_TASK_PROMPTS)} categories)")
    else:
        prompt_pairs = [(args.task_prompt, args.category)]

    results_summary = []

    for ws_file in ws_files:
        with open(ws_file) as f:
            world_state = json.load(f)

        ws_id = world_state.get("world_state_id", ws_file.stem)

        for task_prompt, category in prompt_pairs:
            if loop.delay > 0:
                time.sleep(loop.delay)
            result = loop.run(world_state, task_prompt)

            saved_path = saver.save(
                result,
                world_state_id=ws_id,
                task_prompt=task_prompt,
                category=category,
            )

            results_summary.append({
                "ws_id":    ws_id,
                "category": category,
                "status":   result["status"],
                "steps":    result["step_count"],
                "elapsed":  result["metadata"]["elapsed_seconds"],
                "saved":    saved_path,
            })

    # ── Summary table
    print(f"\n{'─'*80}")
    print(f"{'World State':<12} {'Category':<16} {'Status':<14} {'Steps':>5} {'Time':>7}")
    print(f"{'─'*80}")
    for r in results_summary:
        status_icon = "✓" if r["status"] == "complete" else "✗"
        cat = r.get("category", "")[:15]
        print(f"{r['ws_id']:<12} {cat:<16} {status_icon} {r['status']:<12} "
              f"{r['steps']:>5} {r['elapsed']:>6.1f}s")
    print(f"{'─'*80}")
    total_complete = sum(1 for r in results_summary if r["status"] == "complete")
    n_format_err   = sum(1 for r in results_summary if r["status"] == "format_error")
    n_timeout      = sum(1 for r in results_summary if r["status"] == "timeout")
    print(f"Complete: {total_complete}/{len(results_summary)}  "
          f"format_error: {n_format_err}  timeout: {n_timeout}\n")


if __name__ == "__main__":
    main()