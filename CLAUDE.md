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

## Enforcement

When a tool can check a standard, a tool enforces it. Use linters, formatters, type checkers, hooks, CI jobs and agent definitions with a fixed `model:`. An instruction in `CLAUDE.md` is a request that an agent can interpret or skip, but a tool gives the same result on each run. Put a standard in `CLAUDE.md` only when no tool can check it.

## Working in this repo

This section is temporary. Delete it in the pull request for #18, after the bootstrap sections hold these rules. Tools from the bootstrap will check some of these rules later. Until then, follow them by hand.

The spec is issue #2. Its tickets are the sub-issues of #2, and each ticket lists the tickets that block it.

- **Planning.** Start each change in plan mode. Until #8 sets `opusplan`, the session model applies in plan mode too. Present the plan, run the challenge loop on it, and get the user's approval before you implement.
- **Tests.** For each code change, write a failing test first. Use `/mattpocock-skills:tdd`.
- **Challenge loop.** Run the challenger on each plan before you implement it, and on each diff before you open a pull request. The challenger is an independent subagent. Start it with the Agent tool and set `model` to `opus`, because this repo has no agent definitions yet. Do not use `subagent_type: "fork"`, because a fork inherits this conversation and ignores `model`. A round is one review and one fix pass.
  - Put the severity rules in the reviewer's prompt. Give it the diff or the plan, not your conclusions. Tell it not to edit files.
  - The reviewer gives each finding one severity. A `major` is a finding that can cause unauthorized access, data loss or deletion, or exposed secrets. A `minor` is a real problem that is not major. A `nit` is a wording or style problem. Until #8 adds the severity policy doc, these rules are the policy.
  - In each fix pass, fix each finding, majors included.
  - Stop when a review finds no open findings, or after three rounds. Remaining findings are the findings of the last review that its fix pass did not resolve.
  - When a major remains after the last round, stop and ask the user.
  - When a fix in a diff-review round would change the approved plan, stop and ask the user.
  - For a plan, start to implement only after the user decides. For a diff, open the pull request only after the user decides.
  - Remaining minor findings and nits do not block the plan or the pull request. For a diff, list them as caveats in the Challenge section.
- **Challenge section.** Put a Challenge section after the sections of the `/mattpocock-skills:pr` skill. Give it a heading for each round of the diff review. Under the last round heading, write one line for each remaining finding with its severity. Under each remaining major, write a `Decision:` line with the user's decision. When no findings remain, write `No remaining findings.` under the last round heading. A fix can change the approved plan, and then you stop to ask the user. Write a `Decision:` line with that decision under the round heading. Then add a `Plan review` heading with one `Decision:` line for each decision that the user made on a plan review.
- **Issues.** Before you file an issue, search for related issues and pull requests. Then have an independent subagent review the issue or the batch of issues. Start it with `model` set to `opus`, and do not use `subagent_type: "fork"`.
- **Pull requests.** Keep each pull request to 400 changed lines or fewer, not counting lockfiles. A larger pull request needs the `size-override` label and a reason. Write the body with `/mattpocock-skills:pr`, and add `Closes #<n>`. The title is a Conventional Commit, because GitHub squash-merges the pull request and uses its title as the commit title. Write each commit message as a Conventional Commit too.
- **Prose.** Apply the `unslop` skill to all prose in docs, issues and pull requests. Run its STE linter on each changed Markdown file: `python3 <unslop skill directory>/scripts/ste-lint.py FILE`. The skill shows its base directory when it loads. Fix each hard finding. Advisory findings do not block.
