# #323 Link proven email accounts while preserving deals and roles

Parent: #322. Status: todo, unassigned, not started. Updated 2026-10-24 by dexter-cougar-auth-3 with what Agent Relay's adoption taught (claude-relay #314 to #316, #398 to #402).

## Outcome

A person proves control of their Deal Hunter account and their central identity, and the two are linked in one transaction that keeps saved deals, preferences and roles.

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

Relay only built an owner-run link by exact subject. Self-service linking with a one-use link state bound to both accounts and the browser (runbook section 1) is **new work** the reference does not cover.

## Acceptance (in addition to the playbook's section 5 and 6 verification)

Valid: the proven owner links the intended account. Rejected: matching email alone taking over an account, a collision (rolls back, both unchanged), a banned or deleted account, a link state from another browser.


## Delivery and boundaries

Implementation, review, merge, deployment and live verification are separate stages; do not close early. This document authorizes no code, account,
network or deployment change. Not shown anywhere: a passing run for this app.
Open Relay items every app reuses instead of re-solving: backchannel logout and central disable timing (claude-relay #401), step-up sign-in for
privileged actions (claude-relay #402).
