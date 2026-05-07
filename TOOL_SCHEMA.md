# Foundry [Flow] — Tool-Use Schema Specification
**Version:** 0.1 (locked for SFT data generation)
**Scope:** Communication · Task & Calendar · General/Control Flow
**Format:** JSON-in-XML with `<think>` blocks

---

## 1. Wire Format

Every model turn that invokes a tool must follow this exact structure:

```
<think>
{chain-of-thought reasoning — required, never empty}
</think>
<tool_call>
{
  "name": "<tool_name>",
  "parameters": { ... }
}
</tool_call>
```

Every tool response from the environment follows this structure:

```
<tool_result>
{
  "status": "success" | "error",
  "data": { ... },
  "error": {
    "code": "<ERROR_CODE>",
    "message": "<human-readable explanation>"
  }
}
</tool_result>
```

### Rules

| Rule | Detail |
|------|--------|
| `<think>` is mandatory | Never emit a `<tool_call>` without a preceding `<think>` block |
| One call per turn | Never emit two `<tool_call>` blocks in a single turn |
| No extra text after `</tool_call>` | The model must wait for `<tool_result>` before continuing |
| `<think>` before final response | Required before the prose answer, same as before tool calls |
| `done` is terminal | After calling `done`, the model must not emit any further tool calls |
| `ask_user` pauses execution | Model must wait for user reply before continuing the plan |

---

## 2. Tool Categories

| Category | Tools | Count |
|----------|-------|-------|
| Communication | `search_email`, `read_email`, `draft_email`, `send_email`, `reply_email`, `send_slack_message`, `read_slack_thread` | 7 |
| Task & Calendar | `check_calendar`, `create_event`, `update_event`, `query_notion`, `create_task`, `update_task` | 6 |
| General / Control Flow | `ask_user`, `done` | 2 |
| **Total (this spec)** | | **15** |

> Research tools (`web_search`, `read_article`) are specified separately and bring the full schema to 17 tools.

---

## 3. Communication Tools

---

### `search_email`

Search the user's inbox and return matching thread summaries.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `query` | string | ✅ | 1–200 chars | Natural language or keyword search query |
| `from` | string | ❌ | Valid email or name | Filter by sender |
| `date_range` | string | ❌ | Format: `"Nd"` (days), `"Nw"` (weeks), `"Nm"` (months) | How far back to search. Default: `"4w"` |
| `max_results` | integer | ❌ | 1–20. Default: 5 | Max threads to return |
| `folder` | string | ❌ | `"inbox"` \| `"sent"` \| `"drafts"` \| `"all"`. Default: `"inbox"` | Which folder to search |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "threads": [
      {
        "thread_id": "t_abc123",
        "subject": "Q2 Partnership Review",
        "from": "sarah.chen@acme.com",
        "snippet": "Hi, wanted to follow up on the pricing discussion...",
        "date": "2026-04-25T14:30:00Z",
        "unread": true,
        "message_count": 3
      }
    ],
    "total_matches": 12
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `QUERY_TOO_SHORT` | Query is empty or under 2 characters |
| `INVALID_DATE_RANGE` | date_range format not recognized |
| `SERVICE_UNAVAILABLE` | Email connector is offline |

**Constraints**
- Does not return full email body — use `read_email` to get content
- `snippet` is at most 150 characters
- Results are sorted by date descending

**Training Example**

```
User: What's the latest from the Acme team?

<think>
The user wants recent emails from Acme. I'll search by sender/keyword
and return the most relevant threads.
</think>
<tool_call>
{
  "name": "search_email",
  "parameters": {
    "query": "Acme",
    "date_range": "2w",
    "max_results": 5
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "threads": [
      {
        "thread_id": "t_8f3k1",
        "subject": "Q2 Partnership Review",
        "from": "sarah.chen@acme.com",
        "snippet": "Hi, wanted to follow up on the tier 3 pricing...",
        "date": "2026-04-25T14:30:00Z",
        "unread": true,
        "message_count": 3
      }
    ],
    "total_matches": 1
  }
}
</tool_result>

<think>
One unread thread from Sarah about Q2 pricing. I should read it to
get the full context before summarizing.
</think>
<tool_call>
{
  "name": "read_email",
  "parameters": {
    "thread_id": "t_8f3k1"
  }
}
</tool_call>
```

---

### `read_email`

