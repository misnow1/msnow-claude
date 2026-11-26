## Sub-agent model

`.claude/settings.json` sets `CLAUDE_CODE_SUBAGENT_MODEL` to the alias `opus`. A sub-agent that
starts with no model, on an agent type with no `model:` field, then runs on Opus. So a reviewer
that a skill starts with no model, on such an agent type, runs on Opus and not on Sonnet under
`opusplan`.

- A model in the call or a `model:` field in an agent definition still wins, because the bootstrap
  does not set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`.
- The variable does not change the built-in Explore and Plan agents.
- Every sub-agent with no model runs on Opus, implementers too. This costs more than Sonnet.
- The setting applies only after you trust the folder.
- To see it work, start a sub-agent. Then read the `message.model` field in
  `~/.claude/projects/<project>/<session-id>/subagents/agent-<id>.jsonl`.
