"""
mock_environment.py
-------------------
Foundry [Flow] — Simulated Environment for trajectory generation and FlowBench evaluation.

Implements all 15 tools from TOOL_SCHEMA v0.1:
  Email:    search_email, read_email, draft_email, send_email, reply_email
  Slack:    send_slack_message, read_slack_thread
  Calendar: check_calendar, create_event, update_event
  Notion:   query_notion, create_task, update_task
  Control:  ask_user, done

Usage:
    from mock_environment import MockEnvironment
    env = MockEnvironment(world_state)
    result = env.execute("search_email", {"query": "Acme", "max_results": 3})
"""

import copy
from datetime import datetime, timedelta, timezone


class MockEnvironment:
    def __init__(self, world_state: dict):
        # Deep copy so each trajectory gets an isolated mutable state
        self.state = copy.deepcopy(world_state)
        self._draft_counter = 0
        self._task_counter = 0
        self._event_counter = 0
        self._slack_counter = 0

    # ─────────────────────────────────────────────────────────────
    # PUBLIC INTERFACE
    # ─────────────────────────────────────────────────────────────

    def execute(self, tool_name: str, parameters: dict) -> dict:
        """Route a tool call to its handler. Returns a tool_result dict."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return self._error("UNKNOWN_TOOL", f"No tool named '{tool_name}'")
        try:
            return handler(parameters)
        except KeyError as e:
            return self._error("MISSING_PARAMETER", f"Required parameter missing: {e}")
        except Exception as e:
            return self._error("INTERNAL_ERROR", str(e))

    def snapshot(self) -> dict:
        """Return a deep copy of current world state (for trajectory logging)."""
        return copy.deepcopy(self.state)

    # ─────────────────────────────────────────────────────────────
    # EMAIL TOOLS
    # ─────────────────────────────────────────────────────────────

    def _tool_search_email(self, p: dict) -> dict:
        query       = p.get("query", "").lower()
        max_results = min(p.get("max_results", 5), 20)
        from_filter = p.get("from", "").lower()
        folder      = p.get("folder", "inbox")
        date_range  = p.get("date_range")          # e.g. "2w", "4d"
        cutoff      = self._parse_date_range(date_range)

        # Input validation
        if len(query.strip()) < 2:
            return self._error("QUERY_TOO_SHORT", "Query must be at least 2 characters")
        if date_range and cutoff is None:
            return self._error("INVALID_DATE_RANGE", f"Unrecognized date_range format: '{date_range}'")

        pool = self.state["email"]["threads"]
        if folder == "sent":
            pool = self.state["email"]["sent"]
        elif folder == "drafts":
            pool = self.state["email"]["drafts"]

        results = []
        for thread in pool:
            # Date filter
            if cutoff:
                thread_date = self._parse_iso(thread.get("date", ""))
                if thread_date and thread_date < cutoff:
                    continue
            # Content filter
            text_match = (
                query in thread.get("subject", "").lower()
                or query in thread.get("snippet", "").lower()
            )
            sender_match = not from_filter or from_filter in thread.get("from", "").lower()
            if text_match and sender_match:
                results.append({k: v for k, v in thread.items() if k != "messages"})

        return self._success({
            "threads": results[:max_results],
            "total_matches": len(results)
        })

    def _tool_read_email(self, p: dict) -> dict:
        thread_id    = p.get("thread_id")
        mark_as_read = p.get("mark_as_read", True)

        thread = self._find_thread(thread_id)
        if not thread:
            return self._error("THREAD_NOT_FOUND", f"No thread with id '{thread_id}'")

        if mark_as_read:
            thread["unread"] = False

        messages = thread.get("messages", [])
        # Truncate each message body at 8000 chars
        safe_messages = []
        for m in messages:
            body = m.get("body", "")
            truncated = len(body) > 8000
            safe_messages.append({
                **m,
                "body": body[:8000],
                **({"truncated": True} if truncated else {})
            })

        return self._success({
            "thread_id": thread["thread_id"],
            "subject":   thread["subject"],
            "messages":  safe_messages
        })

    def _tool_draft_email(self, p: dict) -> dict:
        # Validate recipients
        to_list = p.get("to", [])
        if not to_list:
            return self._error("MISSING_PARAMETER", "Parameter 'to' is required")
        for addr in to_list:
            if "@" not in addr:
                return self._error("INVALID_RECIPIENT", f"Malformed email address: '{addr}'")

        body = p.get("body", "")
        if len(body) > 10000:
            return self._error("BODY_TOO_LONG", "Body exceeds 10,000 characters")

        self._draft_counter += 1
        draft_id = f"d_{self._draft_counter:04d}"
        draft = {
            "draft_id":           draft_id,
            "to":                 to_list,
            "subject":            p.get("subject", ""),
            "body":               body,
            "cc":                 p.get("cc", []),
            "reply_to_thread_id": p.get("reply_to_thread_id"),
            "sent":               False
        }
        self.state["email"]["drafts"].append(draft)
        return self._success({
            "draft_id":    draft_id,
            "preview_url": f"gmail://drafts/{draft_id}"
        })

    def _tool_send_email(self, p: dict) -> dict:
        draft_id = p.get("draft_id")
        draft = next(
            (d for d in self.state["email"]["drafts"] if d["draft_id"] == draft_id),
            None
        )
        if not draft:
            return self._error("DRAFT_NOT_FOUND", f"No draft with id '{draft_id}'")
        if draft["sent"]:
            return self._error("DRAFT_ALREADY_SENT", "This draft was already sent")

        draft["sent"] = True
        sent_record = {
            **draft,
            "message_id": f"m_sent_{self._draft_counter:04d}",
            "sent_at":    self.state["current_datetime"]
        }
        self.state["email"]["sent"].append(sent_record)
        return self._success({
            "message_id": sent_record["message_id"],
            "sent_at":    sent_record["sent_at"]
        })

    def _tool_reply_email(self, p: dict) -> dict:
        thread_id        = p.get("thread_id")
        body             = p.get("body", "")
        send_immediately = p.get("send_immediately", False)
        cc               = p.get("cc", [])

        thread = self._find_thread(thread_id)
        if not thread:
            return self._error("THREAD_NOT_FOUND", f"No thread with id '{thread_id}'")
        if len(body) > 10000:
            return self._error("BODY_TOO_LONG", "Body exceeds 10,000 characters")

        self._draft_counter += 1
        draft_id = f"d_{self._draft_counter:04d}"
        reply = {
            "draft_id":           draft_id,
            "to":                 [thread["from"]],
            "subject":            f"RE: {thread['subject']}",
            "body":               body,
            "cc":                 cc,
            "reply_to_thread_id": thread_id,
            "sent":               False
        }
        self.state["email"]["drafts"].append(reply)

        if send_immediately:
            reply["sent"] = True
            sent_record = {
                **reply,
                "message_id": f"m_sent_{self._draft_counter:04d}",
                "sent_at":    self.state["current_datetime"]
            }
            self.state["email"]["sent"].append(sent_record)
            return self._success({"draft_id": draft_id, "sent": True})

        return self._success({"draft_id": draft_id, "sent": False})

    # ─────────────────────────────────────────────────────────────
    # SLACK TOOLS
    # ─────────────────────────────────────────────────────────────

    def _tool_send_slack_message(self, p: dict) -> dict:
        channel   = p.get("channel", "")
        message   = p.get("message", "")
        thread_ts = p.get("thread_ts")

        if not channel:
            return self._error("MISSING_PARAMETER", "Parameter 'channel' is required")
        if len(message) > 4000:
            return self._error("MESSAGE_TOO_LONG", "Message exceeds 4,000 characters")

        slack = self.state.get("slack", {})
        channels = slack.get("channels", {})

        # Normalise: accept "#eng" and "eng"
        channel_key = channel.lstrip("#").lstrip("@")
        matched_key = next(
            (k for k in channels if k.lstrip("#").lstrip("@") == channel_key),
            None
        )
        if matched_key is None:
            return self._error("CHANNEL_NOT_FOUND", f"Channel '{channel}' not found")

        self._slack_counter += 1
        ts = f"17{self._slack_counter:011d}.000100"
        new_msg = {
            "ts":        ts,
            "user":      self.state["user"]["name"].split()[0].lower(),
            "text":      message,
            "thread_ts": thread_ts
        }
        channels[matched_key].append(new_msg)

        return self._success({
            "message_ts":  ts,
            "channel_id":  f"C_{channel_key.upper()}",
            "permalink":   f"https://slack.com/archives/{channel_key}/{ts}"
        })

    def _tool_read_slack_thread(self, p: dict) -> dict:
        channel   = p.get("channel", "")
        thread_ts = p.get("thread_ts")
        limit     = min(p.get("limit", 20), 50)

        slack    = self.state.get("slack", {})
        channels = slack.get("channels", {})

        channel_key = channel.lstrip("#").lstrip("@")
        matched_key = next(
            (k for k in channels if k.lstrip("#").lstrip("@") == channel_key),
            None
        )
        if matched_key is None:
            return self._error("CHANNEL_NOT_FOUND", f"Channel '{channel}' not found")

        messages = channels[matched_key]
        if thread_ts:
            # Return only messages in this thread
            thread_msgs = [m for m in messages
                           if m.get("thread_ts") == thread_ts or m.get("ts") == thread_ts]
            if not thread_msgs:
                return self._error("THREAD_NOT_FOUND",
                                   f"No thread with ts '{thread_ts}' in {channel}")
            messages = thread_msgs

        return self._success({"messages": messages[-limit:]})

    # ─────────────────────────────────────────────────────────────
    # CALENDAR TOOLS
    # ─────────────────────────────────────────────────────────────

    def _tool_check_calendar(self, p: dict) -> dict:
        base_date       = p.get("date", self.state["current_datetime"][:10])
        time_range      = p.get("time_range")          # "HH:MM-HH:MM"
        lookahead_days  = p.get("lookahead_days", 0)
        include_declined = p.get("include_declined", False)

        try:
            start_dt = datetime.strptime(base_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return self._error("INVALID_DATE", f"Cannot parse date '{base_date}'")

        # Build date window
        dates = set()
        for i in range(lookahead_days + 1):
            dates.add((start_dt + timedelta(days=i)).strftime("%Y-%m-%d"))

        # Parse optional time range
        time_start = time_end = None
        if time_range:
            parts = time_range.split("-")
            if len(parts) != 2:
                return self._error("INVALID_TIME_RANGE",
                                   f"time_range must be 'HH:MM-HH:MM', got '{time_range}'")
            time_start, time_end = parts[0].strip(), parts[1].strip()

        events = []
        for e in self.state["calendar"]["events"]:
            e_date = e["start"][:10]
            if e_date not in dates:
                continue
            if not include_declined and e.get("status") == "declined":
                continue
            if time_start and time_end:
                e_time = e["start"][11:16]   # "HH:MM"
                if not (time_start <= e_time <= time_end):
                    continue
            events.append(e)

        return self._success({"events": sorted(events, key=lambda x: x["start"])})

    def _tool_create_event(self, p: dict) -> dict:
        title            = p.get("title", "")
        date             = p.get("date")
        start_time       = p.get("start_time")       # "HH:MM"
        duration_minutes = p.get("duration_minutes", 60)
        attendees        = p.get("attendees", [])
        send_invites     = p.get("send_invites", True)

        if not date:
            return self._error("MISSING_PARAMETER", "Parameter 'date' is required")
        if not start_time:
            return self._error("MISSING_PARAMETER", "Parameter 'start_time' is required")
        if not (15 <= duration_minutes <= 480):
            return self._error("INVALID_DURATION",
                               f"duration_minutes must be 15–480, got {duration_minutes}")
        for addr in attendees:
            if "@" not in addr:
                return self._error("INVALID_ATTENDEE",
                                   f"Malformed attendee email: '{addr}'")

        try:
            start_dt = datetime.strptime(f"{date}T{start_time}:00",
                                         "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return self._error("INVALID_DATE",
                               f"Cannot parse '{date}' / '{start_time}'")

        end_dt = start_dt + timedelta(minutes=duration_minutes)

        # Conflict check — real overlap detection (not just exact-start match)
        for e in self.state["calendar"]["events"]:
            if e["start"][:10] != date:
                continue
            existing_start = self._parse_iso(e["start"])
            existing_end   = self._parse_iso(e.get("end", e["start"]))
            if existing_start and existing_end:
                if start_dt < existing_end and end_dt > existing_start:
                    return self._error(
                        "CONFLICT",
                        f"Conflicts with existing event: '{e['title']}' "
                        f"({e['start'][11:16]}–{e['end'][11:16] if 'end' in e else '?'})"
                    )

        self._event_counter += 1
        event_id = f"ev_{900 + self._event_counter}"
        new_event = {
            "event_id":    event_id,
            "title":       title,
            "start":       start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":         end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attendees":   attendees,
            "location":    p.get("location", ""),
            "notes":       p.get("notes", ""),
            "organizer":   self.state["user"]["email"],
            "send_invites": send_invites
        }
        self.state["calendar"]["events"].append(new_event)
        return self._success({
            "event_id":      event_id,
            "calendar_link": f"https://calendar.google.com/ev/{event_id}"
        })

    def _tool_update_event(self, p: dict) -> dict:
        event_id = p.get("event_id")
        event = next(
            (e for e in self.state["calendar"]["events"] if e["event_id"] == event_id),
            None
        )
        if not event:
            return self._error("EVENT_NOT_FOUND", f"No event with id '{event_id}'")
        if event.get("organizer") != self.state["user"]["email"]:
            return self._error("NOT_ORGANIZER",
                               "You are not the organizer and cannot edit this event")

        updated_fields = []

        if "title" in p:
            event["title"] = p["title"]
            updated_fields.append("title")

        if "date" in p or "start_time" in p:
            new_date = p.get("date", event["start"][:10])
            new_time = p.get("start_time", event["start"][11:16])
            try:
                new_start = datetime.strptime(
                    f"{new_date}T{new_time}:00", "%Y-%m-%dT%H:%M:%S"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                return self._error("INVALID_DATE",
                                   f"Cannot parse '{new_date}' / '{new_time}'")

            # Duration
            duration_minutes = p.get("duration_minutes")
            if duration_minutes:
                if not (15 <= duration_minutes <= 480):
                    return self._error("INVALID_DURATION",
                                       f"duration_minutes must be 15–480")
                new_end = new_start + timedelta(minutes=duration_minutes)
            else:
                # Preserve original duration
                old_start = self._parse_iso(event["start"])
                old_end   = self._parse_iso(event.get("end", event["start"]))
                if old_start and old_end:
                    original_duration = (old_end - old_start).seconds // 60
                    new_end = new_start + timedelta(minutes=original_duration)
                else:
                    new_end = new_start + timedelta(hours=1)

            # Conflict check on new slot
            for e in self.state["calendar"]["events"]:
                if e["event_id"] == event_id:
                    continue
                if e["start"][:10] != new_date:
                    continue
                ex_start = self._parse_iso(e["start"])
                ex_end   = self._parse_iso(e.get("end", e["start"]))
                if ex_start and ex_end:
                    if new_start < ex_end and new_end > ex_start:
                        return self._error(
                            "CONFLICT",
                            f"New time conflicts with: '{e['title']}'"
                        )

            event["start"] = new_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            event["end"]   = new_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            updated_fields.extend(["start", "end"])

        if "notes" in p:
            event["notes"] = p["notes"]
            updated_fields.append("notes")

        return self._success({
            "event_id":       event_id,
            "updated_fields": updated_fields
        })

    # ─────────────────────────────────────────────────────────────
    # NOTION TOOLS
    # ─────────────────────────────────────────────────────────────

    def _tool_query_notion(self, p: dict) -> dict:
        db_name       = p.get("database")
        filter_val    = p.get("filter", "").lower()
        status_filter = p.get("status")
        limit         = min(p.get("limit", 10), 20)

        db = self.state["notion"]["databases"].get(db_name)
        if db is None:
            return self._error("DATABASE_NOT_FOUND",
                               f"No database named '{db_name}'")

        rows = [
            r for r in db
            if (not filter_val or filter_val in r.get("title", "").lower())
            and (not status_filter or r.get("status") == status_filter)
        ]
        return self._success({
            "rows":       rows[:limit],
            "total_rows": len(rows)
        })

    def _tool_create_task(self, p: dict) -> dict:
        title    = p.get("title")
        db_name  = p.get("database")
        due_date = p.get("due_date")
        assignee = p.get("assignee")
        priority = p.get("priority", "medium")

        if not title:
            return self._error("MISSING_PARAMETER", "Parameter 'title' is required")
        if priority not in ("low", "medium", "high"):
            return self._error("INVALID_PRIORITY",
                               f"priority must be low/medium/high, got '{priority}'")

        db = self.state["notion"]["databases"].get(db_name)
        if db is None:
            return self._error("DATABASE_NOT_FOUND",
                               f"No database named '{db_name}'")

        self._task_counter += 1
        task_id = f"nt_{self._task_counter:04d}"
        new_task = {
            "task_id":  task_id,
            "row_id":   task_id,
            "title":    title,
            "status":   "open",
            "owner":    self.state["user"]["email"],
            "due_date": due_date,
            "assignee": assignee,
            "priority": priority,
            "notes":    p.get("notes", ""),
            "properties": {}
        }
        db.append(new_task)
        return self._success({
            "task_id": task_id,
            "url":     f"https://notion.so/{task_id}"
        })

    def _tool_update_task(self, p: dict) -> dict:
        task_id = p.get("task_id")

        # Search across all databases
        target_task = None
        for db in self.state["notion"]["databases"].values():
            target_task = next(
                (r for r in db if r.get("task_id") == task_id or r.get("row_id") == task_id),
                None
            )
            if target_task:
                break

        if not target_task:
            return self._error("TASK_NOT_FOUND", f"No task with id '{task_id}'")

        valid_statuses = {"open", "in_progress", "done", "cancelled"}
        updated_fields = []

        if "status" in p:
            if p["status"] not in valid_statuses:
                return self._error("INVALID_STATUS",
                                   f"status must be one of {valid_statuses}")
            target_task["status"] = p["status"]
            updated_fields.append("status")

        if "due_date" in p:
            target_task["due_date"] = p["due_date"]
            updated_fields.append("due_date")

        if "notes" in p:
            # Append, never replace
            existing = target_task.get("notes", "")
            target_task["notes"] = (existing + "\n" + p["notes"]).strip()
            updated_fields.append("notes")

        if "assignee" in p:
            target_task["assignee"] = p["assignee"]
            updated_fields.append("assignee")

        if "priority" in p:
            if p["priority"] not in ("low", "medium", "high"):
                return self._error("INVALID_PRIORITY",
                                   f"priority must be low/medium/high")
            target_task["priority"] = p["priority"]
            updated_fields.append("priority")

        return self._success({
            "task_id":        task_id,
            "updated_fields": updated_fields
        })

    # ─────────────────────────────────────────────────────────────
    # CONTROL FLOW TOOLS
    # ─────────────────────────────────────────────────────────────

    def _tool_ask_user(self, p: dict) -> dict:
        """
        ask_user responses are injected by the UserSimulator in the generation loop.
        In direct environment calls this raises — the generation loop handles it specially.
        """
        raise NotImplementedError(
            "ask_user is handled by the UserSimulator, not the environment. "
            "The generation loop should intercept this tool call."
        )

    def _tool_done(self, p: dict) -> dict:
        summary = p.get("summary", "")
        if len(summary) > 2000:
            return self._error("SUMMARY_TOO_LONG", "summary exceeds 2,000 characters")
        return self._success({"acknowledged": True})

    # ─────────────────────────────────────────────────────────────
    # PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────

    def _success(self, data: dict) -> dict:
        return {"status": "success", "data": data}

    def _error(self, code: str, message: str) -> dict:
        return {"status": "error", "error": {"code": code, "message": message}}

    def _find_thread(self, thread_id: str):
        return next(
            (t for t in self.state["email"]["threads"] if t["thread_id"] == thread_id),
            None
        )

    def _parse_iso(self, s: str):
        """Parse ISO 8601 datetime string → datetime. Returns None on failure."""
        if not s:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _parse_date_range(self, date_range: str):
        """
        Parse a date_range string like '2w', '4d', '1m' into a cutoff datetime.
        Returns None if parsing fails or input is None.
        """
        if not date_range:
            return None
        now = datetime.now(tz=timezone.utc)
        unit  = date_range[-1].lower()
        try:
            value = int(date_range[:-1])
        except (ValueError, IndexError):
            return None
        if unit == "d":
            return now - timedelta(days=value)
        if unit == "w":
            return now - timedelta(weeks=value)
        if unit == "m":
            return now - timedelta(days=value * 30)
        return None