Fetch the full content of an email thread.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `thread_id` | string | ✅ | Must be a valid `thread_id` from `search_email` | Thread to read |
| `mark_as_read` | boolean | ❌ | Default: `true` | Whether to mark the thread as read |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "thread_id": "t_abc123",
    "subject": "Q2 Partnership Review",
    "messages": [
      {
        "message_id": "m_001",
        "from": "sarah.chen@acme.com",
        "to": ["user@company.com"],
        "cc": [],
        "date": "2026-04-25T14:30:00Z",
        "body": "Hi, wanted to follow up on the tier 3 pricing..."
      }
    ]
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `THREAD_NOT_FOUND` | thread_id does not exist or is not accessible |
| `SERVICE_UNAVAILABLE` | Email connector is offline |

**Constraints**
- `body` is plain text (HTML stripped)
- Body truncated at 8,000 characters per message; `truncated: true` flag set if cut

---

### `draft_email`

Compose an email draft without sending it.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `to` | array[string] | ✅ | Valid email addresses | Recipients |
| `subject` | string | ✅ | 1–200 chars | Email subject |
| `body` | string | ✅ | 1–10,000 chars | Email body (plain text) |
| `cc` | array[string] | ❌ | Valid email addresses | CC recipients |
| `reply_to_thread_id` | string | ❌ | Valid thread_id | If set, links draft to existing thread |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "draft_id": "d_xyz789",
    "preview_url": "gmail://drafts/d_xyz789"
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `INVALID_RECIPIENT` | One or more `to` addresses are malformed |
| `BODY_TOO_LONG` | Body exceeds 10,000 characters |
| `SERVICE_UNAVAILABLE` | Email connector is offline |

**Constraints**
- Always draft first; use `send_email` to dispatch — never write and send in one step without user confirmation
- Model should use `ask_user` to confirm before calling `send_email` on high-stakes drafts (e.g., external partners, executives)

---

### `send_email`

Send a previously created draft.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `draft_id` | string | ✅ | Valid draft_id from `draft_email` | Draft to send |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "message_id": "m_sent_001",
    "sent_at": "2026-04-28T10:15:00Z"
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `DRAFT_NOT_FOUND` | draft_id does not exist |
| `DRAFT_ALREADY_SENT` | This draft was already dispatched |
| `SEND_FAILED` | Delivery error from mail server |
| `SERVICE_UNAVAILABLE` | Email connector is offline |

**Constraints**
- Irreversible — model must never call `send_email` without prior `ask_user` confirmation if the draft is going to an external party or was not explicitly pre-approved by the user in the original request
- If user explicitly said "send this" in the original request, confirmation is not required

---

### `reply_email`

Reply to an existing thread.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `thread_id` | string | ✅ | Valid thread_id | Thread to reply to |
| `body` | string | ✅ | 1–10,000 chars | Reply body (plain text) |
| `send_immediately` | boolean | ❌ | Default: `false` | If false, creates a draft; if true, sends immediately |
| `cc` | array[string] | ❌ | Valid email addresses | Add CC recipients |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "draft_id": "d_reply_001",
    "sent": false
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `THREAD_NOT_FOUND` | thread_id does not exist |
| `BODY_TOO_LONG` | Body exceeds 10,000 characters |
| `SERVICE_UNAVAILABLE` | Email connector is offline |

**Constraints**
- `send_immediately: true` should only be used when the user has explicitly authorized immediate send

---

### `send_slack_message`

Send a message to a Slack channel or DM.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `channel` | string | ✅ | Channel name (e.g., `"#eng-team"`) or user handle (e.g., `"@sarah"`) | Destination |
| `message` | string | ✅ | 1–4,000 chars | Message text |
| `thread_ts` | string | ❌ | Valid Slack timestamp | Reply to a specific thread |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "message_ts": "1714300000.000100",
    "channel_id": "C08ABCDEF",
    "permalink": "https://slack.com/archives/..."
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `CHANNEL_NOT_FOUND` | Channel or user does not exist |
| `NOT_IN_CHANNEL` | Bot is not a member of the channel |
| `MESSAGE_TOO_LONG` | Message exceeds 4,000 characters |
| `SERVICE_UNAVAILABLE` | Slack connector is offline |

**Constraints**
- DMs to users require `@username` format, not email
- Model must not send Slack messages to external parties without user confirmation

---

### `read_slack_thread`

