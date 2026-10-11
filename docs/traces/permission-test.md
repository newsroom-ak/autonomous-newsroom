# GitHub Agent Permission and Merge-Gate Verification

## Assessment

Project: Autonomous Newsroom
Assessment: Confer Inc. AI/ML Take-Home
Repository: `newsroom-ak/autonomous-newsroom`
Verification date: October 11, 2026
Status: Five permission tests passed; runtime isolation pending.

## 1. Purpose

Verify that the autonomous newsroom's GitHub permission
architecture enforces separation between the Builder, Reviewer,
Tester, and human repository owner.

The system uses four fine-grained personal access tokens belonging
to one dedicated GitHub machine account, `newsroom-ak-bot`.

The protected `main` branch requires:

- Pull requests for changes.
- One approving pull-request review.
- Successful `agent/review` and `agent/test` commit statuses.
- Approval from someone other than the most recent pusher.
- Protection against force pushes and branch deletion.

GitHub Actions is disabled.

## 2. Agent identities

| Agent | Runtime | GitHub identity | Responsibility |
|---|---|---|---|
| Dispatch | Hermes | newsroom-ak-bot | Issue coordination |
| Builder | OpenClaw | newsroom-ak-bot | Implement code and create PRs |
| Reviewer | Hermes | newsroom-ak-bot | Code review and review status |
| Tester | OpenClaw | newsroom-ak-bot | Testing and test status |

Each agent has a separate fine-grained token restricted to
`newsroom-ak/autonomous-newsroom`.

The human account, `Ankita2525`, owns the organization and
is responsible for reviewing and merging agent contributions.

## 3. Permission verification results

### Test 1 — Builder cannot publish review statuses

Token: `newsroom-builder`

Attempted operation:

POST /repos/newsroom-ak/autonomous-newsroom/statuses/{sha}

Requested context: `agent/review`
Requested state: `success`

Observed response:

HTTP 403

Error:

Resource not accessible by personal access token

Result: PASS

The Builder token cannot publish the review status through
the tested GitHub API endpoint.

### Test 2 — Reviewer can publish commit statuses

Token: `newsroom-reviewer`

Attempted operation:

POST /repos/newsroom-ak/autonomous-newsroom/statuses/{sha}

Requested context: `permission-test/reviewer`
Requested state: `success`

Observed response:

- State: success
- Context: permission-test/reviewer
- Creator: newsroom-ak-bot

Result: PASS

The Reviewer token successfully published a commit status.

The test used a separate verification context to avoid
satisfying the production `agent/review` merge requirement.

### Test 3 — Tester can publish commit statuses

Token: `newsroom-tester`

Attempted operation:

POST /repos/newsroom-ak/autonomous-newsroom/statuses/{sha}

Requested context: `permission-test/tester`
Requested state: `success`

Observed response:

- State: success
- Context: permission-test/tester
- Creator: newsroom-ak-bot

Result: PASS

The Tester token successfully published a commit status.

The test used a separate verification context to avoid
satisfying the production `agent/test` merge requirement.

### Test 4 — Machine account cannot approve its own PR

Test pull request:

https://github.com/newsroom-ak/autonomous-newsroom/pull/1

PR author: newsroom-ak-bot
Token attempting approval: newsroom-reviewer

Attempted operation:

gh pr review 1 --approve

Observed response:

GraphQL: Review Can not approve your own pull request

Result: PASS

GitHub prevented the Reviewer token from approving a pull
request created by the same underlying machine account.

Separate tokens do not bypass GitHub's self-approval restriction.

### Test 5 — Direct update to protected main is blocked

Token: newsroom-builder

Attempted operation:

PATCH /repos/newsroom-ak/autonomous-newsroom/git/refs/heads/main

Observed response:

HTTP 422

Repository rule violations found.

GitHub reported:

- New changes require approval from someone other than
  the last pusher.
- Two required status checks were expected.

Result: PASS

The active repository ruleset rejected the attempted direct
update to `main`.

## 4. Main branch integrity verification

After the rejected update, the repository's main branch
was queried through the GitHub API.

Observed commit SHA:

bb8576e8fcbfd80bf9645d5121b8a396c555ee86

This matches the original repository bootstrap commit.

Result: VERIFIED

The attempted update did not modify `main`.

## 5. Summary

| Test | Result |
|---|---|
| Builder cannot publish review statuses | PASS |
| Reviewer can publish commit statuses | PASS |
| Tester can publish commit statuses | PASS |
| Machine account cannot approve its own PR | PASS |
| Direct update to protected main is blocked | PASS |
| Main branch remains unchanged | VERIFIED |

All five planned GitHub permission tests passed.

These results validate the tested GitHub API permission
boundaries and branch protection configuration.

They do not establish complete runtime isolation between
the autonomous agents.

## 6. Known limitations

1. All four agents share one GitHub machine account.
   GitHub attributes their actions to `newsroom-ak-bot`.

2. Reviewer and Tester both possess Commit statuses write
   permission. Either token can technically publish either
   required status context.

3. Separate fine-grained tokens enforce API permissions,
   but do not independently guarantee process-level or
   operating-system-level credential isolation.

4. Agent attribution requires additional application-level
   evidence, including agent-specific Git authors,
   structured comments, and execution traces.

5. The Tester's execution of Builder-written code requires
   a sandbox without GitHub credentials.

6. The human account has a pull-request-only bypass
   configured. This permits exceptional human-controlled
   merges and is not a restriction GitHub can limit to
   particular folders.

7. The temporary self-approval verification PR was created
   for testing and must not be merged into main.

## 7. Remaining verification

The following controls still require live testing:

- Agent runtime credential isolation.
- Reviewer and Tester execution separation.
- Sandbox restrictions for Builder-written code.
- Agent-specific audit logs and issue transitions.
- End-to-end PR review, testing, and merge workflow.
- Human bypass governance.

## 8. Evidence references

Repository:
https://github.com/newsroom-ak/autonomous-newsroom

Self-approval test PR:
https://github.com/newsroom-ak/autonomous-newsroom/pull/1

Original main commit:
bb8576e8fcbfd80bf9645d5121b8a396c555ee86

The verification results were obtained from live GitHub
API requests and observed Terminal responses.

No personal access token values are included in this report.