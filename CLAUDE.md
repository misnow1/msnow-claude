# CLAUDE.md

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues on `misnow1/msnow-claude` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Project memory

Agent memory stays on one machine. Repo docs move with the repo. Record durable knowledge in the repo, not in agent memory:

- Decisions go in `docs/adr/`.
- Domain terms go in `GLOSSARY.md`.
- Rules for how to work go in `CLAUDE.md`.
