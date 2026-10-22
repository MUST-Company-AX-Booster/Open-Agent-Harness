---
name: onboard-agent
description: Use when someone wants to set up, configure, onboard, install, run or deploy this Open Agent Harness PM agent (Hermes + Slack) for their own team or project, including filling in .env, describing their team in SOUL.md, creating the Slack app, connecting Telegram, Jira, GitHub, Notion or Jev routing, moving it to a VPS or server, or fixing a setup that won't start. Use it even when they only say "set this up for us", "get it running", or "deploy the agent".
---

# Onboard the agent to a team

You are helping a team run their own copy of this repository: a Hermes agent that answers questions in Slack from
the team's Slack and Telegram history, Jira, Notion and GitHub. Your job is to get them from a fresh clone to a bot
that answers in Slack, changing only what their setup needs.

The repository already holds everything: `docker-compose.yml`, `.env.example`, `hermes/config.yaml`,
`hermes/SOUL.md`, and `README.md`, which has the exact command for every step. Read `README.md` once at the start;
this skill tells you the order, the decisions and the traps.

## How to work

1. **Collect what you need, once.** From the request, work out: model provider, API key and model id; which
   sources (Slack is required; Telegram, Jira, GitHub, Notion, Jev are optional); who may use the bot; team time
   zone; laptop or server; and facts about the team for SOUL.md (product, people and roles, internal words). Ask
   only for what is missing, in one message. A source the user didn't mention stays off.
2. **Write `.env`** from `.env.example` (see "Filling .env").
3. **Describe the team** in the "About the project" block at the top of `hermes/SOUL.md`. Replace the Example
   placeholders with their facts. Delete the GitHub, Notion and request-list lines they don't use. If the team
   writes in a language other than English, add search synonyms in that language to step 2 of
   `hermes/skills/team-history/SKILL.md`.
4. **Configure each chosen integration** and switch off the rest. Read `references/integrations.md` for the
   files each one touches. When you remove an integration, also delete the lines about it in `hermes/SOUL.md`
   and `hermes/skills/team-history/SKILL.md`, so the agent doesn't reach for a tool it no longer has.
5. **Start and verify** (see "Start and verify"). On a server, read `references/deploy-server.md` first.
6. **Report back** with the format in "Final message".

## Filling .env

- Copy `.env.example` to `.env`; never edit `.env.example` with real values. `.env` is git-ignored, and secrets
  must stay out of git, chat messages and Slack. If the user pasted a key in chat, use it, but suggest rotating it
  once setup works.
- Generate the three database passwords yourself with `openssl rand -hex 24`. Hex keeps them URL-safe, which
  matters because they go into connection URLs.
- `LLM_BASE_URL` is always required, OpenAI included (`https://api.openai.com/v1`; OpenRouter is
  `https://openrouter.ai/api/v1`). `AGENT_MODEL` is the provider's exact model id.
- `TEAM_TIMEZONE` is an IANA name such as `Europe/Berlin`. It sets "yesterday" for the nightly summary.
- Access: `SLACK_ALLOWED_USERS` takes Slack member IDs (they start with `U`). Leave `SLACK_ALLOW_ALL_USERS=false`
  unless the user asks for the whole workspace. With both empty, nobody can use the bot; say so if you leave it
  that way.
- Never invent a token. Leave unknown values empty and list them in the final message.

**Placeholder trap.** `docker-compose.yml` marks the model and Slack values as required (`${VAR:?}`), so every
`docker compose` command fails while they are empty, including the one that generates the Slack manifest. Before
the user has real Slack tokens, set `SLACK_BOT_TOKEN=pending` and `SLACK_APP_TOKEN=pending`, generate the manifest,
and replace them before starting the stack. The manifest command also mounts `data/hermes`, so run the first-run
`mkdir`/`touch` line (below) before it.

**Shell variables win.** Docker Compose prefers variables exported in the shell over `.env`. Don't `export` or
`source` `.env` in the user's shell; an exported empty value hides the real one.

## Start and verify

Run in this order from the repo root. Ask before running anything on a server or anything that starts the bot.

```bash
git submodule update --init                                                  # if hermes-agent/ is empty
mkdir -p data/hermes/skills/team-history && touch data/hermes/config.yaml    # first run only
docker compose up -d --build
docker compose ps                         # postgres, pgmcp, jira-mcp (if kept), hermes, collector: all "running"
docker compose logs --tail 50 hermes      # look for the Slack connection and MCP servers connecting
./db/check_permissions.sh                 # every line "ok"
docker compose exec hermes hermes cron create "0 1 * * *" "$(cat hermes/cron/nightly-summaries.md)" \
  --name nightly-summaries --skill team-history
```

The `mkdir`/`touch` line exists because Docker Desktop cannot bind-mount a file whose host path is missing.

Reloading after a change:

| Changed | Run |
|---|---|
| `.env` | `docker compose up -d` (recreates containers whose values changed; `restart` keeps the old values) |
| `hermes/config.yaml`, `SOUL.md`, skills, plugins | `docker compose restart hermes` (`up -d` alone does not reload mounted files) |
| `docker-compose.yml` | `docker compose up -d` |

Finish with a live test: the user invites the bot (`/invite @PM-Agent`) and asks it something in a channel. The
history is empty until the collector has run (Slack every 5 minutes, Telegram backfills on start), so a first
answer of "nothing found" is expected. Check `docker compose logs collector` shows messages being saved.

If a container keeps restarting, read its logs before changing anything. Common causes: an empty required
variable, a wrong `LLM_BASE_URL`, a model id the provider doesn't know, or `data/postgres` left over from an
earlier run with different passwords (the init script runs only on an empty `data/postgres`).

## Steps only the user can do

Some steps need the user's browser, phone or accounts. Give them the exact command or click path from
`README.md` and wait:

- creating and installing the Slack app, and copying its two tokens
- the Telegram login (`collector.login`) asks for a phone code
- the GitHub device-flow approval and the Notion OAuth login
- anything on a server you have no shell on

## Final message

End with these parts, in this order:

1. **Status:** one line saying what runs now and what is still off.
2. **What I changed:** each file and the change, one line each. Mention generated secrets only by name.
3. **Your next steps:** numbered, with exact commands or click paths, starting with anything blocking.
4. **Missing values:** each empty `.env` key you still need from them.
5. **Check it works:** the live test above.
