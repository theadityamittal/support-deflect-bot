# Agent & RAG Interaction Flows

Complete documentation of all agent and RAG interaction flows, including every branch, conditional execution, error path, and data transformation in the system.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Entry Point: Slack Handler Lambda](#entry-point-slack-handler-lambda)
3. [Inbound Middleware Chain](#inbound-middleware-chain)
4. [SQS Enqueueing & Worker Lambda](#sqs-enqueueing--worker-lambda)
5. [Dependency Wiring](#dependency-wiring)
6. [Agent Orchestrator Loop](#agent-orchestrator-loop)
7. [Reasoning Phase](#reasoning-phase)
8. [Tool Execution Phase](#tool-execution-phase)
9. [Generation Phase](#generation-phase)
10. [Agent Tools (Detail)](#agent-tools-detail)
11. [RAG Pipeline: Ingestion Flow](#rag-pipeline-ingestion-flow)
12. [RAG Pipeline: Query Flow](#rag-pipeline-query-flow)
13. [LLM Router & Model Split](#llm-router--model-split)
14. [LLM Fallback Chain](#llm-fallback-chain)
15. [Turn Budget Enforcement](#turn-budget-enforcement)
16. [State Management](#state-management)
17. [Complete End-to-End Sequence Diagram](#complete-end-to-end-sequence-diagram)

---

## System Overview

```
Slack Event → Handler Lambda → Middleware Chain → SQS FIFO → Worker Lambda → Orchestrator
                                                                                  │
                                                              ┌───────────────────┤
                                                              ▼                   ▼
                                                     Reasoning Loop        Generation Phase
                                                     (Flash Lite)           (Flash)
                                                          │
                                                          ▼
                                                     Tool Execution
                                                     ├── search_kb (Pinecone)
                                                     ├── send_message (Slack)
                                                     ├── assign_channel (Slack)
                                                     ├── calendar_event (stub)
                                                     └── manage_progress (DynamoDB + LLM)
```

Two Lambdas form the request lifecycle:
- **Slack Handler Lambda** (`src/slack/handler.py`): Synchronous — verifies signatures, runs middleware, enqueues to SQS, returns 200 immediately.
- **Agent Worker Lambda** (`src/agent/worker.py`): Asynchronous — reads SQS, wires dependencies, runs the orchestrator, sends the Slack reply.

---

## Entry Point: Slack Handler Lambda

**File:** `src/slack/handler.py`

### Route Dispatch

```
HTTP POST arrives at API Gateway
    │
    ├── Read body, headers, path from event
    │
    ├── Retrieve signing secret
    │   ├── IF APP_SECRETS_ARN is set → Secrets Manager → parse JSON → "signing_secret" key
    │   └── ELSE → fallback to SLACK_SIGNING_SECRET env var
    │
    ├── Verify Slack signature (HMAC-SHA256)
    │   ├── FAIL → return 401 {"error": "Invalid signature"}  ← TERMINATE
    │   └── PASS → continue
    │
    └── Route by path:
        ├── /slack/commands  → _handle_slash_command()
        ├── /slack/interactions → _handle_interaction()  (stub, returns 200)
        └── /slack/events (default) → _handle_event()
```

### Event Handling (`_handle_event`)

```
Parse JSON body
    │
    ├── IF body.type == "url_verification"
    │   └── return 200 {"challenge": body.challenge}  ← TERMINATE (Slack URL verification)
    │
    ├── Parse into SlackEvent via SlackEvent.from_event_body()
    │   ├── IF event.type == "team_join"
    │   │   └── Extract user ID from nested user object, channel_id="", text=""
    │   └── ELSE (message, app_mention)
    │       └── Extract user, channel, text, thread_ts, detect is_bot
    │           is_bot = True IF event.bot_id is not None OR subtype == "bot_message"
    │
    ├── Run inbound middleware chain (see next section)
    │   ├── IF not allowed
    │   │   └── return 200 {"ok": true}  ← SILENT DROP or REJECTION (no SQS enqueue)
    │   └── IF allowed → continue
    │
    ├── Build SQSMessage
    │   ├── is_dm = channel_id.startswith("D")
    │   ├── MessageGroupId = "{workspace_id}#{user_id}" (FIFO ordering per user)
    │   └── MessageDeduplicationId = event_id (prevent duplicate processing)
    │
    ├── Enqueue to SQS FIFO
    │   ├── IF SQS_QUEUE_URL not set → log error, return (message lost)
    │   └── ELSE → sqs.send_message()
    │
    └── return 200 {"ok": true}
```

### Slash Command Handling (`_handle_slash_command`)

```
Parse body (try JSON first, fall back to form-encoded parse_qs)
    │
    ├── Build SlackCommand from body
    └── Delegate to handle_command(command, state_store)
        (handles /onboard-status, /onboard-help, /onboard-restart)
```

---

## Inbound Middleware Chain

**File:** `src/middleware/inbound/chain.py`

Checks run in cost order (cheapest first). Each check either **allows** (continue to next) or **short-circuits** (stop processing).

```
SlackEvent arrives
    │
    ├── 1. BotFilter (CPU only, ~0ms)
    │   ├── IF event.is_bot == True → DROP (silent, no response)
    │   └── ELSE → ALLOW
    │
    ├── 2. EmptyFilter (CPU only, ~0ms)
    │   ├── IF event_type == TEAM_JOIN → ALLOW (team_join has no text by design)
    │   ├── IF text is empty or whitespace → DROP (silent)
    │   └── ELSE → ALLOW
    │
    ├── 3. RateLimiter (1 DynamoDB conditional write)
    │   ├── Attempt acquire_lock(workspace_id, user_id) — DynamoDB conditional put with 60s TTL
    │   ├── IF lock NOT acquired → REJECT "Still working on your previous message..."
    │   └── IF lock acquired → ALLOW
    │
    ├── 4. InputSanitizer (CPU + conditional DynamoDB write)
    │   ├── IF event_type == TEAM_JOIN → ALLOW (no text to sanitize)
    │   ├── Check text against 8 injection regex patterns:
    │   │   - "ignore (all) previous instructions"
    │   │   - "ignore (all) prior instructions"
    │   │   - "reveal (your) system prompt"
    │   │   - "you are/act as now a different"
    │   │   - "your new instructions are"
    │   │   - "disregard (all) previous"
    │   │   - "override (your) system"
    │   │   - "forget everything/all (you) know"
    │   ├── IF any pattern matches:
    │   │   ├── Log injection attempt to DynamoDB
    │   │   └── REJECT "I can only help with onboarding questions."
    │   └── IF no match → ALLOW
    │
    └── 5. TokenBudgetGuard (2 DynamoDB reads)
        ├── Read daily usage turns for this user
        │   └── IF daily_turns >= max_turns_per_day (default 50)
        │       → REJECT "Daily limit reached. I'll be back tomorrow!"
        ├── Read monthly cost for this workspace
        │   └── IF monthly_cost >= max_monthly_cost (default $5.00)
        │       → REJECT "Monthly limit reached. Service resumes next month."
        └── ELSE → ALLOW (message proceeds to SQS)
```

### MiddlewareResult Types

| Method | `allowed` | `reason` | `should_respond` | Effect |
|--------|-----------|----------|-------------------|--------|
| `allow()` | `True` | `None` | `True` | Continue to next middleware |
| `reject(reason)` | `False` | `"..."` | `True` | Stop processing, send reason to user |
| `drop()` | `False` | `None` | `False` | Stop processing silently |

---

## SQS Enqueueing & Worker Lambda

### SQS Message Format

```json
{
    "version": "1.0",
    "event_id": "Ev12345",
    "workspace_id": "T12345",
    "user_id": "U12345",
    "channel_id": "D12345",
    "event_type": "message",
    "text": "Hi, I'm a new volunteer!",
    "timestamp": "1234567890.123456",
    "metadata": {
        "is_dm": true,
        "command": null,
        "thread_ts": null
    }
}
```

FIFO guarantees:
- **MessageGroupId** = `{workspace_id}#{user_id}` — messages from the same user are processed in order.
- **MessageDeduplicationId** = `event_id` — Slack retries don't cause duplicate processing.

### Worker Lambda (`lambda_handler`)

**File:** `src/agent/worker.py`

```
SQS trigger invokes lambda_handler(event, context)
    │
    ├── FOR each record in event.Records:
    │   │
    │   ├── Parse JSON body → extract workspace_id, user_id, channel_id, text
    │   │
    │   ├── _get_bot_token(workspace_id)
    │   │   ├── TRY: _get_app_secrets() → secrets["bot_token"]
    │   │   │   ├── IF APP_SECRETS_ARN set → Secrets Manager → JSON parse → cache
    │   │   │   │   (cached per Lambda cold start in module-level _cached_secrets)
    │   │   │   └── IF APP_SECRETS_ARN not set → raise ValueError
    │   │   ├── IF token found and != "placeholder" → return token
    │   │   └── FALLBACK: DynamoDB workspace config → config.bot_token
    │   │       └── IF no config found → raise ValueError (fatal)
    │   │
    │   ├── _create_orchestrator(...) → (see Dependency Wiring)
    │   │
    │   ├── TRY:
    │   │   ├── orchestrator.process_turn(user_message=text)
    │   │   │   → returns response_text (string)
    │   │   │
    │   │   └── _send_slack_message(bot_token, channel_id, response_text)
    │   │       → WebClient.chat_postMessage(channel, text)
    │   │
    │   ├── FINALLY (always runs, even on error):
    │   │   └── _release_user_lock(workspace_id, user_id)
    │   │       ├── Create DynamoStateStore
    │   │       ├── store.release_lock(workspace_id, user_id)
    │   │       └── IF release fails → log exception (don't re-raise; lock has TTL fallback)
    │   │
    │   └── EXCEPT Exception:
    │       ├── Log exception
    │       └── return {"statusCode": 500, "body": "Processing failed"}
    │
    └── return {"statusCode": 200, "body": "OK"}
```

---

## Dependency Wiring

**Function:** `_create_orchestrator()` in `src/agent/worker.py`

Constructs the full dependency graph for one turn:

```
_create_orchestrator(workspace_id, user_id, channel_id, bot_token)
    │
    ├── Load settings from get_settings()
    │
    ├── DynamoDB: boto3.resource("dynamodb").Table(settings.dynamodb_table_name)
    │   └── DynamoStateStore(table)
    │
    ├── LLM Provider:
    │   ├── secrets = _get_app_secrets()
    │   ├── GeminiProvider(api_key=secrets["gemini_api_key"])
    │   └── LLMRouter(provider, reasoning_model_id, generation_model_id)
    │
    ├── Slack: WebClient(token=bot_token) → SlackClient(web_client)
    │
    ├── Pinecone: PineconeVectorStore(api_key=secrets["pinecone_api_key"], index_name)
    │
    ├── Tools dict:
    │   ├── "search_kb"        → SearchKBTool(vectorstore, namespace=workspace_id)
    │   ├── "send_message"     → SendMessageTool(slack_client, channel_id)
    │   ├── "assign_channel"   → AssignChannelTool(web_client, user_id)
    │   ├── "calendar_event"   → CalendarEventTool()
    │   └── "manage_progress"  → ManageProgressTool(state_store, workspace_id, user_id, router)
    │
    ├── TurnBudgetEnforcer(
    │       max_reasoning_calls = settings value (default 3),
    │       max_generation_calls = settings value (default 1),
    │       max_tool_calls = settings value (default 4),
    │       max_output_tokens = settings value (default 5000),
    │   )
    │
    └── Orchestrator(router, state_store, tools, workspace_id, user_id, channel_id, budget)
```

---

## Agent Orchestrator Loop

**File:** `src/agent/orchestrator.py`

The orchestrator runs a three-phase loop for each user message:

```
process_turn(user_message)
    │
    ├── Load plan from DynamoDB: state_store.get_plan(workspace_id, user_id)
    │   ├── IF plan exists → OnboardingPlan with steps, key_facts, recent_messages
    │   └── IF no plan → None (triggers intake flow)
    │
    ├── Initialize tool_results = []
    │
    ├── TRY:
    │   │
    │   ├── ═══ REASONING LOOP (max 5 iterations) ═══
    │   │   │
    │   │   ├── budget.check_reasoning_budget()
    │   │   │   └── IF reasoning_calls >= max (3) → raise TurnBudgetExceededError
    │   │   │
    │   │   ├── Build messages via build_system_context(plan, user_message)
    │   │   │   (see Reasoning Phase for prompt structure)
    │   │   │
    │   │   ├── IF tool_results is not empty:
    │   │   │   └── Append tool results as assistant message:
    │   │   │       "[tool_name]: {json data}" for each result
    │   │   │
    │   │   ├── router.invoke(role=REASONING, messages)
    │   │   │   → Routes to Gemini 2.5 Flash Lite (max_tokens=1000)
    │   │   │
    │   │   ├── budget.record_reasoning_call(output_tokens)
    │   │   │
    │   │   ├── _parse_reasoning(response.text)
    │   │   │   ├── TRY: JSON.parse → {"action": "respond"|"tool_call", ...}
    │   │   │   └── EXCEPT JSONDecodeError → {"action": "respond"} (treat as final answer)
    │   │   │
    │   │   ├── IF decision.action == "respond"
    │   │   │   └── BREAK out of reasoning loop → proceed to generation
    │   │   │
    │   │   └── IF decision.action == "tool_call"
    │   │       │
    │   │       ├── Extract tool_name, params from decision
    │   │       │
    │   │       ├── validate_tool_call(tool_name, params, available_tools)
    │   │       │   ├── IF tool_name is empty → INVALID, reason: "Empty tool name"
    │   │       │   ├── IF tool_name not in available_tools → INVALID, reason: "Unknown tool"
    │   │       │   └── ELSE → VALID
    │   │       │
    │   │       ├── IF validation.valid == False:
    │   │       │   └── log warning → CONTINUE (skip tool, retry reasoning)
    │   │       │
    │   │       ├── budget.check_tool_budget()
    │   │       │   └── IF tool_calls >= max (4) → raise TurnBudgetExceededError
    │   │       │
    │   │       ├── tool = tools[tool_name]
    │   │       ├── result = tool.execute(**params)
    │   │       ├── budget.record_tool_call()
    │   │       │
    │   │       └── Append to tool_results:
    │   │           {"tool": name, "ok": bool, "data": dict, "error": str|None}
    │   │
    │   ├── ═══ GENERATION PHASE ═══
    │   │   │
    │   │   ├── budget.check_generation_budget()
    │   │   │   └── IF generation_calls >= max (1) → raise TurnBudgetExceededError
    │   │   │
    │   │   ├── Build gen_messages via build_response_prompt(plan, user_message, tool_results)
    │   │   │   (see Generation Phase for prompt structure)
    │   │   │
    │   │   ├── router.invoke(role=GENERATION, messages)
    │   │   │   → Routes to Gemini 2.5 Flash (max_tokens=2000)
    │   │   │
    │   │   ├── budget.record_generation_call(output_tokens)
    │   │   │
    │   │   ├── validate_output(generation.text)
    │   │   │   ├── IF text is empty/whitespace → return fallback:
    │   │   │   │   "I'm having trouble processing that right now..."
    │   │   │   ├── IF len(text) > 4000 → truncate to 4000 chars
    │   │   │   └── ELSE → return text as-is
    │   │   │
    │   │   └── _update_context(plan, user_message, response_text)
    │   │       ├── IF plan is None → return (no context to update)
    │   │       └── ELSE:
    │   │           ├── Keep last 4 messages from plan.recent_messages
    │   │           ├── Append new user + assistant messages
    │   │           ├── Create updated plan via dataclasses.replace()
    │   │           └── state_store.save_plan(updated)
    │   │
    │   └── return response_text
    │
    └── EXCEPT TurnBudgetExceededError:
        └── return "I've reached my processing limit for this message..."
```

---

## Reasoning Phase

### Prompt Construction — `build_system_context()`

**File:** `src/agent/prompts/system.py`

Two branches depending on whether a plan exists:

#### Branch 1: No Plan (Intake Flow)

```
plan == None
    │
    └── Messages:
        ├── system: SYSTEM_BASE + INTAKE_CONTEXT
        │   Content: "You are Onboard Assist... No onboarding plan exists yet.
        │             Ask 1-3 questions: role/team, experience level, preferences.
        │             If first message provides info, skip to plan generation."
        └── user: user_message
```

#### Branch 2: Active Plan

```
plan exists
    │
    ├── Format plan into status display:
    │   "Volunteer: {name} | Role: {role}"
    │   "✅ 1. Step title — summary"
    │   "🔄 2. Current step"
    │   "⬜ 3. Pending step"
    │
    ├── Format key_facts as bullet list (or "None yet")
    │
    └── Messages:
        ├── system: SYSTEM_BASE + Current Plan + Key Facts + Instructions
        │   Instructions: "Look at the current step (in_progress) and decide
        │                  what to do. You can use tools: search_kb, send_message,
        │                  assign_channel, calendar_event, manage_progress."
        ├── [last 5 recent_messages from plan context]
        └── user: user_message
```

### Reasoning Output Format

The reasoning model outputs JSON:

```json
// Decision: respond (no tool needed)
{"action": "respond", "reasoning": "User is just saying hello"}

// Decision: call a tool
{"action": "tool_call", "tool": "search_kb", "params": {"query": "volunteer orientation"}}
```

If the output is not valid JSON, `_parse_reasoning()` treats it as `{"action": "respond"}` — the model's raw text is discarded and the system proceeds to generation with whatever tool results have accumulated.

---

## Tool Execution Phase

### Validation Gate

```
tool_call decision from reasoning
    │
    ├── validate_tool_call(tool_name, params, available_tools)
    │   │
    │   ├── IF tool_name == "" → INVALID ("Empty tool name")
    │   │   └── CONTINUE (skip this iteration, re-enter reasoning loop)
    │   │
    │   ├── IF tool_name not in {"search_kb", "send_message", "assign_channel",
    │   │                         "calendar_event", "manage_progress"}
    │   │   → INVALID ("Unknown tool: {name}")
    │   │   └── CONTINUE
    │   │
    │   └── ELSE → VALID
    │
    ├── budget.check_tool_budget()
    │   └── IF exceeded → TurnBudgetExceededError → graceful fallback message
    │
    └── Execute tool → ToolResult(ok, data, error)
```

### ToolResult Contract

All tools return frozen `ToolResult`:

```python
ToolResult.success(data={"key": "value"})  # ok=True
ToolResult.failure(error="description")     # ok=False
```

Results are accumulated in `tool_results[]` and fed back into the next reasoning iteration.

---

## Generation Phase

### Prompt Construction — `build_response_prompt()`

**File:** `src/agent/prompts/responder.py`

```
build_response_prompt(plan, user_message, tool_results)
    │
    ├── IF tool_results exist:
    │   └── Format each as "Tool: {name}\nResult: {data}"
    │
    ├── IF plan exists:
    │   └── Find current in_progress step → "Current step: {title}"
    │
    └── Messages:
        ├── system: "You are Onboard Assist, writing a response..."
        │   + Guidelines (warm, concise, use tool results, no internal mention,
        │     under 300 words, markdown formatting)
        │   + [plan context if available]
        │   + [tool results if available]
        └── user: user_message
```

### Output Validation — `validate_output()`

**File:** `src/middleware/agent/output_validator.py`

```
validate_output(text)
    │
    ├── IF text is None/empty/whitespace
    │   └── return "I'm having trouble processing that right now.
    │              Could you try rephrasing your question?"
    │
    ├── IF len(text) > 4000
    │   └── return text[:4000]  (hard truncation)
    │
    └── ELSE → return text as-is
```

---

## Agent Tools (Detail)

### 1. search_kb — Knowledge Base Search

**File:** `src/agent/tools/search_kb.py`

```
execute(query="...")
    │
    ├── vectorstore.search(query, namespace=workspace_id, top_k=5)
    │   │
    │   ├── Pinecone integrated inference:
    │   │   ├── Embeds query text automatically (no separate embedding call)
    │   │   ├── Searches within workspace namespace
    │   │   └── Returns hits with _id, _score, fields.chunk_text
    │   │
    │   └── Returns list[SearchResult(id, score, text, metadata)]
    │
    ├── ON SUCCESS:
    │   └── ToolResult.success(data={"results": [{"id", "score", "text"}, ...]})
    │
    └── ON EXCEPTION:
        └── ToolResult.failure(error="Knowledge base search failed: {e}")
```

### 2. send_message — Slack Message

**File:** `src/agent/tools/send_message.py`

```
execute(text="...", blocks=None)
    │
    ├── Build send_kwargs: {channel: channel_id, text: text}
    │   └── IF blocks is not None → add blocks to kwargs
    │
    ├── slack_client.send_message(**send_kwargs)
    │
    ├── ON SUCCESS:
    │   └── ToolResult.success(data={"ts": message_timestamp})
    │
    └── ON EXCEPTION:
        └── ToolResult.failure(error="Failed to send message: {e}")
```

### 3. assign_channel — Slack Channel Invitation

**File:** `src/agent/tools/assign_channel.py`

```
execute(channel_id="C12345")
    │
    ├── web_client.conversations_invite(channel=channel_id, users=user_id)
    │
    ├── ON SUCCESS:
    │   └── ToolResult.success(data={"channel_id": "...", "invited": True})
    │
    └── ON EXCEPTION:
        ├── IF "already_in_channel" in error_msg:
        │   └── ToolResult.success(data={"channel_id": "...", "already_member": True})
        │       (treated as idempotent success)
        └── ELSE:
            └── ToolResult.failure(error="Channel assignment failed: {msg}")
```

### 4. calendar_event — Google Calendar (Stub)

**File:** `src/agent/tools/calendar_event.py`

```
execute(title="...", date="...", time="...", duration_minutes=60, attendee_email=None)
    │
    └── ALWAYS returns ToolResult.success(data={
            "stubbed": True,
            "title": title,
            "message": "Calendar event scheduled (stub)"
        })
        (Full Google Calendar implementation deferred to Phase 4)
```

### 5. manage_progress — Plan CRUD + Replan

**File:** `src/agent/tools/manage_progress.py`

Five actions dispatched by the `action` parameter:

#### Action: `get_plan`

```
get_plan()
    │
    ├── state_store.get_plan(workspace_id, user_id)
    ├── IF plan is None → ToolResult.success(data={"plan": None})
    └── IF plan exists → ToolResult.success(data={"plan": {serialized plan dict}})
```

#### Action: `start_step`

```
start_step(step_id=2)
    │
    ├── Load plan from DynamoDB
    ├── IF no plan → ToolResult.failure("No active plan found")
    ├── Find step by ID, replace status → IN_PROGRESS, set started_at
    ├── Save updated plan
    └── ToolResult.success(data={"step_id": 2, "status": "in_progress"})
```

#### Action: `complete_step`

```
complete_step(step_id=2, summary="Completed orientation video")
    │
    ├── Load plan from DynamoDB
    ├── IF no plan → ToolResult.failure("No active plan found")
    ├── Find step by ID, replace status → COMPLETED, set completed_at, summary
    ├── Save updated plan
    │
    ├── _check_plan_completion(updated_plan):
    │   ├── IF ALL steps are COMPLETED:
    │   │   ├── Calculate duration_minutes from plan.created_at to now
    │   │   ├── Extract channels from steps where requires_tool == "assign_channel"
    │   │   ├── Create CompletionRecord (permanent, no TTL)
    │   │   ├── state_store.save_completion_record(record)
    │   │   ├── Set plan.status = COMPLETED
    │   │   └── Save completed plan
    │   └── IF NOT all completed → return (no action)
    │
    └── ToolResult.success(data={"step_id": 2, "status": "completed"})
```

#### Action: `add_fact`

```
add_fact(fact="User prefers morning meetings")
    │
    ├── Load plan from DynamoDB
    ├── IF no plan → ToolResult.failure("No active plan found")
    ├── Append fact to plan.key_facts tuple (immutable; create new tuple)
    ├── Save updated plan
    └── ToolResult.success(data={"fact": "...", "total_facts": N})
```

#### Action: `replan`

```
replan(reason="User changed roles from fundraising to marketing")
    │
    ├── Load plan from DynamoDB
    ├── IF no plan → ToolResult.failure("No active plan found")
    ├── IF router is None → ToolResult.failure("Router not available for replanning")
    │
    ├── Build replan prompt via build_replan_prompt(plan, reason)
    │   ├── System: "You are replanning an onboarding sequence.
    │   │           Rules: ONLY modify pending steps, completed/in_progress are FROZEN.
    │   │           Output FULL step list as JSON array."
    │   └── User: Current plan steps + reason for replan
    │
    ├── router.invoke(role=REASONING, messages)
    │   → Routes to Flash Lite for replan generation
    │
    ├── Parse response as JSON array of steps
    │   ├── ON JSONDecodeError → ToolResult.failure("Failed to parse replan response")
    │   └── ON SUCCESS → create new PlanStep list
    │
    ├── Replace plan.steps, increment plan.version
    ├── Save updated plan
    └── ToolResult.success(data={"version": N, "steps": count})
```

---

## RAG Pipeline: Ingestion Flow

**File:** `src/rag/pipeline.py`, `src/rag/scraper.py`, `src/rag/storage.py`, `src/rag/chunker.py`

### Web Scraping

```
scrape_site(start_url, max_pages=50)
    │
    ├── Parse start_url → extract domain for same-domain constraint
    ├── Initialize: visited={}, to_visit=[start_url], pages=[]
    │
    └── WHILE to_visit is not empty AND len(pages) < max_pages:
        │
        ├── Pop first URL from to_visit
        ├── Normalize URL (remove fragment, trailing slash)
        │
        ├── IF already visited → SKIP
        ├── Mark as visited
        │
        ├── TRY: scrape_page(url)
        │   │
        │   ├── httpx.get(url, follow_redirects=True, timeout=30s)
        │   ├── response.raise_for_status()
        │   │
        │   ├── Parse HTML with BeautifulSoup (lxml parser)
        │   ├── Strip tags: nav, footer, header, script, style, noscript, aside
        │   │
        │   ├── Extract title from <title> tag
        │   │
        │   ├── Extract text from: p, h1-h6, li, td, th, blockquote
        │   ├── Extract alt text from img: "[Image: {alt}]"
        │   ├── Collapse multiple blank lines
        │   │
        │   └── Return ScrapedPage(url, title, text, raw_html)
        │
        ├── Extract same-domain links from page HTML
        │   ├── Find all <a href="...">
        │   ├── Resolve relative URLs with urljoin
        │   ├── IF link domain == start domain AND not visited → add to to_visit
        │   └── ELSE → skip
        │
        └── EXCEPT → log warning, skip page, continue crawling
```

### Full Ingestion Pipeline

```
ingest_page(workspace_id, url, text, raw_html, metadata=None)
    │
    ├── 1. Store raw HTML in S3
    │   ├── s3_key = "{workspace_id}/pages/{sanitized_path}_{hash}.html"
    │   └── s3.put_object(Bucket, Key, Body=raw_html)
    │
    ├── 2. Update S3 manifest
    │   ├── Read existing manifest: "{workspace_id}/manifest.json"
    │   │   └── IF not found → start with {"pages": []}
    │   ├── Compute content_hash = MD5(raw_html)
    │   ├── IF URL already in manifest → update entry
    │   ├── IF new URL → append entry
    │   └── Write updated manifest back to S3
    │
    ├── 3. Chunk text
    │   ├── chunk_text(text, chunk_size=512, chunk_overlap=50, metadata={source_url: url, ...})
    │   │
    │   │   Chunking algorithm:
    │   │   ├── IF overlap >= chunk_size → raise ValueError
    │   │   ├── IF text is empty → return []
    │   │   │
    │   │   └── WHILE start < len(text):
    │   │       ├── Take segment of chunk_size chars
    │   │       ├── IF not last chunk:
    │   │       │   ├── Search for last sentence boundary (. ! ? followed by whitespace)
    │   │       │   ├── IF boundary found AND boundary > overlap:
    │   │       │   │   └── Split at sentence boundary (prefer clean breaks)
    │   │       │   └── ELSE → split at chunk_size limit
    │   │       ├── Create Chunk(text, index, metadata)
    │   │       └── Advance by (segment_length - overlap), minimum 1 char
    │   │
    │   └── IF no chunks produced → log warning, return 0
    │
    ├── 4. Generate chunk IDs
    │   └── "{workspace_id}_{md5(url)[:8]}_{chunk_index}"
    │
    ├── 5. Upsert to Pinecone
    │   ├── Build records: {"_id": id, "chunk_text": text, ...metadata}
    │   ├── vectorstore.upsert_records(namespace=workspace_id, records)
    │   │   (Pinecone Inference embeds text automatically — no separate embedding API call)
    │   └── Log: "Ingested N chunks from URL"
    │
    └── Return number of chunks indexed
```

---

## RAG Pipeline: Query Flow

**File:** `src/rag/pipeline.py`, `src/rag/confidence.py`

```
query(query, workspace_id, top_k=10, filter_metadata=None)
    │
    ├── 1. Vector Search
    │   ├── vectorstore.search(query, namespace=workspace_id, top_k, filter_metadata)
    │   │   ├── Pinecone integrated inference embeds query automatically
    │   │   ├── Searches within workspace namespace only (tenant isolation)
    │   │   ├── Optional metadata filter (team, role, category)
    │   │   └── Returns list[SearchResult(id, score, text, metadata)]
    │   └── Ordered by relevance score (descending)
    │
    ├── 2. Keyword Extraction
    │   ├── _extract_keywords(query)
    │   │   ├── Regex tokenize → lowercase words
    │   │   ├── Remove 60+ stop words (articles, prepositions, pronouns, etc.)
    │   │   ├── Remove words with len <= 2
    │   │   └── Return set of meaningful keywords
    │
    ├── 3. Confidence Scoring
    │   ├── calculate_confidence(similarity_scores, query_keywords, result_texts, max_expected=10)
    │   │
    │   │   IF no similarity scores → return score=0.0 with zero breakdown
    │   │
    │   │   Four factors:
    │   │   │
    │   │   ├── Similarity Factor (weight: 40%)
    │   │   │   └── Average of all cosine similarity scores
    │   │   │
    │   │   ├── Count Factor (weight: 20%)
    │   │   │   └── min(num_results / max_expected_results, 1.0)
    │   │   │       More results = higher confidence
    │   │   │
    │   │   ├── Keyword Overlap Factor (weight: 20%)
    │   │   │   └── Fraction of query keywords found in any result text
    │   │   │       Concatenates all result texts, checks each keyword
    │   │   │
    │   │   └── Content Length Factor (weight: 20%)
    │   │       └── Normalized average text length:
    │   │           (avg_length - 50) / (2000 - 50), clamped to [0, 1]
    │   │           Longer results = more informative = higher confidence
    │   │
    │   │   Final score = 0.4*similarity + 0.2*count + 0.2*keyword + 0.2*length
    │   │   Clamped to [0, 1], rounded to 4 decimal places
    │   │
    │   └── Returns ConfidenceResult(score, breakdown)
    │
    └── Return QueryResult(query, results, confidence)
```

---

## LLM Router & Model Split

**File:** `src/llm/router.py`

### Two-Model Architecture

| Role | Model | Cost (per 1M tokens) | Max Tokens | Purpose |
|------|-------|---------------------|------------|---------|
| REASONING | `gemini-2.5-flash-lite` | $0.10 input / $0.40 output | 1000 | Cheap reasoning: decide what to do, pick tools |
| GENERATION | `gemini-2.5-flash` | $0.30 input / $2.50 output | 2000 | Capable generation: user-facing responses |

### Router Flow

```
router.invoke(role, messages, max_tokens=None)
    │
    ├── Look up model_id from role → _model_map[role]
    ├── Determine max_tokens: explicit override or default per role
    │
    ├── provider.invoke(messages, model_id, max_tokens)
    │   └── GeminiProvider → OpenAI SDK → Gemini API endpoint
    │       POST https://generativelanguage.googleapis.com/v1beta/openai/
    │       → chat.completions.create(model, messages, max_tokens)
    │       → Return LLMResponse(text, input_tokens, output_tokens, model_id)
    │
    ├── Accumulate token usage: _total_input_tokens, _total_output_tokens
    │
    ├── Calculate cost: (tokens / 1M) * price_per_1m
    │   └── Only if model_id is in MODEL_PRICING dict
    │
    └── Return LLMResponse
```

---

## LLM Fallback Chain

**File:** `src/llm/fallback.py`

Not currently wired into the orchestrator (the router uses a single provider), but available for resilient multi-provider retry:

```
FallbackChain(providers=[provider_a, provider_b], model_ids=[model_a, model_b])

invoke(messages, max_tokens=1000)
    │
    ├── FOR each (provider, model_id) pair in order:
    │   │
    │   ├── TRY: provider.invoke(messages, model_id, max_tokens)
    │   │   └── ON SUCCESS → return LLMResponse immediately
    │   │
    │   └── EXCEPT Exception:
    │       └── Log warning, record error, try next provider
    │
    └── IF all providers failed:
        └── raise LLMUnavailableError("All N providers failed: {errors}")
```

Designed chain order from spec: Flash Lite → Flash → graceful error.

---

## Turn Budget Enforcement

**File:** `src/middleware/agent/turn_budget.py`

Per-turn limits prevent runaway execution:

| Budget | Default | Checked Before |
|--------|---------|---------------|
| `max_reasoning_calls` | 3 | Each reasoning loop iteration |
| `max_generation_calls` | 1 | Generation phase |
| `max_tool_calls` | 4 | Each tool execution |
| `max_output_tokens` | 5000 | (tracked, check available but not called in current orchestrator) |

```
Any budget check:
    │
    ├── IF current count >= max
    │   └── raise TurnBudgetExceededError("{type} limit reached ({max})")
    │
    └── ELSE → continue

TurnBudgetExceededError caught in orchestrator:
    └── return "I've reached my processing limit for this message.
               Here's what I have so far — feel free to send another
               message to continue."
```

Budget resets are available via `budget.reset()` and `router.reset_usage()` but are not called within a single turn (they're intended for multi-turn reset).

---

## State Management

**File:** `src/state/models.py`

### DynamoDB Single-Table Design

| Entity | PK | SK | TTL |
|--------|----|----|-----|
| OnboardingPlan | `WORKSPACE#{workspace_id}` | `PLAN#{user_id}` | No |
| CompletionRecord | `WORKSPACE#{workspace_id}` | `COMPLETED#{user_id}` | No (permanent) |
| UsageRecord | (per implementation) | (per implementation) | Yes |
| WorkspaceConfig | (per implementation) | (per implementation) | No |
| Processing Lock | (per implementation) | (per implementation) | 60s TTL |

### Immutable Model Pattern

All models are frozen dataclasses. Updates create new instances via `dataclasses.replace()`:

```python
# Never mutate:
plan.status = PlanStatus.COMPLETED  # ← Raises FrozenInstanceError

# Always create new:
updated = replace(plan, status=PlanStatus.COMPLETED, updated_at=now)
state_store.save_plan(updated)
```

### Plan Lifecycle

```
New user (no plan)
    │
    ├── Intake conversation (1-3 messages)
    │   └── Orchestrator uses INTAKE_CONTEXT prompt
    │       → Asks role, experience, preferences
    │
    ├── Plan generation (via manage_progress.replan or direct creation)
    │   └── LLM generates JSON array of 5-8 steps
    │
    ├── Plan execution
    │   ├── Steps transition: PENDING → IN_PROGRESS → COMPLETED
    │   ├── Key facts accumulated via add_fact
    │   ├── Replans possible (version incremented, only pending steps change)
    │   └── Recent messages maintained (sliding window of last 5)
    │
    └── Plan completion
        ├── All steps COMPLETED
        ├── CompletionRecord created (permanent audit trail)
        └── Plan status → COMPLETED
```

---

## Complete End-to-End Sequence Diagram

```
User                  Slack API          Handler Lambda         SQS FIFO           Worker Lambda          Orchestrator
  │                      │                    │                    │                     │                      │
  │── send message ─────>│                    │                    │                     │                      │
  │                      │── HTTP POST ──────>│                    │                     │                      │
  │                      │                    │                    │                     │                      │
  │                      │                    ├─ Verify signature  │                     │                      │
  │                      │                    │  (HMAC-SHA256)     │                     │                      │
  │                      │                    │                    │                     │                      │
  │                      │                    ├─ Parse SlackEvent  │                     │                      │
  │                      │                    │                    │                     │                      │
  │                      │                    ├─ BotFilter ────── OK                     │                      │
  │                      │                    ├─ EmptyFilter ──── OK                     │                      │
  │                      │                    ├─ RateLimiter ──── OK (lock acquired)     │                      │
  │                      │                    ├─ InputSanitizer ─ OK                     │                      │
  │                      │                    ├─ BudgetGuard ──── OK                     │                      │
  │                      │                    │                    │                     │                      │
  │                      │<── 200 OK ─────────│── enqueue ────────>│                     │                      │
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │── SQS trigger ──────>│                      │
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     ├─ Parse SQS body       │
  │                      │                    │                    │                     ├─ Get bot token        │
  │                      │                    │                    │                     ├─ Wire dependencies    │
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     ├── process_turn() ────>│
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     │                      ├─ Load plan from DynamoDB
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     │                      ├─ ══ REASONING LOOP ══
  │                      │                    │                    │                     │                      ├─ Build system context
  │                      │                    │                    │                     │                      ├─ Flash Lite: "What should I do?"
  │                      │                    │                    │                     │                      │   → {"action":"tool_call","tool":"search_kb",...}
  │                      │                    │                    │                     │                      ├─ Validate tool call
  │                      │                    │                    │                     │                      ├─ Execute search_kb → Pinecone
  │                      │                    │                    │                     │                      ├─ Append tool result
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     │                      ├─ Flash Lite: "Now what?"
  │                      │                    │                    │                     │                      │   → {"action":"respond"}
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     │                      ├─ ══ GENERATION ══
  │                      │                    │                    │                     │                      ├─ Build response prompt
  │                      │                    │                    │                     │                      ├─ Flash: Generate user-facing text
  │                      │                    │                    │                     │                      ├─ Validate output
  │                      │                    │                    │                     │                      ├─ Update plan context
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     │<── response_text ─────│
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     ├─ Slack chat_postMessage
  │<── bot reply ────────│<─────────────────────────────────────────────────────────────│                      │
  │                      │                    │                    │                     │                      │
  │                      │                    │                    │                     ├─ Release user lock    │
  │                      │                    │                    │                     │  (finally block)      │
```

### Worst-Case Execution Per Turn

| Resource | Count | Model/Service |
|----------|-------|---------------|
| Reasoning calls | up to 3 | Gemini 2.5 Flash Lite |
| Tool calls | up to 4 | Pinecone / Slack / DynamoDB |
| Generation calls | 1 | Gemini 2.5 Flash |
| DynamoDB reads | 2-4 | Plan load + tool reads |
| DynamoDB writes | 1-3 | Plan save + lock release |
| Pinecone queries | 0-4 | Via search_kb tool |
| Slack API calls | 1-5 | Final reply + send_message + assign_channel |