Fetch messages from a Slack thread or channel history.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `channel` | string | ✅ | Channel name or ID | Channel to read |
| `thread_ts` | string | ❌ | Valid Slack timestamp | Read a specific thread; if omitted, returns recent channel messages |
| `limit` | integer | ❌ | 1–50. Default: 20 | Number of messages to return |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "messages": [
      {
        "ts": "1714300000.000100",
        "user": "sarah",
        "text": "Can we move the 2pm to 3pm?",
        "reactions": []
      }
    ]
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `CHANNEL_NOT_FOUND` | Channel does not exist or is not accessible |
| `THREAD_NOT_FOUND` | thread_ts does not exist |
| `SERVICE_UNAVAILABLE` | Slack connector is offline |

---

## 4. Task & Calendar Tools

---

### `check_calendar`

Retrieve calendar events for a given time window.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `date` | string | ❌ | ISO 8601 date `YYYY-MM-DD`. Default: today | Target date |
| `time_range` | string | ❌ | `"HH:MM-HH:MM"` in 24h. Default: full day | Narrow to a time window |
| `lookahead_days` | integer | ❌ | 0–14. Default: 0 | Include N days after `date` |
| `include_declined` | boolean | ❌ | Default: `false` | Include events the user declined |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "events": [
      {
        "event_id": "ev_001",
        "title": "Q2 Partnership Review",
        "start": "2026-04-28T14:00:00Z",
        "end": "2026-04-28T15:00:00Z",
        "attendees": ["sarah.chen@acme.com", "mike.patel@acme.com"],
        "location": "Zoom",
        "notes": "Discuss Q2 renewal terms",
        "organizer": "user@company.com"
      }
    ]
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `INVALID_DATE` | Date format not recognized |
| `INVALID_TIME_RANGE` | Time range malformed or start > end |
| `SERVICE_UNAVAILABLE` | Calendar connector is offline |

---

### `create_event`

Create a new calendar event.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `title` | string | ✅ | 1–200 chars | Event title |
| `date` | string | ✅ | ISO 8601 `YYYY-MM-DD` | Event date |
| `start_time` | string | ✅ | `"HH:MM"` in 24h | Start time |
| `duration_minutes` | integer | ✅ | 15–480 | Duration in minutes |
| `attendees` | array[string] | ❌ | Valid emails | People to invite |
| `location` | string | ❌ | Max 300 chars | Physical location or video link |
| `notes` | string | ❌ | Max 2,000 chars | Agenda or notes |
| `send_invites` | boolean | ❌ | Default: `true` | Whether to send calendar invites |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "event_id": "ev_new_001",
    "calendar_link": "https://calendar.google.com/..."
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `CONFLICT` | An existing event overlaps this time slot |
| `INVALID_DATE` | Date format not recognized |
| `INVALID_TIME` | Time format not recognized |
| `INVALID_DURATION` | Duration out of range |
| `INVALID_ATTENDEE` | One or more attendee emails are malformed |
| `SERVICE_UNAVAILABLE` | Calendar connector is offline |

**Constraints**
- On `CONFLICT`, model must surface the conflict to the user via `ask_user` rather than overriding
- `send_invites: true` is irreversible — invitations cannot be unsent; model should confirm with user for external attendees

---

### `update_event`

Modify an existing calendar event.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `event_id` | string | ✅ | Valid event_id from `check_calendar` | Event to update |
| `title` | string | ❌ | 1–200 chars | New title |
| `date` | string | ❌ | ISO 8601 `YYYY-MM-DD` | New date |
| `start_time` | string | ❌ | `"HH:MM"` in 24h | New start time |
| `duration_minutes` | integer | ❌ | 15–480 | New duration |
| `notes` | string | ❌ | Max 2,000 chars | New notes |
| `notify_attendees` | boolean | ❌ | Default: `true` | Whether to send update notification |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "event_id": "ev_001",
    "updated_fields": ["start_time", "duration_minutes"]
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `EVENT_NOT_FOUND` | event_id does not exist |
| `CONFLICT` | Updated time slot conflicts with another event |
| `NOT_ORGANIZER` | User is not the event organizer and cannot edit |
| `SERVICE_UNAVAILABLE` | Calendar connector is offline |

---

### `query_notion`

Query a Notion database and return matching rows.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `database` | string | ✅ | Known database name (e.g., `"partnerships"`, `"tasks"`, `"projects"`) | Which Notion DB to query |
| `filter` | string | ❌ | Keyword or entity name | Filter rows by this value |
| `status` | string | ❌ | e.g., `"open"`, `"in_progress"`, `"done"` | Filter by status field |
| `limit` | integer | ❌ | 1–20. Default: 10 | Max rows to return |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "rows": [
      {
        "row_id": "nr_001",
        "title": "Acme Corp - Q2 Renewal",
        "status": "in_progress",
        "owner": "user@company.com",
        "due_date": "2026-05-01",
        "properties": {
          "deal_value": "$120k",
          "action_items": ["Send revised pricing", "Schedule call"]
        }
      }
    ],
    "total_rows": 3
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `DATABASE_NOT_FOUND` | database name not recognized |
| `PERMISSION_DENIED` | User lacks access to this Notion database |
| `SERVICE_UNAVAILABLE` | Notion connector is offline |

