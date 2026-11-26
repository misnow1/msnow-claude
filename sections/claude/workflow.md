## Workflow

- Start each change in plan mode. Write the plan, run the challenge loop of `AGENTS.md` on it, then
  present the plan to the user. Implement only after the user approves the plan.
- Run the `challenger` agent on each plan before you present it, and on each diff before a pull
  request.
- Start the `challenger` and `issue-reviewer` agents without a `model` parameter. Their `model:`
  field is Opus, and a model in the call wins over it.
- Run the `issue-reviewer` agent on an issue or a batch of issues before you file or edit them.
- The two agents have Bash, so they are not read-only by tool. Do not give them work that writes.
- Write the pull request body with `/mattpocock-skills:pr`, then add the Challenge section of
  `AGENTS.md`.
