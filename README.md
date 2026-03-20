# Onboard Assist

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/theadityamittal/onboard-assist/actions/workflows/ci.yml/badge.svg)](https://github.com/theadityamittal/onboard-assist/actions)
[![Coverage: 90%+](https://img.shields.io/badge/coverage-90%25%2B-brightgreen.svg)]()

Slack bot that onboards nonprofit volunteers. Asks intake questions, builds a personalized plan, then walks through it: answering from the org's knowledge base, assigning Slack channels, scheduling Google Calendar meetings. Picks up where it left off across sessions.

Built as a distributable platform. Any Slack workspace can install it. [Changing the Present](https://changingthepresent.org) is the demo tenant.

## Install

<a href="https://slack.com/oauth/v2/authorize?client_id=10754698455984.10731248951974&scope=app_mentions:read,channels:manage,channels:read,chat:write,chat:write.public,commands,groups:read,im:history,im:read,im:write,usergroups:read,users:read,users:read.email&redirect_uri=https://w7x89hdhpj.execute-api.us-east-1.amazonaws.com/prod/slack/oauth/callback"><img alt="Add to Slack" height="40" width="139" src="https://platform.slack-edge.com/img/add_to_slack.png" srcset="https://platform.slack-edge.com/img/add_to_slack.png 1x, https://platform.slack-edge.com/img/add_to_slack@2x.png 2x" /></a>

## Why this exists

I volunteered at Changing the Present and watched the same onboarding happen differently every time depending on who was running it. New volunteers got inconsistent info, team leads repeated themselves constantly, and nobody knew who'd actually finished orientation. This is my attempt to fix that with a bot.

## How it works

```
1. Workspace admin installs via "Add to Slack" OAuth flow
2. Admin provides org website URL, bot scrapes and indexes the knowledge base
3. New volunteer joins workspace, bot DMs them
4. Intake questions figure out their role and experience
5. Bot generates a personalized onboarding plan (5-8 steps)
6. Walks through the plan conversationally:
   - Answers questions from the knowledge base (RAG)
   - Assigns volunteer to the right Slack channels
   - Creates orientation meeting on Google Calendar
   - Tracks progress, resumes across sessions
   - Replans if the conversation goes in a different direction
7. Completion record saved for audit trail
```

## Architecture

```
                         ┌──────────────────────────────┐
                         │     API Gateway (REST)        │
                         │  5 routes (Slack + OAuth)     │
                         └──────┬───────────────┬───────┘
                                │               │
                     events/commands        OAuth callbacks
                                │               │
                                v               v
                         ┌────────────┐  ┌────────────┐  ┌────────────┐
                         │   Slack    │  │Slack OAuth │  │Google OAuth│
                         │  Handler   │  │  Lambda    │  │  Callback  │
                         │  Lambda    │  │            │  │  Lambda    │
                         └─────┬──────┘  └────────────┘  └────────────┘
                               │
                    signature verify
                    CPU filters +
                    concurrency guard
                    enqueue to SQS
                               │
                               v
                         ┌────────────┐       ┌────────────┐
                         │  SQS FIFO  │──────>│  SQS DLQ   │
                         │  Queue     │       │  (3 fails) │
                         └─────┬──────┘       └────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────────┐
│                      Agent Worker Lambda                         │
│                                                                  │
│  Input Sanitizer + Token Budget Guard (worker middleware)         │
│                                                                  │
│  Orchestrator (Plan + ReAct + Tool Calling)                      │
│      │                                                           │
│      ├── search_kb ──────────────────────────────> Pinecone      │
│      ├── send_message ───────────────────────────> Slack API     │
│      ├── assign_channel ─────────────────────────> Slack API     │
│      ├── calendar_event ─────────────────────────> Google Cal    │
│      └── manage_progress ────────────────────────> DynamoDB      │
│                                                                  │
│  LLM Router: Gemini 2.5 Flash Lite (reasoning) + Flash (gen)    │
│  Agent Middleware: turn budget, tool validator, output validator  │
└──────────────────────────────────────────────────────────────────┘

Supporting: DynamoDB (state) | S3 (docs) | Secrets Manager | CloudWatch
Scheduled: Health Check (daily) | Kill Switch (budget SNS)
```

### Lambda functions

There are six. Each has a single job.

| Lambda | Trigger | What it does |
|---|---|---|
| Slack Handler | API Gateway POST | Verifies signature, runs handler middleware, enqueues to SQS |
| Slack OAuth | API Gateway GET | Exchanges auth code for bot token, saves to DynamoDB |
| Google OAuth | API Gateway GET | Exchanges auth code for refresh token, unblocks calendar steps |
| Agent Worker | SQS FIFO | Runs worker middleware, processes the message, runs the orchestrator, replies in Slack |
| Kill Switch | SNS (budget alarm) | Throttles API Gateway to zero, sets DynamoDB flag |
| Health Check | EventBridge (daily 8am) | Pings the Pinecone index so it doesn't get paused for inactivity |

### Inbound middleware chain

Split between handler (fast, CPU-only) and worker (DynamoDB-heavy) to stay within Slack's 3-second timeout.

**Handler middleware** (runs in Slack Handler Lambda):

| # | Middleware | Cost | On failure |
|---|---|---|---|
| 1 | Signature Verification | CPU | Reject (forged request) |
| 2 | EventType Filter | CPU | Drop (unknown event types/subtypes) |
| 3 | Bot Filter | CPU | Drop (prevent self-loops + self-ID) |
| 4 | Empty Filter | CPU | Drop (blank messages, skipped for TEAM_JOIN) |
| 5 | Concurrency Guard | 1 DynamoDB write | "Still working on your previous message..." |

**Worker middleware** (runs in Agent Worker Lambda after SQS dequeue):

| # | Middleware | Cost | On failure |
|---|---|---|---|
| 6 | Input Sanitizer | CPU + conditional write | "I can only help with onboarding questions" (skipped for TEAM_JOIN) |
| 7 | Token Budget Guard | 2 DynamoDB reads | "Daily/monthly limit reached" |

### Agent orchestration

The agent uses a hybrid approach: Plan + ReAct + Tool Calling.

On first interaction, the LLM generates a personalized onboarding plan from intake answers. Each step uses structured tool calls (search KB, send message, assign channel, etc.). When the user says something unexpected, the LLM reasons explicitly before acting. Replanning only touches pending steps; completed steps are frozen.

Two models keep costs low. Gemini 2.5 Flash Lite handles reasoning ("what should I do next?") and Gemini 2.5 Flash handles generation ("write the response"). Reasoning is cheap, generation is where the quality matters.

### Cost protection

Three layers, plus a nuclear option:

```
Layer 3: Workspace monthly cap ($5)     <- protects the AWS bill
  Layer 2: User daily cap (50 turns)    <- one user can't burn through it
    Layer 1: Per-turn budget            <- stops runaway agent loops

  + AWS Budget ($5) + Kill Switch       <- shuts everything down
```

## Tech stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12, AWS Lambda (arm64) |
| Infrastructure | AWS SAM / CloudFormation, GitHub Actions CI/CD |
| Queue | SQS FIFO (per-user ordering, event deduplication) |
| State | DynamoDB (single-table design, TTL policies) |
| LLM | Google Gemini (Flash Lite + Flash) via OpenAI-compatible SDK |
| Vector search | Pinecone (namespaces for multi-tenancy) |
| Storage | S3 (versioned raw HTML archive) |
| Secrets | AWS Secrets Manager (1 consolidated secret) + KMS |
| Monitoring | CloudWatch (logs, metrics, alarms) |
| Slack | slack-sdk, Events API, Block Kit, OAuth2 |
| Calendar | Google Calendar API, OAuth2 |
| Testing | pytest, moto, TDD, 90%+ coverage gate |
| Linting | ruff, mypy, pre-commit hooks |

### What it costs to run

| Component | Cost |
|---|---|
| Lambda, API Gateway, SQS, DynamoDB, S3, CloudWatch, EventBridge, SNS | $0 (free tier) |
| Gemini (Flash Lite + Flash) | ~$0.50 - $2.00 |
| Secrets Manager (1 secret) + KMS | ~$0.50 |
| Pinecone, Google Calendar API, Slack Platform | $0 (free tiers) |
| Total | $1 - $3/month |

If somehow it hits $5/month, AWS Budgets fires an SNS alarm and the Kill Switch Lambda throttles API Gateway to zero.

## Project structure

```
onboard-assist/
├── src/
│   ├── config/
│   │   └── settings.py              # Pydantic Settings, env-based config
│   ├── slack/
│   │   ├── handler.py               # Slack Handler Lambda (events + commands + interactions)
│   │   ├── oauth.py                 # Slack OAuth Lambda
│   │   ├── models.py                # Frozen dataclasses (SlackEvent, SlackCommand, SQSMessage)
│   │   ├── signature.py             # HMAC-SHA256 signature verification
│   │   ├── client.py                # Slack API wrapper
│   │   ├── commands.py              # Slash command handlers
│   │   └── blocks.py                # Block Kit message builders
│   ├── middleware/
│   │   ├── inbound/                 # Handler: EventTypeFilter, BotFilter, EmptyFilter, ConcurrencyGuard
│   │   │                            # Worker: InputSanitizer, TokenBudgetGuard
│   │   └── agent/                   # Per-LLM-call: output validator, tool validator, turn budget
│   ├── agent/
│   │   ├── worker.py                # Agent Worker Lambda
│   │   ├── orchestrator.py          # Plan + ReAct + Tool Calling engine
│   │   ├── tools/                   # search_kb, send_message, assign_channel, calendar_event, manage_progress
│   │   └── prompts/                 # System, planner, and responder prompts
│   ├── rag/
│   │   ├── pipeline.py              # Scrape -> S3 -> chunk -> embed -> Pinecone
│   │   ├── vectorstore.py           # Pinecone client (namespaces per workspace)
│   │   ├── chunker.py               # Document chunking with overlap
│   │   ├── confidence.py            # 4-factor confidence scoring
│   │   ├── scraper.py               # Web scraper (robots.txt compliant)
│   │   └── storage.py               # S3 raw HTML + manifest storage
│   ├── llm/
│   │   ├── provider.py              # LLM provider interface
│   │   ├── gemini.py                # Gemini provider (Flash Lite + Flash)
│   │   ├── router.py                # Model router + cost tracking
│   │   └── fallback.py              # Fallback chain
│   ├── state/
│   │   ├── dynamo.py                # DynamoDB single-table operations
│   │   ├── models.py                # Frozen dataclasses (Plan, Steps, Usage, WorkspaceConfig)
│   │   └── ttl.py                   # TTL policies (60s locks, 90d plans, permanent completions)
│   ├── security/
│   │   └── crypto.py                # KMS field-level encryption (bot tokens)
│   ├── gcal/
│   │   ├── callback.py              # Google OAuth Callback Lambda
│   │   ├── client.py                # Google Calendar API client
│   │   └── oauth.py                 # Google OAuth flow helpers
│   └── admin/
│       ├── kill_switch.py           # Kill Switch Lambda (SNS -> disable API Gateway)
│       ├── kill_switch_check.py     # Kill switch check with local cache
│       ├── health_check.py          # Pinecone health check Lambda (daily cron)
│       └── setup.py                 # Workspace setup state machine
├── tests/
│   ├── unit/                        # Per-module unit tests
│   ├── integration/                 # Mocked AWS integration tests
│   └── conftest.py                  # Shared fixtures
├── infra/
│   └── template.yaml                # SAM template (AWS resources)
├── .github/workflows/
│   ├── ci.yml                       # Orchestrator (calls unit, integration, check, deploy, e2e)
│   ├── ci-unit.yml                  # Unit tests + coverage gate
│   ├── ci-integration.yml           # Integration tests
│   ├── ci-check.yml                 # Lint, format, type check, SAM validate
│   ├── deploy.yml                   # SAM deploy via OIDC
│   └── ci-e2e.yml                   # End-to-end tests (post-deploy)
├── .pre-commit-config.yaml          # ruff, ruff-format, mypy, pytest, sam-validate
├── samconfig.toml
└── pyproject.toml
```

## DynamoDB single-table design

| pk | sk | What it stores | TTL |
|---|---|---|---|
| `WORKSPACE#{id}` | `CONFIG` | Org name, bot_user_id, channel mappings, calendar_enabled | -- |
| `WORKSPACE#{id}` | `SECRETS` | KMS-encrypted bot_token, signing_secret | -- |
| `WORKSPACE#{id}` | `SETUP` | Setup state machine step, admin_user_id | -- |
| `WORKSPACE#{id}` | `PLAN#{user_id}` | Active onboarding plan + conversation context | 90 days |
| `WORKSPACE#{id}` | `COMPLETED#{user_id}` | Completion record (kept forever for audit) | Never |
| `WORKSPACE#{id}` | `USAGE#{user_id}#{date}` | Per-user daily turn count | 7 days |
| `WORKSPACE#{id}` | `USAGE#{yyyy-mm}` | Per-workspace monthly estimated cost | 30 days |
| `WORKSPACE#{id}` | `LOCK#{user_id}` | Processing lock (prevents duplicate work) | 60 seconds |
| `WORKSPACE#{id}` | `OAUTH#GOOGLE#{user_id}` | Google Calendar refresh tokens | 90 days |
| `SYSTEM` | `KILL_SWITCH` | Global kill switch flag | -- |
| `SECURITY` | `INJECTION#{ts}` | Logged injection attempts | 90 days |

## Security

Every request gets its Slack signature verified (HMAC-SHA256). Prompt injection attempts are caught by regex patterns in the Input Sanitizer middleware and logged to DynamoDB; after 3 strikes the bot silently stops responding to that user. On the output side, a validator blocks responses that leak the system prompt or break persona.

Tool calls are validated against an allowlist with parameter constraints and per-turn limits. Each Lambda function has its own least-privilege IAM role. App secrets live in Secrets Manager (1 consolidated secret); per-workspace bot tokens are KMS-encrypted in DynamoDB. DynamoDB is encrypted at rest.

There's no VPC. Every external service (Slack, Pinecone, Google Calendar) talks over HTTPS with API keys or OAuth. Adding a VPC would mean a NAT Gateway at $32/month just so Lambda can reach the internet, which buys nothing here.

## Development

```bash
# Install
pip install -e ".[dev]"

# Run tests (TDD, 90%+ coverage enforced)
pytest

# Lint + format + type check
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/

# Pre-commit (runs all of the above + sam validate)
pre-commit run --all-files

# SAM build + validate
sam build
sam validate --template infra/template.yaml --lint

# Deploy (requires AWS credentials with deploy-policy.json)
sam deploy
```

## CI/CD

GitHub Actions runs on every push and PR. The orchestrator workflow (`ci.yml`) calls four reusable workflows in parallel: unit tests with coverage gate, integration tests, lint/format/type check with SAM validation. Merges to `main` trigger a SAM deploy via OIDC followed by end-to-end tests, but only if you've set the `DEPLOY_ENABLED` repo variable (so it won't surprise you).

## Author

Aditya Mittal - [theadityamittal@gmail.com](mailto:theadityamittal@gmail.com)

## License

MIT
