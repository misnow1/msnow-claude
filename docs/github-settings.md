# GitHub settings file

The `github` command reads the desired GitHub settings from `.github/bootstrap.yml` in the target repo.
`bootstrap apply` writes this file. A repo that has the file can run `bootstrap github` alone.

Pydantic checks the file. The command stops with an error when the file is missing, empty or not valid.
An unknown key, a duplicate key and a symlink are not valid. A value must have the exact type: `"yes"` is not a boolean.
GitHub accepts only some pairs of squash title and message. The file must use one of them: `PR_TITLE` with any message, or `COMMIT_OR_PR_TITLE` with `COMMIT_MESSAGES`.

## Keys

| Key | Type | Meaning |
| --- | --- | --- |
| `repository.allow_squash_merge` | boolean | Allow squash merges. |
| `repository.allow_merge_commit` | boolean | Allow merge commits. |
| `repository.allow_rebase_merge` | boolean | Allow rebase merges. |
| `repository.squash_merge_commit_title` | `PR_TITLE` or `COMMIT_OR_PR_TITLE` | Title of the squash commit. |
| `repository.squash_merge_commit_message` | `PR_BODY`, `COMMIT_MESSAGES` or `BLANK` | Message of the squash commit. |
| `repository.delete_branch_on_merge` | boolean | Delete the branch after a merge. |
| `repository.allow_auto_merge` | boolean | Allow auto-merge. |
| `labels` | list | Labels that must exist. Each label has `name`, `color` and `description`. |
| `required_checks` | list of strings | Names of the CI checks that the ruleset requires. The list is empty now. Later tickets add names. |

Write each `color` in quotes, with six hexadecimal digits. YAML reads an unquoted `000000` as a number.
Label names are unique when you ignore case.

The planner only creates labels. It does not change a label that exists, so your edits to its color or description stay.
When either squash field differs, the plan changes both fields, because GitHub accepts only some pairs.

## Dry run

`bootstrap github TARGET --dry-run --state FILE` prints the plan as JSON and changes nothing.
This version supports only the dry run, so the command needs `--dry-run` and `--state`.
The state file is JSON. It replaces the live read of GitHub and has the keys `repository` and `labels`. Each object can hold the extra keys that the GitHub API returns. You can paste the output of `gh api repos/O/R` under `repository` and the labels under `labels`. The command `gh api repos/O/R/labels` returns 30 labels for each page, so use `--paginate` and join the pages into one list. The command ignores a misspelled key. The field then counts as unknown, so the plan sets it.
The plan has two lists. `operations` holds `update_repository` and `create_label` operations. `skipped` holds the operations that the command cannot run. It is always empty now.