**Constraints**
- `properties` shape varies by database — treat as a flexible key-value map
- Model must not assume a specific field exists; check before referencing

---

### `create_task`

Create a new task in the connected task manager (Notion/Asana/Linear).

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `title` | string | ✅ | 1–300 chars | Task title |
| `database` | string | ✅ | Known database name | Which project/board to add to |
| `due_date` | string | ❌ | ISO 8601 `YYYY-MM-DD` | Task due date |
| `assignee` | string | ❌ | Email or username | Who to assign it to |
| `notes` | string | ❌ | Max 2,000 chars | Task description |
| `priority` | string | ❌ | `"low"` \| `"medium"` \| `"high"`. Default: `"medium"` | Task priority |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "task_id": "nt_001",
    "url": "https://notion.so/..."
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `DATABASE_NOT_FOUND` | database name not recognized |
| `INVALID_ASSIGNEE` | Assignee not found in workspace |
| `INVALID_DUE_DATE` | Date format not recognized or in the past |
| `SERVICE_UNAVAILABLE` | Task service connector is offline |

---

### `update_task`

Update the status, due date, or notes on an existing task.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `task_id` | string | ✅ | Valid task_id from `query_notion` or `create_task` | Task to update |
| `status` | string | ❌ | `"open"` \| `"in_progress"` \| `"done"` \| `"cancelled"` | New status |
| `due_date` | string | ❌ | ISO 8601 `YYYY-MM-DD` | Updated due date |
| `notes` | string | ❌ | Max 2,000 chars | Appended (not replaced) to existing notes |
| `assignee` | string | ❌ | Email or username | Reassign task |
| `priority` | string | ❌ | `"low"` \| `"medium"` \| `"high"` | Updated priority |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "task_id": "nt_001",
    "updated_fields": ["status", "due_date"]
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `TASK_NOT_FOUND` | task_id does not exist |
| `INVALID_STATUS` | Status value not in allowed set |
| `INVALID_DUE_DATE` | Date format not recognized |
| `SERVICE_UNAVAILABLE` | Task service connector is offline |

**Constraints**
- `notes` is appended, not replaced — to overwrite, the model must explicitly state the full new note content
- Marking a task `"done"` is not reversible via this tool; model should confirm with user before doing so

---

## 5. General / Control Flow Tools

---

### `ask_user`

Pause execution and ask the user a clarifying question or request confirmation.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `question` | string | ✅ | 1–500 chars | The question to surface to the user |
| `context` | string | ❌ | Max 500 chars | Brief background to help the user understand why this is being asked |
| `options` | array[string] | ❌ | 2–5 items, each max 100 chars | Suggested answer options (rendered as buttons) |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "user_response": "Yes, go ahead and send it."
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `QUESTION_TOO_LONG` | question exceeds 500 characters |

**Constraints**
- Model must call `ask_user` and wait for a response before proceeding on any irreversible action (send email to external party, create calendar invite with attendees, post to Slack)
- Do not use `ask_user` to ask questions whose answer is already in the conversation context — check first
- Do not chain multiple questions in one `ask_user` call; ask the single most important blocker

**Training Example**

```
<think>
The user asked me to send Sarah's reply. This is an external email —
I need to confirm before sending.
</think>
<tool_call>
{
  "name": "ask_user",
  "parameters": {
    "question": "Ready to send this reply to Sarah at Acme?",
    "context": "This will go to sarah.chen@acme.com regarding the Q2 pricing discussion.",
    "options": ["Yes, send it", "Let me review the draft first", "Cancel"]
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "user_response": "Yes, send it"
  }
}
</tool_result>

<think>
User confirmed. Now call send_email with the draft_id.
</think>
<tool_call>
{
  "name": "send_email",
  "parameters": {
    "draft_id": "d_xyz789"
  }
}
</tool_call>
```

---

### `done`

Signal that the task is complete. Delivers the final response to the user.

**Parameters**

