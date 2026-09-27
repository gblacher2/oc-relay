# oc-relay agent instructions

## Project overview

`oc-relay` is a macOS-only, localhost reverse proxy that lets LM Studio's
`openai-compat-endpoint` plugin call OpenCode Go. It injects the required stable
`x-opencode-session` header while preserving streaming response delivery.

## Architecture

- `src/oc_relay.py`: relay server and configuration loading.
- The typed `oc-relay model` CLI stores only a model ID in an owner-only state
  file. The relay reads it per Chat Completions request without restarting or
  exposing a writable HTTP control endpoint.
- `bin/oc-relay`: launchd management and operator CLI.
- `scripts/`: installer and uninstaller.
- `launchd/`: LaunchAgent template.
- `tests/`: stdlib unit and integration tests.

The repository contains reconstruction-safe source only. Installed files,
configuration, LaunchAgent state, logs, credentials, and API keys stay outside
Git in the user's home directory.

## Development

Install with `./scripts/install.sh`. Operate with `~/.local/bin/oc-relay`.
Use only the Python standard library and macOS system tools.

## Validation

Run all of the following from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
./scripts/check.sh
```

For installed-service verification also run:

```bash
~/.local/bin/oc-relay status
~/.local/bin/oc-relay models
curl --fail --silent http://127.0.0.1:8788/health
```

## Coding conventions

- Target the system Python available on supported macOS versions.
- Bind only to `127.0.0.1`; never add a configurable public bind address.
- Never log request headers, response bodies, credentials, or API keys.
- Preserve response streaming; do not buffer successful upstream responses.
- Apply a selected model only to future `POST /chat/completions` requests.
  Reject unsupported IDs; never translate Messages or Responses implicitly.
- Shell scripts use `set -euo pipefail` and pass `shellcheck` when available.

## GitHub workflow and agent guardrails

- GitHub is the canonical system of record. Record durable decisions, acceptance
  criteria, implementation state, review findings, and validation in the repository,
  Issues, or pull requests rather than only in an AI conversation.
- Meaningful work uses an Issue or equivalent complete PR context, a scoped branch,
  an early draft PR, review comments, verification, and a final `READY FOR HUMAN` state.
- Use `feat/`, `fix/`, `refactor/`, or `chore/` branch prefixes. Do not implement normal
  feature work directly on `main` and do not merge without human authorization.
- Inspect the existing architecture and `git status` before changing files. Preserve
  unrelated local work and prefer minimal, scoped modifications.
- Do not suppress type or lint failures, replace working implementations unnecessarily,
  introduce dependencies without justification, or alter unrelated code.
- Never commit secrets, credentials, `.env` values, personal data, local databases,
  logs, or machine-specific state. Commit reconstruction-safe examples and setup docs.
- Push meaningful checkpoints and keep the PR accurate so a fresh agent can continue
  from GitHub alone.
