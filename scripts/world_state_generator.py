"""
world_state_generator.py
------------------------
Foundry [Flow] — World State Generator

Generates realistic, diverse world states for trajectory generation and FlowBench.
Each world state is a self-consistent snapshot of a user's digital environment:
  - Gmail inbox (threads with full message bodies)
  - Google Calendar (events, some with conflicts)
  - Notion (databases: partnerships, tasks, projects)
  - Slack (channels with message history)

Usage:
    from world_state_generator import WorldStateGenerator
    gen = WorldStateGenerator(seed=42)
    world_state = gen.generate(scenario_tags=["conflict", "external_email"])
    
    # Or generate a batch
    states = gen.generate_batch(n=50)
"""

import random
import json
import hashlib
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# ENTITY POOLS
# These are the raw materials the generator draws from.
# Expanding these pools is the easiest way to increase variety.
# ─────────────────────────────────────────────────────────────────────────────

FIRST_NAMES = [
    "Sarah", "Marcus", "Priya", "James", "Aisha", "Tom", "Elena", "Raj",
    "Mei", "Carlos", "Nadia", "Ben", "Fatima", "Luke", "Ingrid", "Omar",
    "Sophie", "Arjun", "Chloe", "Derek", "Yuki", "Hassan", "Lena", "Dev"
]

LAST_NAMES = [
    "Chen", "Okafor", "Patel", "Rivera", "Kim", "Walsh", "Novak", "Sharma",
    "Tanaka", "Lopez", "Müller", "Singh", "Park", "Kowalski", "Andersen",
    "Al-Rashid", "Dubois", "Nakamura", "Berg", "Santos", "Fischer", "Rao"
]

COMPANIES = [
    "Acme Corp", "Nexus Systems", "Vertex AI", "Orbit Labs", "Pinnacle Tech",
    "Meridian Capital", "Solaris Group", "Quantum Dynamics", "Atlas Ventures",
    "Horizon Health", "Stratum Analytics", "Vantage Point", "Crestline Partners",
    "Nova Engineering", "Apex Solutions", "Bridgewater Consulting", "Ironclad Security"
]

INTERNAL_DOMAINS = ["foundry.ai", "foundry.co", "flow.ai"]

ROLES = [
    "CEO", "CTO", "CFO", "VP of Engineering", "VP of Sales", "Head of Product",
    "Director of Partnerships", "Engineering Manager", "Senior Engineer",
    "Product Manager", "Marketing Lead", "Legal Counsel", "Chief of Staff",
    "Account Executive", "Customer Success Manager"
]

MEETING_TYPES = [
    "Quarterly Business Review", "1:1", "Sprint Planning", "All Hands",
    "Partnership Sync", "Investor Update", "Design Review", "Post-Mortem",
    "Roadmap Review", "Budget Discussion", "Kickoff Meeting", "Demo Day",
    "Contract Review", "Pipeline Review", "Team Standup"
]

EMAIL_SUBJECTS = {
    "partnership": [
        "RE: Q{q} Renewal — Pricing Concern",
        "Partnership Proposal — {company}",
        "Follow-up: Contract Terms",
        "RE: SLA Amendment Request",
        "Tier {tier} Pricing Discussion",
    ],
    "internal": [
        "RE: Action Items from {meeting}",
        "Quick question on {topic}",
        "{project} — Status Update",
        "RE: Budget Approval Needed",
        "Team: Please review before {day}",
    ],
    "vendor": [
        "Invoice #{num} — {company}",
        "RE: Shipment Delay",
        "Contract Renewal — {company}",
        "Technical Support Request",
        "Onboarding Checklist — {company}",
    ],
    "investor": [
        "Board Meeting Prep — {date}",
        "RE: Q{q} Metrics",
        "Investor Update: {month}",
        "Due Diligence Request",
        "Cap Table Update",
    ]
}

NOTION_PROJECTS = [
    "Product Roadmap Q{q}", "Customer Onboarding Revamp", "Infrastructure Migration",
    "Series A Data Room", "PCB Redesign v2", "GTM Strategy {year}",
    "Partnership Expansion", "Compliance Audit", "ML Pipeline Overhaul"
]

SLACK_CHANNELS = [
    "#general", "#engineering", "#sales", "#partnerships", "#product",
    "#design", "#ops", "#marketing", "#leadership", "#customer-success"
]

