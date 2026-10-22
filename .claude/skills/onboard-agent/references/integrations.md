# Integrations: what to change for each

Exact user-facing commands are in `README.md` under "Configuration". This file lists the files each integration
touches and the mistakes to avoid. Reload as in SKILL.md: `docker compose up -d` after `.env` changes,
`docker compose restart hermes` after `hermes/` file changes.

## Slack (required)

- `.env`: `SLACK_BOT_TOKEN` (`xoxb-…`), `SLACK_APP_TOKEN` (`xapp-…`, app-level token with `connections:write`),
  plus `SLACK_ALLOWED_USERS` or `SLACK_ALLOW_ALL_USERS`.
- New app: generate the manifest with the command in README "Slack". It runs a one-off container and needs the
  required `.env` values present (placeholders are fine, see the placeholder trap in SKILL.md). After pasting the
  manifest, the user must add the bot scope `channels:join` and the bot event `agent_session_stopped`, then
  install the app.
- One stack per Slack app. Two stacks on one app split Slack's events and each misses messages, so stop a laptop
  copy before starting a server copy.

## Telegram (optional)

- `.env`: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (from my.telegram.org), `TELEGRAM_SESSION_STRING`,
  `TELEGRAM_GROUPS` (comma-separated chat IDs, usually negative numbers starting with -100).
- The user runs `docker compose run --rm collector python -m collector.login` (phone code prompt), pastes the
  session string into `.env` themselves, then `... collector.login --list` for group IDs.
- Tell them the session string is a full login to that Telegram account. One session must not run in two
  programs at once.
- Not using Telegram: leave the four values empty. The collector skips Telegram and still polls Slack.

## Jira (optional)

- `.env`: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT` (project key). The agent acts as that
  account and only sees that project.
- Not using Jira: remove it in three places, or `jira-mcp` crash-loops and the agent advertises tools that fail:
  1. the whole `jira-mcp:` service in `docker-compose.yml`
  2. `jira-mcp` from the `hermes` service's `depends_on` (leaving `depends_on: [pgmcp]`)
  3. the `jira:` block under `mcp_servers` in `hermes/config.yaml`

  Then drop the Jira rule at the end of `hermes/skills/team-history/SKILL.md` and Jira from the sources list in
  `hermes/SOUL.md`.

## GitHub (optional, read-only)

- `hermes/config.yaml`: set `enabled: true` under `mcp_servers.github`.
- `.env`: `GITHUB_OAUTH_CLIENT_ID` (the user's OAuth App, "Enable Device Flow" ticked) and `GITHUB_OAUTH_TOKEN`
  from the device flow in README "GitHub". Scope `read:org read:user` covers public repos; private repos need
  `repo`. The user approves in their browser, so leave the token empty until they have it.
- `hermes/SOUL.md`: name the org in the About block (`org:<name>`).

## Notion (optional)

- `hermes/config.yaml`: set `enabled: true` under `mcp_servers.notion`.
- Login is interactive and done by the user: `docker compose exec -it -u hermes hermes hermes mcp login notion`,
  then restart hermes. The browser lands on a localhost error page; they paste that full URL back.
- `hermes/SOUL.md`: name the start page. The agent sees everything the logged-in account sees, so recommend an
  account limited to the project's pages.
- Request list job (optional): fill every `<...>` in `hermes/cron/task-list.md`, name the database in SOUL.md,
  then schedule it with the command in README.

## Jev router (optional)

- `.env`: `TYPESAFE_API_KEY`.
- `hermes/config.yaml`, under `plugins.entries.pm_router.settings`: uncomment `rungs:` and set all seven, cheapest
  first: `quick`, `lookup`, `reasoned`, `routine`, `multi_step`, `deep`, `hardest`. Each needs `model` (the
  provider's exact id) and `effort` (one of none, minimal, low, medium, high, xhigh, max) that this model
  accepts. Add `provider:` only for a model on a different endpoint than the main one. With a missing rung,
  an unknown effort or no key, routing stays off and a warning goes to the hermes log.
- Only use models the user named. With two models, the cheap one usually covers quick to reasoned and the strong
  one covers routine to hardest, with effort rising inside each.
- `mode: apply` routes for real; `mode: shadow` only logs choices to `data/hermes/logs/pm_router.jsonl`, which is
  a safe first week.
- Tell the user both Jev plugins send the message text to TypeSafe, and pm_router also sends up to 2,000
  characters of the thread.

## Web dashboard (optional)

- `.env`: `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`, `..._PASSWORD`, `..._SECRET` (`openssl rand -hex 32`),
  `HERMES_DASHBOARD_PUBLIC_URL`. No username keeps it off.
- It listens on `127.0.0.1:9119` only. To reach it from outside, put a reverse proxy with HTTPS in front; never
  change the port binding to `0.0.0.0`.
