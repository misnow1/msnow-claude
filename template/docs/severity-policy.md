# Severity policy

A reviewer gives each finding exactly one severity. The challenge loop in `AGENTS.md` uses the
severity to decide what happens after the last round.

| Severity | Meaning |
| --- | --- |
| `major` | A finding in one of the classes below. |
| `minor` | A real problem that is not major. |
| `nit` | A wording or style problem. |

## Classes of a major

A finding is major when it can cause one of these:

- unauthorized access
- data loss or deletion
- exposed secrets

This project owns this file. Add a class when a problem must stop the work until a person decides.
The bootstrap does not change this file when it exists.
