<div align="center">

<img src="docs/readme-banner.png" alt="Open Agent Harness — Team project assistant, with six interlocking mascot pieces" width="100%">

<!-- # Open Agent Harness -->

**Any project. Any domain. Start with Open Agent.**

A self-hosted Slack assistant that searches your team's chats, issues, docs, and code.
Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Deployment: Docker Compose](https://img.shields.io/badge/Deployment-Docker_Compose-2496ED?logo=docker&logoColor=white)](#quick-start)
[![Documentation](https://img.shields.io/badge/Documentation-Read_the_docs-526CFE)](#configuration)
[![Quick Start](https://img.shields.io/badge/Quick_Start-Get_started-2EA44F)](#quick-start)

[Integrations](#configuration) · [Architecture](#architecture) · [Security](#security) · [Contributing](#contributing)

</div>

Ask in Slack. Open Agent answers from Slack and Telegram history, plus Jira, GitHub, and Notion if connected.
It cites sources and tells proposals apart from decisions.

https://github.com/user-attachments/assets/95bac865-b353-4ef2-b027-3383b1c467c8

## Quick start

Install the setup skill into your coding agent (Claude Code, Codex, Cursor, Gemini CLI and
[others](https://github.com/vercel-labs/skills)):

```bash
npx skills add MUST-Company-AX-Booster/Open-Agent-Harness --skill open-agent-setup
```

Then ask it: `Set up Open Agent for my team.`

The agent fills in `.env` and `hermes/SOUL.md`, configures the integrations you pick, and starts the stack. It
pauses for steps that need your browser or phone. No Node.js? Tell your agent to follow
[`skills/open-agent-setup/SKILL.md`](skills/open-agent-setup/SKILL.md).

<details>
<summary><strong>Manual setup</strong></summary>

You need Git, Docker with Compose, a Slack app you can install, and an API key for a tool-calling model.

**1. Clone and configure**

```bash
git clone --recurse-submodules https://github.com/MUST-Company-AX-Booster/Open-Agent-Harness.git
cd Open-Agent-Harness
cp .env.example .env
```

Fill in the required values from [Configuration](#configuration), plus `TEAM_TIMEZONE` (default `UTC`). Describe
your project, team, and terms under **About the project** in [`hermes/SOUL.md`](hermes/SOUL.md). Not using Jira?
Follow the [removal steps](#jira) first.

**2. Start**

```bash
mkdir -p data/hermes/skills/team-history
touch data/hermes/config.yaml   # Docker Desktop needs the mount target to exist
docker compose up -d --build
docker compose ps
```

**3. Ask a question.** Run `/invite @Open-Agent` in a channel, then:

```text
@Open-Agent What has this channel discussed recently? Include your sources.
```

Slack is collected every five minutes, so new messages show up late. Errors: `docker compose logs -f hermes collector`.

**4. Nightly summaries** (01:00 in `TEAM_TIMEZONE`)

```bash
docker compose exec hermes hermes cron create "0 1 * * *" "$(cat hermes/cron/nightly-summaries.md)" \
  --name nightly-summaries --skill team-history
```

</details>

## What you can ask

| Ask in Slack | Where it looks |
|---|---|
| “What did we decide about the onboarding flow last week?” | Discussions and dates: what was proposed, what was agreed. |
| “Who owns this bug, and what is blocking it?” | Jira status, assignee, related chats. |
| “What shipped this week?” | GitHub changes, Jira issues, team updates. |
| “Turn this discussion into a Jira issue.” | A draft you confirm before it writes. |
| “Give me the full report as a Markdown file.” | A file attached to the reply. |

It can only answer from sources it can reach.

## Meet Open Agent

Six mascots for the assistant's roles. Branding only, not separate agents.

| Fox | Owl | Border collie | Elephant | Otter | Raven |
|:---:|:---:|:---:|:---:|:---:|:---:|
| [![Fox logo: plum and ivory curled fox on an orange background](docs/icon-concepts/32-fox-curled.png)](docs/icon-concepts/32-fox-curled.png) | [![Owl logo: navy owl on a golden background](docs/icon-concepts/34-animal-owl.png)](docs/icon-concepts/34-animal-owl.png) | [![Border collie logo: navy dog on a blue background](docs/icon-concepts/35-animal-collie.png)](docs/icon-concepts/35-animal-collie.png) | [![Elephant logo: plum elephant on an apricot background](docs/icon-concepts/36-animal-elephant.png)](docs/icon-concepts/36-animal-elephant.png) | [![Otter logo: forest-green otter on a mint background](docs/icon-concepts/37-animal-otter.png)](docs/icon-concepts/37-animal-otter.png) | [![Raven logo: plum raven on a periwinkle background](docs/icon-concepts/38-animal-raven.png)](docs/icon-concepts/38-animal-raven.png) |
| Solve problems | Find knowledge | Coordinate work | Remember decisions | Help the team | Connect the clues |

## Architecture

![Architecture: team sources, Hermes, and the history database](docs/architecture.png)

| Service | Role |
|---|---|
| **Hermes** (`hermes`) | Slack gateway (Socket Mode), agent, scheduled jobs, optional dashboard. |
| **Collector** (`collector`) | Polls Slack every 5 minutes, streams Telegram, stores both in PostgreSQL. |
| **PostgreSQL + PGroonga** (`postgres`) | Messages and summaries, full-text search in English, Korean, Japanese, Chinese. |
| **History connector** (`teamdb`) | Reads history; can write only to `summaries`. |
| **Jira connector** (`jira`) | Read, create, update, comment. No delete, move, or transition. |
| **GitHub, Notion** | Optional hosted connectors. GitHub is read-only. |
| **Jev router** | Optional per-message model and skill selection via TypeSafe. |

The agent reads summaries first, then searches messages for detail. Any OpenAI-compatible model works; services
are in [`docker-compose.yml`](docker-compose.yml).

## Configuration

| Integration | Setup | Configuration |
|---|---|---|
| <img src="docs/integration-icons/model.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Model](#model)** | **Required** | `.env`<br>`LLM_API_KEY`, `LLM_BASE_URL`, `AGENT_MODEL` |
| <img src="docs/integration-icons/database.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **Database** | **Required** | `.env`<br>`POSTGRES_PASSWORD`, `COLLECTOR_DB_PASSWORD`, `HERMES_DB_PASSWORD` (`openssl rand -hex 24` each) |
| <img src="docs/integration-icons/slack.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Slack](#slack)** | **Required** | `.env`<br>`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALLOWED_USERS` |
| <img src="docs/integration-icons/telegram.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Telegram](#telegram)** | Optional | `.env`<br>`TELEGRAM_*` |
| <img src="docs/integration-icons/jira.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Jira](#jira)** | Optional | `.env`<br>`JIRA_*` |
| <img src="docs/integration-icons/github.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[GitHub](#github)** | Optional | `.env`<br>`GITHUB_OAUTH_TOKEN`, plus `enabled: true` in `hermes/config.yaml` |
| <img src="docs/integration-icons/notion.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Notion](#notion)** | Optional | `enabled: true` in `hermes/config.yaml`, then a one-time login |
| <img src="docs/integration-icons/jev-router.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Jev router](#jev-router)** | Optional | `.env`<br>`TYPESAFE_API_KEY`, plus a model ladder in `hermes/config.yaml` |
| <img src="docs/integration-icons/web-dashboard.svg" width="24" height="24" align="absmiddle" alt="">&nbsp; **[Web dashboard](#web-dashboard)** | Optional | `.env`<br>`HERMES_DASHBOARD_*` |

<a id="model"></a>
<details>
<summary><img src="docs/integration-icons/model.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Model</strong></summary>

Set `LLM_BASE_URL` to an OpenAI-compatible endpoint, such as `https://api.openai.com/v1` or
`https://openrouter.ai/api/v1`. Pick a model that handles several tool calls in a row. Set
`agent.reasoning_effort` in `hermes/config.yaml` (default `high`) and add `fallback_providers` if you want a backup.

Test in your team's language. Tool calling that works in English can fail in others.

</details>

<a id="slack"></a>
<details>
<summary><img src="docs/integration-icons/slack.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Slack</strong></summary>

Socket Mode, so no public URL needed.

1. Generate a manifest. Compose won't run with empty required values, so set `SLACK_BOT_TOKEN=pending` and
   `SLACK_APP_TOKEN=pending` for now:
   ```bash
   docker compose run --rm --no-deps --entrypoint /opt/hermes/.venv/bin/hermes hermes \
     slack manifest --name Open-Agent --agent-view
   ```
2. At api.slack.com/apps: **Create New App → From an app manifest**, paste it.
3. Add bot scope `channels:join` (read links from unjoined public channels) and bot event `agent_session_stopped`
   (Stop button). Install the app.
4. Bot token (`xoxb-…`) → `SLACK_BOT_TOKEN`. App-level token with `connections:write` (`xapp-…`) → `SLACK_APP_TOKEN`.
5. Set `SLACK_ALLOWED_USERS` to member IDs, or `SLACK_ALLOW_ALL_USERS=true`.

Run one stack per Slack app; Socket Mode splits events between connections, so two stacks miss messages.

</details>

<a id="telegram"></a>
<details>
<summary><img src="docs/integration-icons/telegram.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Telegram</strong></summary>

The collector logs in as a regular user and sees what a member sees.

```bash
# Put api_id and api_hash from https://my.telegram.org in .env first
docker compose run --rm collector python -m collector.login          # prints TELEGRAM_SESSION_STRING
docker compose run --rm collector python -m collector.login --list   # lists group IDs for TELEGRAM_GROUPS
docker compose up -d collector
```

The session string is a full login. Treat it like a password and never run it in two programs.

</details>

<a id="jira"></a>
<details>
<summary><img src="docs/integration-icons/jira.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Jira</strong></summary>

Set `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (from id.atlassian.com/manage-profile/security/api-tokens)
and `JIRA_PROJECT`. The agent acts as that account, sees only that project, and asks before any write. Allowed
tools are in `ENABLED_TOOLS` in `docker-compose.yml`.

**No Jira?** Remove the `jira` service and its `depends_on` entry under `hermes` in `docker-compose.yml`, and the
`jira:` block under `mcp_servers` in `hermes/config.yaml`.

</details>

<a id="github"></a>
<details>
<summary><img src="docs/integration-icons/github.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>GitHub</strong></summary>

1. Create a GitHub OAuth App with **Enable Device Flow** ticked. Client ID → `GITHUB_OAUTH_CLIENT_ID`.
2. Get a token. `read:org read:user` covers public repos; private ones need `repo`.
   ```bash
   curl -s -X POST https://github.com/login/device/code -H 'Accept: application/json' \
     -d "client_id=$GITHUB_OAUTH_CLIENT_ID&scope=read:org read:user"
   # Open https://github.com/login/device, enter the user_code and approve. Then:
   curl -s -X POST https://github.com/login/oauth/access_token -H 'Accept: application/json' \
     -d "client_id=$GITHUB_OAUTH_CLIENT_ID&device_code=<device_code>&grant_type=urn:ietf:params:oauth:grant-type:device_code"
   ```
3. `access_token` → `GITHUB_OAUTH_TOKEN`. Set `enabled: true` under `mcp_servers.github`, name your org in
   `hermes/SOUL.md`, and run `docker compose up -d` (`restart` keeps old `.env` values).

The token has the approver's permissions; revoke it when done. The `X-MCP-Readonly` header keeps it read-only.

</details>

<a id="notion"></a>
<details>
<summary><img src="docs/integration-icons/notion.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Notion</strong></summary>

Set `enabled: true` under `mcp_servers.notion`, then log in once (token saved in `data/hermes/mcp-tokens/`):

```bash
docker compose exec -it -u hermes hermes hermes mcp login notion
docker compose restart hermes
```

Approve the printed URL. The browser lands on an error page at `http://127.0.0.1:<port>/callback?code=…`; paste
that full URL into the terminal. The agent sees everything that account sees, so use one limited to your
project. Name your start page in `hermes/SOUL.md`.

**Request list (optional).** A job reads Telegram and keeps a Notion database of team requests
(`hermes/cron/task-list.md`). Create a database with Name, Requester, Group, Priority (High/Medium/Low), Status
(Open/In progress/Done), Requested at (date), Last update (date), Key and Evidence. Replace every `<...>` in the
prompt, name the database in `hermes/SOUL.md`, then:

```bash
docker compose exec hermes hermes cron create "0 9,23 * * *" "$(cat hermes/cron/task-list.md)" --name task-list
```

</details>

<a id="jev-router"></a>
<details>
<summary><img src="docs/integration-icons/jev-router.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Jev router</strong></summary>

Off until you set `TYPESAFE_API_KEY`.

- **`oa_router`** picks a model and reasoning effort per Slack message. Uncomment the seven rungs under
  `plugins.entries.oa_router.settings.rungs` in `hermes/config.yaml` and fill in your models. It stays off until
  all seven are valid. `mode: shadow` only logs to `data/hermes/logs/oa_router.jsonl`.
- **`jev-skill-router`** suggests skills for the message.

Both send the message to TypeSafe; `oa_router` also sends up to 2,000 characters of the thread.

</details>

<a id="web-dashboard"></a>
<details>
<summary><img src="docs/integration-icons/web-dashboard.svg" width="22" height="22" align="absmiddle" alt="">&nbsp; <strong>Web dashboard</strong></summary>

Set `HERMES_DASHBOARD_*`. It listens on `127.0.0.1:9119`; use an HTTPS reverse proxy (such as Caddy) for remote access.

</details>

## Customizing the agent

| File | Controls |
|---|---|
| `hermes/SOUL.md` | Instructions: project, people, terms, answer format, safety rules. |
| `hermes/skills/team-history/SKILL.md` | How it searches history. Add synonyms in your languages. |
| `hermes/config.yaml` | Model, tools, connectors, plugins. |
| `hermes/cron/*.md` | Scheduled job prompts. |
| `hermes/plugins/` | `oa_slack` (live reads, status, Stop), `oa_export` (file answers), `oa_router`, `jev-skill-router`. |

Changed these files? `docker compose restart hermes`. Changed `.env`? `docker compose up -d` (`restart` keeps old values).

## Security

- In Slack, shell, code execution, file access, memory, and session search are off. Plugins can read channel
  messages and write report files.
- Hermes's database user reads messages and writes only to `summaries`. Check with `./db/check_permissions.sh`.
- Each container gets only its own secrets. The Telegram session and database passwords never reach `hermes`.
- The agent is told to ignore instructions inside retrieved content. That is an instruction, not a guarantee
  against prompt injection.
- Every allowed Slack user can reach all collected history. Per-user source permissions are not enforced, so
  connect only sources fit for everyone you allow.
- Relevant content goes to your model provider and hosted connectors; Jev routing also sends message text to TypeSafe.
- `.env` and `data/` are git-ignored. Never commit them.

## Operations

| Task | Command |
|---|---|
| Logs | `docker compose logs -f hermes` (or `collector`) |
| Scheduled jobs | `docker compose exec hermes hermes cron list` / `cron run <id>` |
| Database shell | `docker compose exec postgres psql -U hermes -d openagent` |
| Check DB permissions | `./db/check_permissions.sh` |
| Evals | `python3 evals/run.py` (add cases to `evals/cases.yaml`) |

**Backup**

```bash
docker compose exec -T postgres pg_dump -U postgres openagent | gzip > backup-$(date +%F).sql.gz
tar czf hermes-home-$(date +%F).tgz data/hermes      # scheduled jobs, sessions, logins
```

<details>
<summary><strong>Moving servers, upgrading from pm-hermes, updating Hermes</strong></summary>

**Moving to another server.** Dump with `--data-only` and stop the old stack (only one copy may use the Slack app
and Telegram session). Copy `.env` and `data/hermes`, not `data/postgres` (CPU-specific). Start `postgres` on the
new machine, load the dump, run `docker compose up -d --build`. On Linux, first run
`sudo chown -R 10000:10000 data/hermes` (Hermes runs as UID 10000).

**Upgrading from pm-hermes.** Older versions used compose project `pm-hermes` and database `pm`. Run this once
after pulling, before any other `docker compose` command, or a second stack starts on the same data and Slack app:

```bash
docker compose -p pm-hermes down
docker compose up -d postgres
docker compose exec postgres psql -U postgres -d postgres -c 'ALTER DATABASE pm RENAME TO openagent'
docker compose up -d --build
```

**Updating Hermes.** The plugins patch Hermes internals, so run the [tests](#tests) after every update.

```bash
cd hermes-agent && git fetch --tags && git checkout <new-tag> && cd ..
docker compose build hermes && docker compose up -d hermes
```

</details>

## Tests

```bash
docker compose run --rm --no-deps collector pytest -q
cd hermes/plugins && for t in test_oa_*.py; do python3 -m unittest -q "$t"; done && cd ../..
python3 hermes/plugins/jev-skill-router/tests/test_offline.py
```

## Repository guide

| Path | Purpose |
|---|---|
| [`collector/`](collector/) | Slack and Telegram collection, with tests. |
| [`db/`](db/) | Database init and permission checks. |
| [`hermes/`](hermes/) | Instructions, config, plugins, skills, cron prompts. |
| [`hermes-agent/`](hermes-agent/) | Upstream Hermes, pinned submodule. |
| [`evals/`](evals/) | Answer-quality questions. |
| [`skills/open-agent-setup/`](skills/open-agent-setup/) | Setup skill for coding agents. |

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report vulnerabilities
privately per [SECURITY.md](SECURITY.md). [GOVERNANCE.md](GOVERNANCE.md) covers maintainers, response times, and
what we promise never to close.

## Credits and license

- Built by Abhijit Rao ([@must-aero](https://github.com/must-aero)) and Yashal Nawid
  ([@must-zaffreblue](https://github.com/must-zaffreblue)) at MUST Company.
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (MIT), git submodule.
- `hermes/plugins/jev-skill-router` by DoGMaTiiC (MIT), vendored with small changes.
- Everything else: MIT, see [LICENSE](LICENSE).
