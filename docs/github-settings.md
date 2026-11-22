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
With `--dry-run`, the command prints the plan and changes nothing.
Without `--state`, the command reads GitHub through `gh`. See "Read GitHub through gh".
Without `--dry-run`, the command runs the plan. See "Run the plan".
The state file is JSON. It replaces the read of GitHub through `gh`. These are its keys:

- `repository`: the repo settings, and `default_branch`. The command needs `default_branch` when a ruleset has an exclude pattern other than `~DEFAULT_BRANCH` and `~ALL`.
- `labels`: the labels of the repo.
- `empty`: true when the repo has no branch. The plan then starts with a `push` of the current local branch. The key `default_branch` is then required, and the local branch must have that name.
- `rulesets_available`: false for a private repo on GitHub Free.
- `rulesets`: the repo rulesets only. Each entry needs the key `source_type`. Read each one with `gh api repos/O/R/rulesets/ID`, because the list endpoint lacks `rules`, `conditions` and `bypass_actors`. A ruleset without those keys is not valid, so the command cannot plan an update that drops your rules.
- `unreadable_rulesets`: names of repo rulesets that the token cannot read. The plan then skips the ruleset with the reason "needs admin access to read the ruleset".

List the repo rulesets with `gh api "repos/O/R/rulesets?includes_parents=false"`.
Each object can hold the extra keys that the GitHub API returns. You can paste the output of `gh api repos/O/R` under `repository`.
The command `gh api repos/O/R/labels` returns 30 labels for each page. Use `--paginate` and join the pages into one list under `labels`.
The command ignores a misspelled key. The field then counts as unknown, so the plan sets it.

The plan has two lists. `operations` holds `push`, `update_repository`, `create_label`, `create_ruleset` and `update_ruleset` operations, in this order: the push comes first, and the ruleset comes last. `skipped` holds the operations that the command cannot run, each with a reason. An `update_ruleset` operation has `expected_updated_at`, the time of the last change of the ruleset when the command read it. The run uses it to find a change that came in between. A state file can leave it out.

## Read GitHub through gh

`bootstrap github TARGET --dry-run` without `--state` reads the real state of the repo through the `gh` CLI. It prints the plan and changes nothing. One line on stderr names the repo: `bootstrap: GitHub repo OWNER/REPO`.

Requirements:

- `gh` 2.48 or later, logged in with admin rights on the repo. Without admin rights, GitHub leaves out some settings, so the command stops.
- The remote `origin` is on `github.com`.

The command takes the repo from `origin`. It ignores `GH_REPO`, `gh repo set-default` and other remotes, so a fork clone reads its own repo and not the upstream repo. The fetch URL and the push URL of `origin` must name the same repo. Each must be one URL with the scheme https or ssh. The form `git@github.com:O/R` also works. A second `url` or `pushurl` value, an `http://` URL and a `file://` URL stop the command. The command drops a token in a remote URL and never prints it.
An SSH host alias such as `git@github-work:O/R.git` does not work. Use the https URL, or a `Host github.com` entry in `~/.ssh/config` in place of the alias.

For an empty repo, the local branch must have the name of the default branch that GitHub reports. The first push makes its branch the default branch, and the ruleset then protects it. To rename the branch, run `git branch -m NAME`.

The command reads only repo-level data. It reads rulesets with `includes_parents=false`, so it never reads or plans a ruleset of an organization.

Two cases are not errors. A private repo on GitHub Free has no rulesets, so the plan skips the ruleset. A token can lack the right to edit rulesets. A fine-grained token can be like this. The plan then skips the ruleset that it cannot read, with the reason "needs admin access to read the ruleset". The command then does not try to create a second ruleset with the same name.

## Run the plan

`bootstrap github TARGET` without `--dry-run` reads GitHub, plans, and runs the plan. It prints a report as JSON.

| Flags | State comes from | Effect |
| --- | --- | --- |
| `--dry-run --state FILE` | the file | prints the plan |
| `--dry-run` | GitHub, through `gh` | prints the plan |
| none | GitHub, through `gh` | runs the plan and prints the report |

A state file only works with `--dry-run`. A fake state cannot change GitHub.
For an empty repo, the plan starts with a `push`. See "The first push".

### The report

The report has two lists: `results` and `plan_skipped`. `plan_skipped` is the skip list of the plan, such as "rulesets are not available". Each entry in `results` has the operation, an `outcome` and sometimes a `reason`.

