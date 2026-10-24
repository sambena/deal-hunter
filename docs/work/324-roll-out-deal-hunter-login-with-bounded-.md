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

**"Old login" here means every way in that does not go through central login**, not only the password form. Deal Hunter has these today (checked against `server.py` and `auth.py` on 2026-10-24; corrected after an independent review of PR 58).

Sign-in paths (each one issues a session or device credential):

| Path | Where | What it does today |
|---|---|---|
| Email and password | `POST /api/auth/login` | Web port: the `dh_session` cookie, or a device token when `device_name` is sent. API port: always a device token |
| Invite link | `POST /api/auth/accept` (kind `invite`) | Creates a new member account with a password and signs it in |
| Password-reset link | `POST /api/auth/accept` (kind `reset`) | Sets a new password, ends the person's other sessions and signs them in. It does **not** check `disabled` (the issued token is then refused by `auth.user_for_token`) |
| First-admin setup | `POST /api/auth/setup` | Web port only. Sets the first admin's password while no admin has one |

Credentials those paths leave behind (each one is accepted on later requests by `_identify`):

| Credential | Where | What it does today |
|---|---|---|
| Browser session | `dh_session` cookie, web port only | A `tokens` row of kind `web`. The cookie lasts 365 days and the server sets no expiry |
| Device token | `Authorization: Bearer` or `X-API-Key` | A `tokens` row of kind `device`. Lasts until revoked (logout, disable or reset) |
| Old single API key | API port only | Acts as the **first admin** and does not check `disabled`. Blocked for `API_PORT_BLOCKED` (`/api/settings`, `/api/api-key`, `/api/test-discord`) and `LEGACY_KEY_BLOCKED` (`/api/admin/`, `/api/tokens`). It still reaches `/api/import/ha`, `/api/me` and `POST /api/poll` (all watches) |

Also opened by an invite or reset code without a sign-in: `GET /api/auth/link` (it shows the email address for a reset) and the Android download (`/download/android?code=`).

Each path and credential needs an explicit decision for the window: keep it with a deny check, or switch it off when central login is switched on. Each also needs a planned end: retired, or replaced (for example, invites become admission-group membership and resets move to the login service). The rollout window, audit, rollback and retirement cover all of them. **Sessions and device tokens of both kinds** (`web` and `device`) issued by an old path stay subject to the live deny state and are revoked when that path retires, unless the person has re-linked through #323. The Deal Hunter Android app signs in with email and password and keeps a device token, so retiring the password path waits for #325/#326.

New open routes (`/auth/callback`, `/auth/backchannel`) must never call `me()`: with no signed-in user it returns the first admin (`server.py:54`).

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

Per old path (valid control beside each rejection):
- **A disabled member is refused:**
  - by password sign-in
  - by an existing `web` session
  - by an existing device token
  - by a reset link: the reset is refused before it changes the password. This is new behaviour; today the reset sets the password anyway.

  An enabled member gets in through each path that is still open.
- **Invites:** an invite always creates a new account, so there is no "disabled person" case. The check is that an invite whose email matches an existing account is refused (409 today), and that invites are refused once the path is switched off.
- **The old API key** is refused once it is retired, and on every route while the deny state marks the first admin. The deny check on this branch is new code (`_identify` does not check `disabled` today). The first admin cannot be disabled through the app (`server.py:951`), so test this by setting the deny state directly.
- **First-admin setup** is refused once any admin has a password or a central mapping.
- **Links made before a switch-off:** an invite or reset link made before its path was switched off is refused afterwards, and so are its `GET /api/auth/link` and Android-download uses. Using one changes nothing.
- **Retirement** refuses to complete while any path or credential above is still enabled, and names the open one. Re-enable one once and the retirement check fails.
- **Rollback** (`central off`) restores only the paths that were open before, and keeps bans, mappings and the deny state. While central login is off, #412's two-factor guard cannot be met. The owner must still be able to reach the admin routes needed in an incident (at least disabling an account), so #412 states what its guard does when central login is off.


## Delivery and boundaries

Implementation, review, merge, deployment and live verification are separate stages; do not close early. This document authorizes no code, account,
network or deployment change. Not shown anywhere: a passing run for this app.
Open Relay items every app reuses instead of re-solving: backchannel logout and central disable timing (claude-relay #401), step-up sign-in for
privileged actions (claude-relay #402).
