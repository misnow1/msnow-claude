# msnow-claude

Development standards for my repos, applied by a bootstrap command.
See issue #2 for the spec.

`docs/github-settings.md` describes the GitHub settings file.

`bootstrap apply TARGET` writes the standards into a repo. An empty repo also gets the first commit and the GitHub step. Use `--dry-run` to see the files and the GitHub plan first, and `--state FILE` to read the GitHub state from a file in a dry run. `docs/adr/0004-apply-runs-github-for-empty-repos.md` explains the rules.

The template gives each repo a `Makefile` with one `check` entry point, a pre-commit config and a file-length check. A CI workflow runs the check, and the GitHub settings file requires it. `docs/adr/0005-file-length-with-scc-in-pre-commit.md` explains the check.