| Name | Type | Required | Constraints | Description |
|------|------|----------|-------------|-------------|
| `summary` | string | ✅ | 1–2,000 chars | Final answer, summary, or result presented to the user |
| `actions_taken` | array[string] | ❌ | Max 10 items, each max 200 chars | List of concrete actions performed (e.g., "Sent email to sarah.chen@acme.com") |
| `follow_ups` | array[string] | ❌ | Max 5 items, each max 200 chars | Optional next steps the user might want to take |

**Returns (success)**

```json
{
  "status": "success",
  "data": {
    "acknowledged": true
  }
}
```

**Error Codes**

| Code | Meaning |
|------|---------|
| `SUMMARY_TOO_LONG` | summary exceeds 2,000 characters |

**Constraints**
- `done` must always be the last tool call — model cannot call any other tool after `done`
- Every trajectory must terminate with a `done` call — open-ended trajectories are invalid training data
- `summary` is what the user sees — write it for them, not for the model; no internal reasoning here
- `actions_taken` should only list actions that actually happened (confirmed sends, created events, etc.) — not planned actions

**Training Example**

```
<think>
Task complete. I've read the email thread, checked the calendar, pulled Notion context,
and prepped the meeting summary. Ready to deliver.
</think>
<tool_call>
{
  "name": "done",
  "parameters": {
    "summary": "**Meeting prep for Acme 2pm:**\n\n- **Topic:** Q2 partnership renewal\n- **Attendees:** Sarah Chen, Mike Patel\n- **Key concern:** Sarah flagged tier 3 pricing is ~20% above competitor in her Apr 25 email\n- **Open Notion items:** 2 action items overdue from the March review\n- **Suggested angle:** Come with a revised tier 3 proposal or a goodwill concession (e.g., extended contract term)",
    "actions_taken": [],
    "follow_ups": [
      "Draft a revised tier 3 pricing proposal",
      "Reply to Sarah's email confirming you'll address pricing today"
    ]
  }
}
</tool_call>
```

---

## 6. Global Constraints & Training Rules

### Safety Rails

| Constraint | Applies To |
|-----------|------------|
| Always confirm before sending to external parties | `send_email`, `reply_email` (external), `send_slack_message` (external) |
| Always confirm before creating calendar events with external attendees | `create_event` |
| Surface conflicts instead of silently overriding | `create_event`, `update_event` |
| Never mark tasks done without user intent | `update_task` |
| Never chain multiple `ask_user` calls back-to-back | `ask_user` |

### Trajectory Quality Rules (for data filtering)

1. **Every trajectory must end with `done`.** Incomplete trajectories are discarded.
2. **Minimum 1 `<think>` block per tool call.** Empty or trivial think blocks are rejected.
3. **No hallucinated IDs.** `thread_id`, `event_id`, `task_id`, `draft_id` must chain from prior `tool_result` outputs — never invented.
4. **Tool results must be used.** A tool call whose result is never referenced in subsequent reasoning is flagged as low quality.
5. **Step efficiency.** Prefer 3-step traces over 7-step traces when the task can be solved in fewer calls. Reward signal penalizes unnecessary tool calls.
6. **Error recovery is positive signal.** Trajectories that encounter an error code and recover gracefully (by re-planning or asking the user) are high-value training examples — include them.

### ID Chaining Rule

IDs must flow from tool results, never be invented:

```
search_email → thread_id → read_email(thread_id) ✅
draft_email → draft_id → send_email(draft_id)   ✅
check_calendar → event_id → update_event(event_id) ✅
"t_abc123" hardcoded without prior search_email  ❌
```

---

## 7. Full End-to-End Trajectory Example

**Task:** *"Prep me for my 2pm Acme meeting and send Sarah a quick note that I've reviewed her pricing concern."*

