# Security policy

Open Agent reads private team messages and holds tokens for Slack, Telegram, Jira, GitHub, Notion and
your model provider. Report vulnerabilities privately. Do not report them in public issues.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/MUST-Company-AX-Booster/Open-Agent-Harness/security/advisories/new).
If you can't use it, email aero@must.company and zaffreblue@must.company with "Open Agent security" in the
subject.

Include the affected service (`collector`, `hermes`, a plugin, the database setup), steps to reproduce,
and the impact. Leave out real tokens, session strings and private team messages.

## What to expect

- A first reply within 7 days.
- A fix or a mitigation plan within 30 days for confirmed issues, then a published advisory that credits
  you unless you ask us not to.

## Supported versions

Only the latest commit on `main` gets security fixes.

## Scope

In scope: code in this repository, the bundled Hermes configuration, and the database permission setup.
Report issues in [Hermes Agent](https://github.com/NousResearch/hermes-agent) or `jev-skill-router` to
those projects. Tell us too if they affect Open Agent.

Prompt injection through collected messages is a known limit. The [Security](README.md#security)
section of the README explains why. Reports that bypass the tool restrictions or the database
permission split are in scope.