LOCATIONS = [
    "Zoom", "Google Meet", "Conference Room A", "Conference Room B",
    "Board Room", "Offsite — {city}", "Founder's Office", "Team Lounge"
]

CITIES = ["San Francisco", "New York", "London", "Berlin", "Singapore", "Mumbai", "Tokyo"]

TOPICS = [
    "pricing model", "Q3 roadmap", "infrastructure costs", "sales pipeline",
    "product launch", "hiring plan", "compliance requirements", "API integration",
    "customer churn", "board presentation", "vendor evaluation", "security audit"
]


# ─────────────────────────────────────────────────────────────────────────────
# WORLD STATE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

class WorldStateGenerator:

    def __init__(self, seed: int = None, base_date: str = None):
        self.rng = random.Random(seed)
        self.base_date = base_date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._thread_counter = 0
        self._event_counter  = 0
        self._task_counter   = 0

    # ── PUBLIC API ────────────────────────────────────────────────────────────

    def generate(self, scenario_tags: list = None) -> dict:
        """
        Generate a single world state.

        scenario_tags control which "flavours" of content get injected:
          "external_email"  — inbox contains external partner/vendor email
          "internal_email"  — inbox contains internal team email
          "conflict"        — calendar contains a scheduling conflict
          "overdue_tasks"   — Notion has overdue open tasks
          "ambiguous_sender"— two contacts with the same first name
          "slack_mention"   — relevant Slack message in a channel
          "multi_step"      — world state set up for 4+ tool orchestration
          "error_recovery"  — world state designed to trigger a tool error
        """
        tags = set(scenario_tags or [])
        user = self._make_user()
        now  = self._parse_dt(self.base_date)

        world = {
            "scenario_tags":    list(tags),
            "user":             user,
            "current_datetime": self.base_date,
            "contacts":         self._make_contacts(user, tags),
            "email": {
                "threads": [],
                "drafts":  [],
                "sent":    []
            },
            "calendar": {
                "events": []
            },
            "notion": {
                "databases": {
                    "partnerships": [],
                    "tasks":        [],
                    "projects":     []
                }
            },
            "slack": {
                "channels": {ch: [] for ch in self._pick(SLACK_CHANNELS, 4)}
            }
        }

        # ── Email threads
        world["email"]["threads"] = self._make_email_threads(user, world["contacts"], now, tags)

        # ── Calendar events
        world["calendar"]["events"] = self._make_calendar_events(user, world["contacts"], now, tags)

        # ── Notion databases
        world["notion"]["databases"]["partnerships"] = self._make_partnerships(world["contacts"], now)
        world["notion"]["databases"]["tasks"]        = self._make_tasks(world["contacts"], now, tags)
        world["notion"]["databases"]["projects"]     = self._make_projects(now)

        # ── Slack messages
        self._populate_slack(world, world["contacts"], now, tags)

        return world

    def generate_batch(self, n: int, tag_distribution: dict = None) -> list:
        """
        Generate n world states with a controlled distribution of scenario tags.

        tag_distribution: {tag: probability} — if None, uses default distribution.
        Each world state gets 1–3 tags sampled according to the distribution.
        """
        if tag_distribution is None:
            tag_distribution = {
                "external_email":   0.6,
                "internal_email":   0.5,
                "conflict":         0.25,
                "overdue_tasks":    0.4,
                "ambiguous_sender": 0.15,
                "slack_mention":    0.35,
                "multi_step":       0.45,
                "error_recovery":   0.15,
            }

        states = []
        for i in range(n):
            # Sample tags probabilistically
            tags = [tag for tag, prob in tag_distribution.items()
                    if self.rng.random() < prob]
            # Always at least one tag
            if not tags:
                tags = [self.rng.choice(list(tag_distribution.keys()))]

            state = self.generate(scenario_tags=tags)
            state["world_state_id"] = f"ws_{i+1:04d}"
            states.append(state)

        return states

    def save_batch(self, states: list, output_dir: str = "world_states/") -> None:
        """Save a batch of world states as individual JSON files."""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        for state in states:
            ws_id = state.get("world_state_id", f"ws_{self.rng.randint(1000,9999)}")
            path  = Path(output_dir) / f"{ws_id}.json"
            with open(path, "w") as f:
                json.dump(state, f, indent=2)

    # ── PRIVATE BUILDERS ──────────────────────────────────────────────────────

    def _make_user(self) -> dict:
        first = self.rng.choice(FIRST_NAMES)
        last  = self.rng.choice(LAST_NAMES)
        domain = self.rng.choice(INTERNAL_DOMAINS)
        return {
            "name":     f"{first} {last}",
            "email":    f"{first.lower()}.{last.lower()}@{domain}",
            "role":     self.rng.choice(ROLES),
            "timezone": self.rng.choice(["America/New_York", "Europe/London",
                                         "Asia/Kolkata", "Asia/Singapore"])
        }

    def _make_contacts(self, user: dict, tags: set) -> list:
        contacts = []
        n_contacts = self.rng.randint(4, 8)

        for _ in range(n_contacts):
            first  = self.rng.choice(FIRST_NAMES)
            last   = self.rng.choice(LAST_NAMES)
            is_ext = self.rng.random() < 0.6
            company = self.rng.choice(COMPANIES) if is_ext else user["email"].split("@")[1]
            domain  = company.lower().replace(" ", "") + ".com" if is_ext else user["email"].split("@")[1]
            contacts.append({
                "name":     f"{first} {last}",
                "email":    f"{first.lower()}.{last.lower()}@{domain}",
                "company":  company,
                "role":     self.rng.choice(ROLES),
                "external": is_ext
            })

        # Inject duplicate first name if ambiguous_sender tag
        if "ambiguous_sender" in tags and contacts:
            base = self.rng.choice(contacts)
            first = base["name"].split()[0]
            new_last   = self.rng.choice(LAST_NAMES)
            new_domain = self.rng.choice(COMPANIES).lower().replace(" ", "") + ".com"
            contacts.append({
                "name":     f"{first} {new_last}",
                "email":    f"{first.lower()}.{new_last.lower()}@{new_domain}",
                "company":  new_domain.replace(".com", ""),
                "role":     self.rng.choice(ROLES),
                "external": True
            })

        return contacts

    def _make_email_threads(self, user: dict, contacts: list, now: datetime, tags: set) -> list:
        threads = []
        n = self.rng.randint(3, 7)

        for _ in range(n):
            sender    = self.rng.choice(contacts)
            days_ago  = self.rng.randint(0, 14)
            thread_dt = now - timedelta(days=days_ago, hours=self.rng.randint(0, 23))
            category  = self.rng.choice(["partnership", "internal", "vendor", "investor"])

            # Pick and render subject template
            subject_tmpl = self.rng.choice(EMAIL_SUBJECTS[category])
            subject = self._render(subject_tmpl, {
                "q":       self.rng.randint(1, 4),
                "company": sender["company"],
                "meeting": self.rng.choice(MEETING_TYPES),
                "topic":   self.rng.choice(TOPICS),
                "project": self._render(self.rng.choice(NOTION_PROJECTS),
                                        {"q": self.rng.randint(1, 4), "year": 2026}),
                "day":     self.rng.choice(["Monday", "Wednesday", "Friday"]),
                "num":     self.rng.randint(1000, 9999),
                "date":    thread_dt.strftime("%b %d"),
                "month":   thread_dt.strftime("%B"),
                "tier":    self.rng.randint(1, 4),
            })

            body = self._make_email_body(sender, user, subject, category, tags)
            snippet = body[:120].replace("\n", " ")

            self._thread_counter += 1
            thread_id = f"t_{self._thread_counter:05d}"

            threads.append({
                "thread_id":     thread_id,
                "subject":       subject,
                "from":          sender["email"],
                "snippet":       snippet,
                "date":          thread_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "unread":        self.rng.random() < 0.5,
                "message_count": self.rng.randint(1, 4),
                "messages": [
                    {
                        "message_id": f"m_{thread_id}_001",
                        "from":       sender["email"],
                        "to":         [user["email"]],
                        "cc":         [],
                        "date":       thread_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "body":       body
                    }
                ]
            })

        return threads

    def _make_email_body(self, sender: dict, user: dict, subject: str,
                         category: str, tags: set) -> str:
        first_name = user["name"].split()[0]
        sender_first = sender["name"].split()[0]
        topic = self.rng.choice(TOPICS)

        bodies = {
            "partnership": [
                f"Hi {first_name},\n\nHope you're well. I wanted to flag a concern ahead of our next call — "
                f"the {topic} you proposed is about {self.rng.randint(10, 30)}% above what "
                f"{self.rng.choice(COMPANIES)} is currently offering. We'd love to stay with you, "
                f"but need this addressed before we can renew.\n\nCan you come prepared with a revised proposal?\n\n"
                f"Best,\n{sender_first}",

                f"Hi {first_name},\n\nFollowing up on our conversation from last week. "
                f"We still need the updated SLA document and confirmation on the {topic}. "
                f"Our deadline is end of this month.\n\nThanks,\n{sender_first}",
            ],
            "internal": [
                f"Hi {first_name},\n\nQuick question on the {topic} — "
                f"have we made a final decision? The team is waiting on this before we can proceed.\n\n"
                f"Let me know when you have a moment.\n\n{sender_first}",

                f"Hi {first_name},\n\nJust a reminder that the {topic} review is due by EOD Friday. "
                f"I've attached the relevant docs. Please review and add your comments.\n\n{sender_first}",
            ],
            "vendor": [
                f"Hi {first_name},\n\nPlease find attached Invoice #{self.rng.randint(1000,9999)} "
                f"for services rendered in {datetime.now().strftime('%B')}. "
                f"Payment terms are net 30.\n\nThank you,\n{sender_first}",

                f"Hi {first_name},\n\nWe wanted to inform you that the shipment for your recent order "
                f"has been delayed by {self.rng.randint(3, 10)} business days due to supply chain issues. "
                f"We apologize for the inconvenience.\n\n{sender_first}",
            ],
            "investor": [
                f"Hi {first_name},\n\nIn preparation for the upcoming board meeting, "
                f"could you please share the latest metrics on {topic}? "
                f"We'll need them at least 48 hours in advance.\n\nThank you,\n{sender_first}",

                f"Hi {first_name},\n\nWe've completed our initial due diligence and have a few follow-up "
                f"questions on the {topic}. Could we schedule a call this week?\n\nBest,\n{sender_first}",
            ]
        }

        options = bodies.get(category, bodies["internal"])
        return self.rng.choice(options)

    def _make_calendar_events(self, user: dict, contacts: list, now: datetime, tags: set) -> list:
        events = []
        today  = now.date()

        # Generate 3–6 events across the next 5 days
        n = self.rng.randint(3, 6)
        used_slots = set()  # (date_str, time_str)

        for _ in range(n):
            days_ahead = self.rng.randint(0, 4)
            event_date = today + timedelta(days=days_ahead)
            hour       = self.rng.choice([9, 10, 11, 14, 15, 16])
            slot_key   = (str(event_date), f"{hour:02d}:00")

            # Skip if slot already used (unless we're injecting a conflict)
            if slot_key in used_slots and "conflict" not in tags:
                hour = (hour + 1) % 18
                slot_key = (str(event_date), f"{hour:02d}:00")

            attendees = [c["email"] for c in self.rng.sample(
                contacts, min(self.rng.randint(1, 3), len(contacts))
            )]
            duration  = self.rng.choice([30, 45, 60, 90])
            start_dt  = datetime(event_date.year, event_date.month, event_date.day,
                                 hour, 0, 0, tzinfo=timezone.utc)
            end_dt    = start_dt + timedelta(minutes=duration)
            location  = self._render(self.rng.choice(LOCATIONS), {"city": self.rng.choice(CITIES)})

            self._event_counter += 1
            events.append({
                "event_id":  f"ev_{800 + self._event_counter}",
                "title":     self.rng.choice(MEETING_TYPES),
                "start":     start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end":       end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "attendees": attendees,
                "location":  location,
                "notes":     f"Agenda: {self.rng.choice(TOPICS).capitalize()}",
                "organizer": user["email"] if self.rng.random() < 0.6 else attendees[0]
            })
            used_slots.add(slot_key)

        # Inject a deliberate conflict if tagged
        if "conflict" in tags and events:
            base_event  = self.rng.choice(events)
            base_start  = self._parse_dt(base_event["start"])
            conflict_dt = base_start + timedelta(minutes=15)   # overlapping start
            conflict_end = conflict_dt + timedelta(minutes=30)
            self._event_counter += 1
            events.append({
                "event_id":  f"ev_{800 + self._event_counter}",
                "title":     self.rng.choice(MEETING_TYPES),
                "start":     conflict_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end":       conflict_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "attendees": [self.rng.choice(contacts)["email"]],
                "location":  "Zoom",
                "notes":     "Conflicts with existing block",
                "organizer": self.rng.choice(contacts)["email"]
            })

        return sorted(events, key=lambda e: e["start"])

    def _make_partnerships(self, contacts: list, now: datetime) -> list:
        ext_contacts = [c for c in contacts if c.get("external")]
        if not ext_contacts:
            ext_contacts = contacts[:2]

        rows = []
        for c in ext_contacts[:self.rng.randint(1, 3)]:
            self._task_counter += 1
            due_days = self.rng.randint(-5, 30)   # negative = overdue
            rows.append({
                "row_id":   f"nr_{self._task_counter:04d}",
                "task_id":  f"nr_{self._task_counter:04d}",
                "title":    f"{c['company']} — Q{self.rng.randint(1,4)} Renewal",
                "status":   self.rng.choice(["open", "in_progress", "in_progress", "done"]),
                "owner":    c["email"],
                "due_date": (now + timedelta(days=due_days)).strftime("%Y-%m-%d"),
                "properties": {
                    "deal_value":  f"${self.rng.randint(10, 500)}k",
                    "action_items": self.rng.sample([
                        "Send updated SLA doc",
                        "Confirm pricing tiers",
                        "Schedule QBR",
                        "Review contract terms",
                        "Share product roadmap",
                        "Complete security questionnaire"
                    ], self.rng.randint(1, 3))
                }
            })
        return rows

    def _make_tasks(self, contacts: list, now: datetime, tags: set) -> list:
        tasks = []
        n = self.rng.randint(3, 7)
        statuses = ["open", "in_progress", "done"]

        for i in range(n):
            self._task_counter += 1
            days_delta   = self.rng.randint(-10, 20)
            is_overdue   = days_delta < 0
            # Force some overdue if tag present
            if "overdue_tasks" in tags and i < 2:
                days_delta = self.rng.randint(-7, -1)
                is_overdue = True

            status = "open" if is_overdue else self.rng.choice(statuses)
            tasks.append({
                "row_id":   f"nt_{self._task_counter:04d}",
                "task_id":  f"nt_{self._task_counter:04d}",
                "title":    f"{self.rng.choice(TOPICS).capitalize()} — {self.rng.choice(['Review', 'Update', 'Implement', 'Resolve', 'Ship'])}",
                "status":   status,
                "owner":    self.rng.choice(contacts)["email"] if contacts else "",
                "due_date": (now + timedelta(days=days_delta)).strftime("%Y-%m-%d"),
                "priority": self.rng.choice(["low", "medium", "medium", "high"]),
                "notes":    ""
            })
        return tasks

    def _make_projects(self, now: datetime) -> list:
        rows = []
        for _ in range(self.rng.randint(1, 3)):
            self._task_counter += 1
            tmpl = self.rng.choice(NOTION_PROJECTS)
            rows.append({
                "row_id":  f"np_{self._task_counter:04d}",
                "task_id": f"np_{self._task_counter:04d}",
                "title":   self._render(tmpl, {"q": self.rng.randint(1, 4), "year": 2026}),
                "status":  self.rng.choice(["in_progress", "in_progress", "done", "open"]),
                "owner":   "",
                "due_date": (now + timedelta(days=self.rng.randint(10, 90))).strftime("%Y-%m-%d"),
                "properties": {
                    "milestones": self.rng.sample([
                        "Kickoff complete", "Design signed off",
                        "Engineering in progress", "QA pending", "Launch ready"
                    ], self.rng.randint(1, 3))
                }
            })
        return rows

    def _populate_slack(self, world: dict, contacts: list, now: datetime, tags: set) -> None:
        channels = list(world["slack"]["channels"].keys())
        if not channels:
            return

        # Add 2–4 recent messages per channel
        for channel in channels:
            n_messages = self.rng.randint(2, 4)
            for i in range(n_messages):
                sender = self.rng.choice(contacts) if contacts else {"name": "teammate"}
                mins_ago = self.rng.randint(5, 480)
                msg_dt   = now - timedelta(minutes=mins_ago)
                ts       = f"{int(msg_dt.timestamp())}.{i:06d}"
                world["slack"]["channels"][channel].append({
                    "ts":   ts,
                    "user": sender["name"].split()[0].lower(),
                    "text": self._make_slack_message(sender, channel),
                })

        # Inject relevant Slack mention if tagged
        if "slack_mention" in tags and channels and contacts:
            channel  = self.rng.choice(channels)
            sender   = self.rng.choice(contacts)
            user_handle = world["user"]["name"].split()[0].lower()
            ts = f"{int(now.timestamp())}.999999"
            world["slack"]["channels"][channel].append({
                "ts":   ts,
                "user": sender["name"].split()[0].lower(),
                "text": f"@{user_handle} — can you take a look at the {self.rng.choice(TOPICS)} before EOD?",
            })

    def _make_slack_message(self, sender: dict, channel: str) -> str:
        topic = self.rng.choice(TOPICS)
        options = [
            f"Just finished the {topic} review. Notes in the doc.",
            f"Can someone double-check the {topic} numbers before tomorrow?",
            f"Heads up — {topic} is blocked. Need a decision from leadership.",
            f"Updated the {topic} deck with latest figures.",
            f"Quick question on {topic} — anyone free for a 10min call?",
            f"Reminder: {topic} sync is at 3pm today.",
            f"PR for {topic} is ready for review.",
            f"The {topic} issue has been resolved.",
        ]
        return self.rng.choice(options)

    # ── UTILITY ───────────────────────────────────────────────────────────────

    def _pick(self, pool: list, n: int) -> list:
        return self.rng.sample(pool, min(n, len(pool)))

    def _render(self, template: str, context: dict) -> str:
        """Simple {key} substitution."""
        for k, v in context.items():
            template = template.replace(f"{{{k}}}", str(v))
        return template

    def _parse_dt(self, s: str) -> datetime:
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return datetime.now(tz=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# QUICK SMOKE TEST
# Run: python world_state_generator.py
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    gen = WorldStateGenerator(seed=42, base_date="2026-04-28T09:00:00Z")

    print("── Single world state (tags: conflict + external_email) ──")
    ws = gen.generate(scenario_tags=["conflict", "external_email", "overdue_tasks"])
    print(f"User:       {ws['user']['name']} <{ws['user']['email']}>")
    print(f"Contacts:   {len(ws['contacts'])} people")
    print(f"Emails:     {len(ws['email']['threads'])} threads")
    print(f"Events:     {len(ws['calendar']['events'])} calendar events")
    print(f"Tasks:      {len(ws['notion']['databases']['tasks'])} tasks")
    print(f"Partnerships: {len(ws['notion']['databases']['partnerships'])} rows")
    print(f"Slack chs:  {list(ws['slack']['channels'].keys())}")
    print()

    print("── Batch of 5 world states ──")
    batch = gen.generate_batch(n=5)
    for s in batch:
        tags = s["scenario_tags"]
        n_emails  = len(s["email"]["threads"])
        n_events  = len(s["calendar"]["events"])
        n_tasks   = len(s["notion"]["databases"]["tasks"])
        print(f"  {s['world_state_id']}  tags={tags}  "
              f"emails={n_emails}  events={n_events}  tasks={n_tasks}")

    print()
    print("── Sample email thread ──")
    thread = ws["email"]["threads"][0]
    print(f"  Subject: {thread['subject']}")
    print(f"  From:    {thread['from']}")
    print(f"  Snippet: {thread['snippet'][:80]}...")

    print()
    print("── Sample calendar event ──")
    event = ws["calendar"]["events"][0]
    print(f"  Title:     {event['title']}")
    print(f"  Start:     {event['start']}")
    print(f"  Attendees: {event['attendees']}")

    print()
    print("── Overdue tasks ──")
    from datetime import date
    today = date.today().isoformat()
    overdue = [t for t in ws["notion"]["databases"]["tasks"]
               if t.get("due_date", "9999") < today and t["status"] != "done"]
    for t in overdue:
        print(f"  [{t['status']}] {t['title']} — due {t['due_date']}")


if __name__ == "__main__":
    gen = WorldStateGenerator(seed=42)
    states = gen.generate_batch(n=50)
    gen.save_batch(states, output_dir="world_states/")