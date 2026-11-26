## Challenge loop

An independent reviewer reviews each plan before it goes to the user, and each diff before a pull
request. A round is one review and one fix pass. In each fix pass, fix each finding, majors
included.

Give the reviewer the plan or the diff, and the findings of earlier rounds, but not your
conclusions. The reviewer does not edit files.

The loop ends when a review finds no open findings, or after three rounds. The remaining findings
are the findings of the last review that its fix pass did not resolve.

- With only minor findings and nits left, the work continues. The pull request lists them as
  caveats.
- With a major left, stop and ask the user. For a plan, implement only after the user decides.
  For a diff, open the pull request only after the user decides.
- In a diff round, when a fix would change the approved plan, stop and ask the user. Continue only
  after the user decides.

The severities `major`, `minor` and `nit` come from `docs/severity-policy.md`.

The Challenge section comes after the other sections of the pull request body, before any
footer:

- One heading for each round of the diff review.
- Under the last round heading, one line for each remaining finding with its severity. Under each
  remaining major, a `Decision:` line with the user's decision. When no findings remain, the line
  `No remaining findings.`
- Under the heading of a round whose fix would change the plan, a `Decision:` line with the user's
  decision.
- A `Plan review` heading with one `Decision:` line for each decision that the user made on a plan
  review.
