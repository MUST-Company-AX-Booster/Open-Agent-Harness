# Open Agent Harness

A self-hosted PM agent for your team, built on [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Ask it a question in Slack and it answers from your team's Slack and Telegram history, Jira, Notion and GitHub,
with a source for every fact.

It began as the PM agent for one production team. This repository is that same system with the team-specific
parts taken out, so any team can run its own copy. You configure it through two files: `.env` for keys and
`hermes/SOUL.md` for what the agent should know about your team.

![How the assistant connects the team](docs/architecture.png)

## What it can do

- Answer questions about past discussions: what was decided, who owns a task, what is still open.
- Read Slack and Telegram history, Jira issues, Notion pages and GitHub repositories, and cite each source.
- Write a nightly summary of every active channel, then read those summaries first when answering.
- Create and update Jira issues after the person asking confirms.
- Send long answers as `.md` or `.zip` files in Slack.
- Pick a cheaper or stronger model for each message (optional Jev router).

## Architecture

Everything runs with Docker Compose on one machine.

| Component | Role |
|---|---|
| **Hermes** (`hermes`) | The assistant. Answers in Slack over Socket Mode, runs scheduled jobs and serves the optional web dashboard. |
| **Message collector** (`collector`) | Saves Slack messages every 5 minutes and Telegram messages as they arrive. |
| **PostgreSQL + PGroonga** (`postgres`) | Stores messages and nightly summaries. PGroonga gives full-text search in English, Korean, Japanese and Chinese. |
| **History connector** (`pgmcp`) | Postgres MCP server. Hermes reads history through a database user that can only write to `summaries`. |
| **Jira connector** (`jira-mcp`) | Jira MCP server, limited to reading, creating, updating and commenting. |
| **GitHub, Notion** | Hosted MCP servers, read-only. Off by default. |
| **Jev router** | Optional [TypeSafe Jev](https://docs.typesafe.ai) plugin that chooses the model and reasoning effort per message. |

The model can be any OpenAI-compatible API that supports tool calling. The diagram shows the original
deployment, which used NeuralWatt.

## Quick start

**Using Claude Code?** Open the cloned repository in Claude Code and run `/onboard-agent`. The bundled skill
(`.claude/skills/onboard-agent/`) asks for what it needs, fills in `.env` and `hermes/SOUL.md`, sets up the
integrations you choose, and walks you through starting the stack or deploying it to a server. The manual steps
are below.

**Requirements:** Docker, git, a Slack workspace where you can install apps, and an API key for a model provider.

```bash
# 1. Clone with the Hermes submodule
git clone --recurse-submodules https://github.com/MUST-Company-AX-Booster/Open-Agent-Harness.git
cd Open-Agent-Harness

# 2. Add your keys (model, database passwords, Slack)
cp .env.example .env

# 3. Describe your team in the "About the project" block of hermes/SOUL.md

# 4. Start the stack
mkdir -p data/hermes/skills/team-history && touch data/hermes/config.yaml   # first run only
docker compose up -d --build
docker compose ps

# 5. Schedule the nightly summary (01:00 in TEAM_TIMEZONE)
docker compose exec hermes hermes cron create "0 1 * * *" "$(cat hermes/cron/nightly-summaries.md)" \
  --name nightly-summaries --skill team-history
```

Invite the bot to a channel with `/invite @PM-Agent` and mention it with a question.

The `mkdir` and `touch` line is needed once because Docker Desktop cannot bind-mount a file whose host path
does not exist.

## Configuration

| Integration | Required | Where |
|---|---|---|
| Model | Yes | `.env`: `LLM_API_KEY`, `LLM_BASE_URL`, `AGENT_MODEL` |
| Database | Yes | `.env`: three passwords (`openssl rand -hex 24`) |
| Slack | Yes | `.env`: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, allowed users |
| Telegram | No | `.env`: `TELEGRAM_*` |
| Jira | No | `.env`: `JIRA_*` |
| GitHub | No | `.env`: `GITHUB_OAUTH_TOKEN`, plus `enabled: true` in `hermes/config.yaml` |
| Notion | No | `enabled: true` in `hermes/config.yaml`, then a one-time login |
| Jev router | No | `.env`: `TYPESAFE_API_KEY`, plus a model ladder in `hermes/config.yaml` |
| Web dashboard | No | `.env`: `HERMES_DASHBOARD_*` |

### Model

Set `LLM_BASE_URL` to your provider's OpenAI-compatible endpoint, for example `https://api.openai.com/v1` or
`https://openrouter.ai/api/v1`. Each answer takes several search and read steps, so choose a model that is good
at tool calling. Reasoning effort is `agent.reasoning_effort` in `hermes/config.yaml` (default `high`). To fall
back to a second model when the first fails, add it under `fallback_providers`.

If your team writes in a language other than English, test the model in that language first. Some models that
call tools well in English return empty answers in other languages.

### Slack

Hermes connects over Socket Mode, so the server needs no public URL.

1. Generate an app manifest. Docker Compose refuses to run while required `.env` values are empty, so until you
   have real tokens set `SLACK_BOT_TOKEN=pending` and `SLACK_APP_TOKEN=pending`. This does not start the bot:
   ```bash
   docker compose run --rm --no-deps --entrypoint /opt/hermes/.venv/bin/hermes hermes \
     slack manifest --name PM-Agent --agent-view
   ```
2. At api.slack.com/apps choose **Create New App → From an app manifest** and paste it.
3. Add the bot scope `channels:join`, so the bot can read a link from a public channel it hasn't joined, and the
   bot event `agent_session_stopped`, so Slack's Stop button works. Install the app.
4. Put the bot token (`xoxb-…`) in `SLACK_BOT_TOKEN`. Create an app-level token with `connections:write` and put
   it (`xapp-…`) in `SLACK_APP_TOKEN`.
5. Set `SLACK_ALLOWED_USERS` to a list of member IDs, or `SLACK_ALLOW_ALL_USERS=true`.

Run one copy of the stack per Slack app. Slack splits events between Socket Mode connections, so two copies
would each miss messages. In channels the bot answers only when mentioned; it always answers direct messages.

### Telegram

The collector reads groups as a regular user account, so it sees what a member sees.

```bash
# Put api_id and api_hash from https://my.telegram.org in .env first
docker compose run --rm collector python -m collector.login          # prints TELEGRAM_SESSION_STRING
docker compose run --rm collector python -m collector.login --list   # lists group IDs for TELEGRAM_GROUPS
docker compose up -d collector
```

The session string is a full login to that account. Store it like a password and never use one session in two
running programs.

### Jira

Set `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (create one at
id.atlassian.com/manage-profile/security/api-tokens) and `JIRA_PROJECT`. The agent acts as that account and sees
only that project. It asks for confirmation before it creates or changes an issue, and it cannot delete, move or
transition issues (see `ENABLED_TOOLS` in `docker-compose.yml`).

If you don't use Jira, remove the `jira-mcp` service and its entry in `hermes`' `depends_on` from
`docker-compose.yml`, and the `jira:` block under `mcp_servers` in `hermes/config.yaml`.

### GitHub

1. Create a GitHub OAuth App with **Enable Device Flow** ticked and put its client ID in `GITHUB_OAUTH_CLIENT_ID`.
2. Request a token. The scope `read:org read:user` sees public repositories; private ones need `repo`.
   ```bash
   curl -s -X POST https://github.com/login/device/code -H 'Accept: application/json' \
     -d "client_id=$GITHUB_OAUTH_CLIENT_ID&scope=read:org read:user"
   # Open https://github.com/login/device, enter the user_code and approve. Then:
   curl -s -X POST https://github.com/login/oauth/access_token -H 'Accept: application/json' \
     -d "client_id=$GITHUB_OAUTH_CLIENT_ID&device_code=<device_code>&grant_type=urn:ietf:params:oauth:grant-type:device_code"
   ```
3. Put the `access_token` in `GITHUB_OAUTH_TOKEN`, set `enabled: true` under `mcp_servers.github`, name your
   organization in `hermes/SOUL.md`, and run `docker compose up -d` (a plain `restart` keeps the old `.env`
   values).

The token does not expire and acts as the account that approved it. The `X-MCP-Readonly` header keeps every
GitHub tool read-only.

### Notion

Set `enabled: true` under `mcp_servers.notion`, then log in once. The token is saved in `data/hermes/mcp-tokens/`.

```bash
docker compose exec -it -u hermes hermes hermes mcp login notion
docker compose restart hermes
```

Open the printed URL and approve. Your browser then lands on `http://127.0.0.1:<port>/callback?code=…` and shows
an error page; copy that full URL back into the terminal. The agent can see everything the logged-in Notion
account can see, so use an account limited to your project's pages. Name your start page in `hermes/SOUL.md`.

**Request list (optional).** A scheduled job reads Telegram and keeps a Notion database of team requests up to
date (`hermes/cron/task-list.md`). Create a database with the properties Name, Requester, Group, Priority
(High/Medium/Low), Status (Open/In progress/Done), Requested at (date), Last update (date), Key and Evidence.
Replace every `<...>` in the prompt, name the database in `hermes/SOUL.md`, then schedule it:

```bash
docker compose exec hermes hermes cron create "0 9,23 * * *" "$(cat hermes/cron/task-list.md)" --name task-list
```

### Jev router

Both Jev plugins stay off until `TYPESAFE_API_KEY` is set.

- **`pm_router`** chooses the model and reasoning effort for each Slack message, from a cheap model for "thanks"
  to a strong one for sprint planning. Uncomment the seven rungs under `plugins.entries.pm_router.settings.rungs`
  in `hermes/config.yaml` and fill in models your provider offers. Routing stays off until all seven are valid.
  With `mode: shadow` it only logs its choices to `data/hermes/logs/pm_router.jsonl`.
- **`jev-skill-router`** tells the agent which skills fit the message.

Both send the message text to TypeSafe, and `pm_router` also sends up to 2,000 characters of the thread.

### Web dashboard

Set the `HERMES_DASHBOARD_*` values to turn it on. It listens on `127.0.0.1:9119`; to reach it from another
machine, put a reverse proxy with HTTPS (for example Caddy) in front of it.

## Customizing the agent

| File | What it controls |
|---|---|
| `hermes/SOUL.md` | The agent's instructions: your project, people and terms, answer format, safety rules. |
| `hermes/skills/team-history/SKILL.md` | How it searches history. Add synonyms in your team's languages. |
| `hermes/config.yaml` | Model, tools per platform, MCP servers and plugins. |
| `hermes/cron/*.md` | Prompts for scheduled jobs. |
| `hermes/plugins/` | `pm_slack` (live Slack reads, status line, Stop button), `pm_export` (file answers), `pm_router`, `jev-skill-router`. |

After editing these files, run `docker compose restart hermes`; `docker compose up -d` does not reload them. After
changing `.env`, run `docker compose up -d` instead; `restart` keeps the old values.

## Security

- On Slack the agent has no shell, code execution or file access, and it cannot search other people's chats.
- Hermes reads the database as a user that can read messages but write only to `summaries`. Run
  `./db/check_permissions.sh` to verify.
- Each container receives only the secrets it needs. The Telegram session and database passwords never reach
  the `hermes` container.
- The agent treats text it reads from Slack, Notion, GitHub or the web as data and ignores instructions inside it.
- `.env` and `data/` are git-ignored. Never commit them.

## Operations

| Task | Command |
|---|---|
| Follow logs | `docker compose logs -f hermes` (or `collector`) |
| List or run scheduled jobs | `docker compose exec hermes hermes cron list` / `cron run <id>` |
| Open a read-only database shell | `docker compose exec postgres psql -U hermes -d pm` |
| Check database permissions | `./db/check_permissions.sh` |
| Run eval questions | `python3 evals/run.py` (add your own to `evals/cases.yaml`) |

**Backup**

```bash
docker compose exec -T postgres pg_dump -U postgres pm | gzip > backup-$(date +%F).sql.gz
tar czf hermes-home-$(date +%F).tgz data/hermes      # scheduled jobs, sessions, logins
```

**Moving to another server.** Dump the database with `--data-only` and stop the old stack, since only one copy
may use the Slack app and the Telegram session. Copy `.env` and `data/hermes`, but not `data/postgres`, which
depends on the CPU architecture. Start `postgres` on the new machine, load the dump, then run
`docker compose up -d --build`. On Linux, run `sudo chown -R 10000:10000 data/hermes` first, because Hermes runs
as UID 10000.

**Updating Hermes**

```bash
cd hermes-agent && git fetch --tags && git checkout <new-tag> && cd ..
docker compose build hermes && docker compose up -d hermes
```

The plugins patch a few Hermes internals, so run the plugin tests after every update.

## Tests

```bash
docker compose run --rm --no-deps collector pytest -q
cd hermes/plugins && for t in test_pm_*.py; do python3 -m unittest -q "$t"; done && cd ../..
python3 hermes/plugins/jev-skill-router/tests/test_offline.py
```

## Credits and license

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research (MIT), included as a git submodule.
- `hermes/plugins/jev-skill-router` by DoGMaTiiC (MIT), vendored with small changes.
- All other code is released under the MIT License. See [LICENSE](LICENSE).
