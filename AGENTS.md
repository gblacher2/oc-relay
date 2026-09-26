# oc-relay agent instructions

## Project overview

Describe what the project does and the user or system it serves.

## Architecture

Document important directories, modules, boundaries, data ownership, and design
principles. Link consequential decisions in `docs/adr/` when appropriate.

## Development

Document exact installation and run commands for this repository.

## Validation

Document exact test, lint, typecheck, build, and manual verification commands.
A failed required check means the task is not complete.

## Coding conventions

Document project-specific language, framework, compatibility, data, and UI rules.

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