| Outcome | Meaning |
| --- | --- |
| `applied` | GitHub made the change. The reason can name fields that GitHub did not apply. |
| `skipped` | An organization policy blocked the change. Or GitHub ignored it: it applied no field of a settings change, or it cut down a ruleset. The ruleset then exists, but not as planned. |
| `failed` | The change did not happen, or the result is unknown. |
| `not_run` | An earlier operation failed, so the command did not try this one. |

The command stops at the first `failed` result. It still prints the report. The exit code is 1 when a result is `failed`, and 0 otherwise.
A run after a failure finishes the work. A run after a full success makes no change, and prints an empty report.

### The first push

An empty repo on GitHub has no branch. The plan then starts with a `push` of the local branch, before the settings, the labels and the ruleset. The ruleset comes last, so it cannot block the push.

The command runs `git push` with the explicit refspec `refs/heads/BRANCH:refs/heads/BRANCH`. A `remote.origin.push` setting or `push.default` cannot send the branch to another name. The command also turns off `push.followTags`, `push.recurseSubmodules` and `push.gpgSign` for this push. A tag, a submodule commit or a signature does not go with it. A `pre-push` hook still runs, because the hook is your own rule.

The local branch must have a commit, and its name must be the default branch that GitHub reports. If the local branch has no commit, a live run stops before any change. A dry run prints the plan, and writes a warning on stderr. The spec's dry run makes no commit, so it does not stop.
After a push that created the branch, the command reads the default branch of the repo again. If someone else pushed a different branch first, GitHub made that branch the default. The result is `failed`, and the ruleset is not created. The reason starts with the commit that the command pushed.

A failed push shows only a redacted reason. The reason comes from the status line of git for the branch. A word that holds a URL or an `@` becomes `<url>`, because a remote URL can hold a token. Git speaks English for this push, so the prefixes of its messages are the same on every machine. A commit that adds a file under `.github/workflows` needs the `workflow` scope for the token. Run `gh auth refresh -s workflow` if GitHub refuses it.

The push has a timeout of 300 seconds. Set `BOOTSTRAP_PUSH_TIMEOUT` to a whole number of seconds from 1 to 3600 to change it. After a timeout, Ctrl-C, SIGTERM or a hangup, the command kills `git` and its helpers. If you run the command with `nohup`, it keeps the hangup ignored. The result is unknown, because GitHub can have the commit already. Run the command again. It finds the branch and does not push it twice.
A push over SSH needs a key in `ssh-agent` and a known host key. The command cannot answer a question, so the push times out.

### What counts as a policy block

A result is `skipped` when two things are true. The status is 403 or 422, and the message names an organization or enterprise policy.
The message must not name any of these:

- SAML, single sign-on or OAuth.
- an IP allow list, a token, credentials or two-factor authentication.
- a bypass actor, an owner or admin role, a member, a permission, an authorization, a scope, an approval or access.

Those words name an access problem, so the result stays `failed`.
This rule is a guess. We have no verified GitHub message for an organization policy. A policy block that does not say "policy" is `failed`, so you see it. When you see a real message, record it here and check the rule.

GitHub can ignore a setting. An example is `allow_auto_merge` on a private repo on GitHub Free. The report then shows `skipped`, or `applied` with a reason. The plan cannot know this limit, so the next run plans the same change again and reports it again. The exit code stays 0.

A write can time out or return a 5xx answer, and the change can still apply. The reason then says "the result is unknown". Run the command again to see the real state.
The timeout is 60 seconds. Set `BOOTSTRAP_GH_TIMEOUT` to a whole number of seconds from 1 to 3600 to change it.

### What the run changes

The run changes only repo-level settings: the merge settings, the labels and the ruleset of the repo. Every write goes to `repos/OWNER/REPO`. The command never deletes anything, and never writes to an organization.
Before it replaces a ruleset, it reads the ruleset again. If the ruleset changed after the first read, the result is `failed`, and a new run starts from the new state. If the read gave no time of the last change, the result is also `failed`.
If GitHub reports a new name for the repo, the command stops before any write, and tells you to update `origin`.

### Check a run by hand

Use a scratch repo. Run the command, then run it again. The second report must be empty.
To check the replace of a ruleset, change the ruleset in the GitHub page of the repo. Turn off the dismissal of stale approvals, add a rule and add a bypass actor. Run the command. Your rule and actor must stay, and the dismissal must be on again. Run the command a third time. The report must be empty.
To check the first push, make a scratch repo on GitHub with no file. In a local folder, run `git init -b main`, then add the settings file and a file under `.github/workflows`, and commit. Add `origin` with the https URL. Run the command with `--dry-run`. The plan must start with a `push`. Run the command. The first result must be the `push`. Run it again. The report must be empty.
