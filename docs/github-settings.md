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
| `ruleset.name` | string | Name of the ruleset on the default branch. Use `protect-default-branch`. |
| `ruleset.required_approving_review_count` | integer, 0 to 10 | Approvals that a pull request needs. This is a minimum. A higher value in GitHub stays. |
| `ruleset.dismiss_stale_reviews_on_push` | boolean | Dismiss approvals when a push adds commits. |
| `ruleset.allowed_merge_methods` | list of `merge`, `squash`, `rebase` | Merge methods that the ruleset allows. |
| `required_checks` | list of strings | Unique names of the CI checks that the ruleset requires. The list is empty now. Later tickets add names. The ruleset pins each check to GitHub Actions, because any commit status can use a name without the pin. |

Write each `color` in quotes, with six hexadecimal digits. YAML reads an unquoted `000000` as a number.
Label names are unique when you ignore case.

The planner only creates labels. It does not change a label that exists, so your edits to its color or description stay.
When either squash field differs, the plan changes both fields, because GitHub accepts only some pairs.

## Ruleset

The ruleset targets the default branch (`~DEFAULT_BRANCH`). It requires a pull request, blocks force pushes and deletion, and lets the repository admin role bypass it in pull-request mode only.
The command keeps extra rules, extra bypass actors, extra check names, other branch patterns and other pull request parameters. It keeps a review count above the file value.
The file value is the authority for `dismiss_stale_reviews_on_push` and the merge methods. If the file turns off the dismissal that GitHub has on, the command turns it off.
The command always adds the admin bypass in pull-request mode when the ruleset has no entry for it.
The command stops when an exclude pattern has more than 1000 brace options, such as `{a,b}` repeated many times.
It sets only the values that it owns:

- the name and the active enforcement.
- the `deletion`, `non_fast_forward` and `pull_request` rules, and the owned pull request values.
- the required checks.
- the admin bypass.
- `~DEFAULT_BRANCH` in the branch patterns. The command also drops an exclude pattern that matches the default branch.

A second run after an applied plan makes no change.

Only repo rulesets count. The command never changes an organization ruleset.
The command skips a repo ruleset with the same name that targets tags or pushes. GitHub does not allow a second ruleset with that name.
A private repo on GitHub Free has no rulesets. The plan then skips the ruleset and lists it under `skipped`.

## Dry run

`bootstrap github TARGET --dry-run --state FILE` prints the plan as JSON and changes nothing.
This version supports only the dry run, so the command needs `--dry-run` and `--state`.
The state file is JSON. It replaces the live read of GitHub. These are its keys:

- `repository`: the repo settings, and `default_branch`. The command needs `default_branch` when a ruleset has an exclude pattern other than `~DEFAULT_BRANCH` and `~ALL`.
- `labels`: the labels of the repo.
- `empty`: true when the repo has no branch. The plan then starts with a `push` of the current local branch.
- `rulesets_available`: false for a private repo on GitHub Free.
- `rulesets`: the repo rulesets only. Each entry needs the key `source_type`. Read each one with `gh api repos/O/R/rulesets/ID`, because the list endpoint lacks `rules`, `conditions` and `bypass_actors`. A ruleset without those keys is not valid, so the command cannot plan an update that drops your rules.

List the repo rulesets with `gh api "repos/O/R/rulesets?includes_parents=false"`.
Each object can hold the extra keys that the GitHub API returns. You can paste the output of `gh api repos/O/R` under `repository`.
The command `gh api repos/O/R/labels` returns 30 labels for each page. Use `--paginate` and join the pages into one list under `labels`.
The command ignores a misspelled key. The field then counts as unknown, so the plan sets it.

The plan has two lists. `operations` holds `push`, `update_repository`, `create_label`, `create_ruleset` and `update_ruleset` operations, in this order: the push comes first, and the ruleset comes last. `skipped` holds the operations that the command cannot run, each with a reason.
