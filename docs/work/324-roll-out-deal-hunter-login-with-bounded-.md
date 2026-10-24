# #324 Roll out Deal Hunter login with bounded legacy compatibility

Parent: #322. Status: todo, unassigned, not started. Updated 2026-10-24 by dexter-cougar-auth-3 with what Agent Relay's adoption taught (claude-relay #314 to #316, #398 to #402).

## Outcome

Central login is rolled out with the old password login kept for a dated window, then retired only through the gated checks.

## Start here (push-button)

Follow `docs/integration/adopt-central-login.md` in cougar-auth (https://github.com/sambena/cougar-auth/blob/main/docs/integration/adopt-central-login.md; also in the global Library as
`central-login-adoption-playbook.md`). Run `python scripts/app-login-manifest.py cougar-deals` first. Agent Relay is the finished, live reference
(claude-relay PRs 13 to 16): copy it and its tests, do not redesign or redo its discovery. The playbook lists the mistakes Relay already paid for.

## This app (read with the manifest tool on 2026-10-24)

- Registered client: `cougar-deals` (confidential, central user UUID as the subject). Exact callback `https://deals.cougarcave.dev/auth/callback`,
  post-logout `https://deals.cougarcave.dev/auth/logout`, backchannel logout `https://deals.cougarcave.dev/auth/backchannel`. The app must serve exactly those paths.
- Admission group `cougar-admission-cougar-deals` has **0 members**: nobody can sign in until the first person is added on the login service (ask first; that is a
  login-service change).

## What is different from Relay

Use Relay #316's window, audit, rollback and gated-retire mechanisms. The old login must read live ban/deny state or fail closed.

## Acceptance (in addition to the playbook's section 5 and 6 verification)

A banned account is denied on both logins; rollback keeps bans and mappings; retirement refuses until every runbook check is named.


## Delivery and boundaries

Implementation, review, merge, deployment and live verification are separate stages; do not close early. This document authorizes no code, account,
network or deployment change. Not shown anywhere: a passing run for this app.
Open Relay items every app reuses instead of re-solving: backchannel logout and central disable timing (claude-relay #401), step-up sign-in for
privileged actions (claude-relay #402).
