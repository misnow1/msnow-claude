# Run the GitHub step in apply only for an empty repo

The `apply` command runs the `github` step only when the local repo has no commit. It makes the first commit of that repo, and `github` pushes it. For an existing repo, `apply` writes the files, makes no commit and calls no GitHub API. The skill runs `bootstrap github .` after the bootstrap pull request merges, so the ruleset never requires checks that are not yet on the default branch (#17).

We decide that a repo is empty from its local state, and not from GitHub. An existing repo then needs no network and no `gh`. We rejected a read of GitHub in each `apply`, because `apply` would then need `gh`, a login and an origin for every repo. The cost is that a rerun of `apply` does not retry a failed push. The commit stays, and `bootstrap github .` finishes the work. A local repo with commits and an empty GitHub repo counts as an existing repo. The note that `apply` prints says to commit the bootstrap files and run `bootstrap github .`, which pushes the branch itself.

A repo with no commit and no `origin` gets the commit, and no GitHub step. `apply` prints a note. This lets you start a project before its GitHub repo exists. An `origin` that is not on `github.com` stops `apply` for an empty repo, because the GitHub step reads it. To skip the step, run `apply` in a repo that has no `origin`.

A dry run writes no file into the repo. The spec said that a dry run writes the files and makes no commit. But a live `apply` after such a dry run stops on the uncommitted files. The skill runs a dry run, shows the plan, and then runs `apply`. So the dry run renders the files into a scratch folder. It prints the files that a live run writes, the files that an update deletes, and the GitHub plan. A dry run on an existing repo cannot show the conflicts of the three-way merge of an update.

The commit stages the files that `apply` wrote, and no others. A file that git already ignores, for example through a global `.claude/` rule, stops `apply` for an empty repo before it writes anything. For an existing repo, `apply` warns. The commit refuses the identity that git guesses from the user and host name, because the push sends a first commit to GitHub.

`apply` has no input for project-owned content yet. #17 says that the majors you give go into the severity policy doc in the bootstrap commit. #17 or #8 must add that input before `apply` can do it.
