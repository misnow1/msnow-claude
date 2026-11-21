# Deliver standards through a bootstrap skill

This repo delivers its development standards to other projects through a bootstrap skill. The skill writes the configuration, hooks and docs into each target repo. We rejected a Claude Code plugin because plugin content stays in the user's plugin cache and not in the target repo. We rejected a template repo because it applies only when a repo is created and cannot update an existing repo.

Consequence: each target repo holds its own copy of the standards. Repos stay portable, but a change here applies to a target repo only when the bootstrap skill runs again in that repo.
