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

## Plan adjustments from what we learned

Use Relay #316's window, audit, rollback and gated-retire mechanisms; the old password login must obey the live deny state.

## What we have learned since (2026-10-24), applies to this item

1. **The reference is live and tested.** Agent Relay signs the owner in through central login (claude-relay PRs 13 to 19; 82+ tests). Copy it and its tests.
2. **Modes are configurable per client and per person (cougar-auth epic #404).** Each client can require `password`, `code` or `both` (default `both`; owner and superusers
   always `both`); a person can be set the same way, with floors. The login service decides what to ask for and the owner changes it with an owner command; the app never
   manages it. Each registered client (an app and its login type: web, native, TV) is configured on its own. **Apps verify the signed `amr`:** a code-only sign-in carries
   `amr=["mfa"]` with no `pwd` (read from the provider's source and proven on the disposable stack), so anything that needs two factors must require BOTH `pwd` and `mfa`.
3. **Forcing a fresh sign-in (step-up) is not available yet.** The live sign-in flow is `require_unauthenticated`, so re-authenticating a signed-in person returns the provider's
   404 "Not Found"; `max_age=0` is ignored (zero is falsy); `max_age=1` works. A login-service change (flow `authentication: none`) plus apps sending `max_age=1` is proven on the
   disposable stack and needs the owner's go-ahead (claude-relay #402, cougar-auth #405 and #408). Until then an app refuses a stale `auth_time` for privileged actions with a clear
   message instead of looping, and must not send `prompt=login`.
4. **Back-channel logout.** The client's `/auth/backchannel` is registered; implement it as Relay did (claude-relay PR 18: pinned-key signature, issuer, audience, event, one-use `jti`,
   no `nonce`, `iat` within 5 minutes, ends only that person's sessions, throttled). Whether the provider sends it on a central disable (not only on sign-out) and the latency are still unmeasured.
5. **Gotchas already paid for:** serve exactly the registered callback path; Cloudflare answers 403 to Python's default `urllib` user agent; the access log records the callback URL, so redact its
   `code` and `state`; serve `/favicon.ico` without auth; do not import an app at test collection if it binds its database at import; the callback answers a 200 page that navigates (design choice).
6. **Nobody can sign in until someone is in the admission group** (all groups except Relay's are empty). Adding the first person is a login-service change that needs the owner's OK.
7. **Process that worked:** off by default behind one flag; consistent backup first; prove a valid control and the matching rejection with the same real account; prove rollback live (`central off`
   then `on`); do not close until verified live; merged to main means done (Library item 26).

## Acceptance (in addition to the playbook's section 5 and 6 verification)

A banned account is denied on both logins; rollback keeps bans and mappings; retirement refuses until every runbook check is named.


## Delivery and boundaries

Implementation, review, merge, deployment and live verification are separate stages; do not close early. This document authorizes no code, account,
network or deployment change. Not shown anywhere: a passing run for this app.
Open Relay items every app reuses instead of re-solving: backchannel logout and central disable timing (claude-relay #401), step-up sign-in for
privileged actions (claude-relay #402).
