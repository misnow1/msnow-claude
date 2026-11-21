# Enforce standards mechanically where possible

Each standard that a tool can check is enforced by a tool, not by instructions in `CLAUDE.md`. Examples are linters, formatters, type checkers, hooks, CI jobs and agent definitions with a fixed `model:`. Instructions in `CLAUDE.md` are requests that an agent can interpret or skip. A tool gives the same result on each run. `CLAUDE.md` contains only the standards that no tool can check.
