# Deploying to a server

Any Linux machine with Docker works. The stack needs no inbound ports: Slack uses Socket Mode and Telegram is an
outbound connection. Only the optional dashboard needs a reverse proxy.

Confirm with the user before running anything on their server. If you have no shell there, give them the commands
as a numbered list and say where each one runs (laptop or server).

## Fresh server

```bash
# on the server
curl -fsSL https://get.docker.com | sh            # Docker Engine + compose plugin (Ubuntu/Debian)
sudo usermod -aG docker "$USER"                   # then log out and back in
git clone --recurse-submodules <repo-url> open-agent && cd open-agent
```

A private repository needs credentials on the server: a deploy key or a fine-grained read-only token.

Put `.env` on the server without passing it through chat, Slack or git. Either write it on the server, or copy it
from the laptop:

```bash
# on the laptop
scp .env <user>@<server>:~/open-agent/.env
```

Then, on the server:

```bash
chmod 600 .env
mkdir -p data/hermes/skills/team-history && touch data/hermes/config.yaml
sudo chown -R 10000:10000 data/hermes             # Hermes runs as UID 10000 on Linux
docker compose up -d --build                      # build on the server: laptop images may be the wrong CPU arch
```

Then follow "Start and verify" in SKILL.md (`ps`, logs, `check_permissions.sh`, nightly cron, live test).

## Moving from a laptop that already runs it

1. On the laptop, dump data, then stop the stack. Only one copy may hold the Slack app and the Telegram session.
   ```bash
   docker compose exec -T postgres pg_dump -U postgres --data-only openagent | gzip > openagent-data.sql.gz
   docker compose stop
   ```
2. Copy `.env`, `data/hermes/` (scheduled jobs, sessions, Notion login; skip `gateway.*` files) and the dump.
   Never copy `data/postgres`: it depends on the CPU architecture.
3. On the server: `chown` as above, start `postgres` alone, wait for `pg_isready`, load the dump, then start the
   rest:
   ```bash
   docker compose up -d --build postgres
   docker compose exec -T postgres pg_isready -U postgres -d openagent
   gunzip -c openagent-data.sql.gz | docker compose exec -T postgres psql -U postgres -d openagent -v ON_ERROR_STOP=1
   docker compose up -d --build
   ```

## After deploy

- Containers use `restart: unless-stopped`, so they come back after a reboot once Docker is enabled
  (`sudo systemctl enable docker`).
- Suggest a daily backup cron on the server, using the two backup commands in README "Operations".
- Updates: `git pull && git submodule update && docker compose up -d --build`.
