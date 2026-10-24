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

## Scope decision (owner, 2026-10-24)

No other item owns the base central sign-in, so #323 includes it, and it lands first, off by default behind one flag. It covers the begin and callback routes, token validation, sessions, and the owner-run link by exact subject, copied from Relay. Self-service linking follows in a later PR. The owner approved adding Relay's pinned packages (PyJWT, cryptography, Authlib, requests) with a `requirements.txt`. Until now the app had no packages to install.

## What is different from Relay

Relay only built an owner-run link by exact subject. Self-service linking with a one-use link state bound to both accounts and the browser (runbook section 1) is **new work** the reference does not cover.

**What "proves control of the Deal Hunter account" means here** (from `db.py`, `auth.py` and `server.py` on 2026-10-24):
- Proof is a fresh email-and-password sign-in to that account, inside the same link flow. An existing cookie or device token is not proof, and neither is a reset or invite link. A reset link is a password change someone else can start (the admin makes it), not proof by the person.
- **Accounts without a password.** `password_hash` is NULL only for the first admin before first-admin setup (`/api/auth/setup`). Invites set a password when they are used, and an unused invite has no account yet. A NULL-password account cannot be linked by self-service. It is linked only by the owner-run link by exact subject (as in Relay), or after setup gives it a password.
- **Disabled accounts** (`users.disabled = 1`) cannot be linked. Disabling an account after it is linked keeps the mapping and refuses sign-in.
- **The app cannot delete accounts.** It has no delete route and no `DELETE FROM users`, and there is no soft delete. A row removed by hand cascades only `tokens`, `user_settings` and `invites`. `watches`, `machines` and `ai_usage` carry a plain `user_id` with no foreign key, so they would be left orphaned. `users.id` is AUTOINCREMENT, so an id is never reused. "Deleted" therefore means "no such account": the link state names an account id that no longer exists. The test sets this up by deleting the row directly.
- **Email is neither stable nor unique.** A person can change it with `POST /api/me`, and `users.email` has no `UNIQUE` constraint. Neither `POST /api/me` nor setup checks for duplicates, and password sign-in takes the first match. So email is never the join key, either at link time or afterwards. The proof binds to the `users.id` that the fresh password sign-in returned, not to the email typed in. The mapping is the central subject to `users.id`.

## Plan adjustments from what we learned

Self-service account linking with proof of both accounts is NEW work the reference does not cover (Relay only has an owner-run link by exact subject). Never link by email.

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

Valid: the proven owner links the intended account. Rejected: matching email alone taking over an account, a collision (rolls back, both unchanged), a banned or deleted account, a link state from another browser.

Specific to Deal Hunter (valid control beside each rejection):
- **Valid control:** a fresh password sign-in plus a central sign-in links the account. Afterwards its `watches`, finds (`listings`), `machines`, `user_settings`, `ai_usage` (`user_id`, `paid_by`), `invites.created_by`, and its `users` columns (role, `watch_limit`, `ai_shared`, `ai_allowance`, `ai_daily_cap`) are unchanged, compared row by row with a snapshot taken before the link.
- **Rejected:**
  - an existing cookie or device token without a fresh password sign-in
  - a reset or invite link offered as proof
  - a NULL-password account through self-service (the owner-run link by exact subject still works for it)
  - a disabled account (`disabled = 1`)
  - a link state naming an account id that no longer exists
  - a central account whose email matches but which has no password proof
  - two local accounts sharing one email: the link goes to the account the password proved, never to the other one
- **Email change after linking:** the account still signs in through its central subject, and a different central account with the old email cannot take it over.


## Delivery and boundaries

Implementation, review, merge, deployment and live verification are separate stages; do not close early. This document authorizes no code, account,
network or deployment change. Not shown anywhere: a passing run for this app.
Open Relay items every app reuses instead of re-solving: backchannel logout and central disable timing (claude-relay #401), step-up sign-in for
privileged actions (claude-relay #402).