```
<think>
Two subtasks: (1) prep for the 2pm meeting, (2) send Sarah a note.
I'll start by checking the calendar for the 2pm event.
</think>
<tool_call>
{
  "name": "check_calendar",
  "parameters": {
    "date": "2026-04-28",
    "time_range": "13:00-16:00"
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "events": [
      {
        "event_id": "ev_881",
        "title": "Q2 Partnership Review",
        "start": "2026-04-28T14:00:00Z",
        "end": "2026-04-28T15:00:00Z",
        "attendees": ["sarah.chen@acme.com", "mike.patel@acme.com"],
        "notes": "Discuss Q2 renewal terms"
      }
    ]
  }
}
</tool_result>

<think>
Meeting is with Sarah and Mike at Acme. I need to check recent email
from Sarah to understand her concerns.
</think>
<tool_call>
{
  "name": "search_email",
  "parameters": {
    "query": "Acme pricing",
    "from": "sarah.chen@acme.com",
    "date_range": "2w",
    "max_results": 3
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "threads": [
      {
        "thread_id": "t_8f3k1",
        "subject": "RE: Q2 Renewal — Pricing Concern",
        "from": "sarah.chen@acme.com",
        "snippet": "Hi, one concern before Thursday — tier 3 is about 20% above what...",
        "date": "2026-04-25T14:30:00Z",
        "unread": true,
        "message_count": 2
      }
    ],
    "total_matches": 1
  }
}
</tool_result>

<think>
Relevant thread found. Reading the full email to get exact concern.
</think>
<tool_call>
{
  "name": "read_email",
  "parameters": {
    "thread_id": "t_8f3k1"
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "thread_id": "t_8f3k1",
    "subject": "RE: Q2 Renewal — Pricing Concern",
    "messages": [
      {
        "message_id": "m_002",
        "from": "sarah.chen@acme.com",
        "date": "2026-04-25T14:30:00Z",
        "body": "Hi, one concern before Thursday — tier 3 is about 20% above what Vendor B is quoting. We'd love to stay with you but need this addressed. Can you come with a revised proposal?"
      }
    ]
  }
}
</tool_result>

<think>
Sarah wants a revised tier 3 proposal. Now let me check Notion for
any open action items on this account.
</think>
<tool_call>
{
  "name": "query_notion",
  "parameters": {
    "database": "partnerships",
    "filter": "Acme",
    "status": "open"
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "rows": [
      {
        "row_id": "nr_044",
        "title": "Acme Corp — Q2 Renewal",
        "status": "in_progress",
        "properties": {
          "action_items": ["Send updated SLA doc", "Confirm pricing tiers"]
        }
      }
    ]
  }
}
</tool_result>

<think>
Two open action items — both overdue. Now I have enough context for
meeting prep. Next, draft a reply to Sarah per the user's request.
</think>
<tool_call>
{
  "name": "draft_email",
  "parameters": {
    "to": ["sarah.chen@acme.com"],
    "subject": "RE: Q2 Renewal — Pricing Concern",
    "body": "Hi Sarah,\n\nThanks for flagging this. I've reviewed your concern on tier 3 pricing and I'll come prepared with a revised proposal today. Looking forward to the conversation at 2pm.\n\nBest,\n[Name]",
    "reply_to_thread_id": "t_8f3k1"
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "draft_id": "d_reply_88"
  }
}
</tool_result>

<think>
Draft is ready. This is an external email — I need to confirm with
the user before sending.
</think>
<tool_call>
{
  "name": "ask_user",
  "parameters": {
    "question": "Ready to send this reply to Sarah?",
    "context": "Replies to her pricing concern email, confirms you'll bring a revised tier 3 proposal to the 2pm.",
    "options": ["Yes, send it", "Let me edit it first", "Don't send"]
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "user_response": "Yes, send it"
  }
}
</tool_result>

<think>
User confirmed. Sending the draft.
</think>
<tool_call>
{
  "name": "send_email",
  "parameters": {
    "draft_id": "d_reply_88"
  }
}
</tool_call>

<tool_result>
{
  "status": "success",
  "data": {
    "message_id": "m_sent_112",
    "sent_at": "2026-04-28T09:45:00Z"
  }
}
</tool_result>

<think>
Email sent. Now I have everything for the meeting prep summary. Call done.
</think>
<tool_call>
{
  "name": "done",
  "parameters": {
    "summary": "**Meeting prep — Acme Q2 Review (2pm today):**\n\n**Attendees:** Sarah Chen, Mike Patel\n**Core issue:** Sarah flagged tier 3 pricing is ~20% above Vendor B — she's asking for a revised proposal\n**Open Notion items:** 2 overdue — send updated SLA doc, confirm pricing tiers\n**Suggested approach:** Lead with revised tier 3 pricing or a trade-off (e.g., multi-year discount)\n\n**Done:** Sent Sarah a reply confirming you've reviewed her concern and will come prepared.",
    "actions_taken": [
      "Sent reply to sarah.chen@acme.com (RE: Q2 Renewal — Pricing Concern)"
    ],
    "follow_ups": [
      "Prepare revised tier 3 pricing proposal before 2pm",
      "Send updated SLA doc (overdue Notion item)"
    ]
  }
}
</tool_call>
```

---

*Schema version 0.1 — lock before generating SFT trajectories. Any changes to tool names, parameter names, or return shapes require a full data regeneration.*
