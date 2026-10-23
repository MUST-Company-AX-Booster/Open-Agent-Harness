# Contributing

Bug reports, fixes, new connectors and answer-quality cases are welcome. By taking part you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Report a bug

[Open an issue](https://github.com/MUST-Company-AX-Booster/Open-Agent-Harness/issues) with reproduction
steps, the affected service, and redacted logs. Leave out tokens, session strings and private team
messages. Report security problems through [SECURITY.md](SECURITY.md), not an issue.

## Send a change

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Keep the pull request focused, and add a test for code changes.
3. Run the [test commands](README.md#tests) for the parts you touched.
4. For answer-quality changes, add a representative question to [`evals/cases.yaml`](evals/cases.yaml)
   and check it against your own sources.
5. Keep credentials in `.env` and deployment state in `data/`. Neither belongs in a pull request.

Changes to `hermes-agent/` belong upstream in [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Changes to `hermes/plugins/jev-skill-router/` should also go upstream to its author.

Contributions are accepted under the [MIT License](LICENSE).

## What happens next

See [GOVERNANCE.md](GOVERNANCE.md) for who reviews changes and how quickly.
