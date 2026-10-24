# Honor the sign-in mode and require pwd and mfa for privileged actions

Parent: #322. Status: todo, unassigned, not started. Written 2026-10-24 by dexter-cougar-auth-3 from the Agent Relay adoption and the sign-in-mode proof (cougar-auth #405). Creating it authorizes no code, account, network or deployment change.

## Outcome
The owner can choose, on the login service, how people sign in to Deal Hunter (password, code or both; cougar-auth epic #404). Deal Hunter never manages that, but it verifies the signed `amr` and refuses a sign-in that is too weak for what is asked.

## Rules
`amr` is truthful but bare: password-only gives `["pwd"]`, both gives `["pwd","mfa"]`, code-only gives `["mfa"]` with no `pwd`. Anything that needs two factors (account and admin routes) must require BOTH `pwd` and `mfa`. Ordinary use follows the configured mode (default `both`). Until the login service allows re-authentication (cougar-auth re-authentication item), a privileged action from a stale sign-in is refused with a clear message and must not loop; never send `prompt=login`.

## Privileged routes in Deal Hunter today
From `server.py` on 2026-10-24, corrected after an independent review of PR 58. Admin routes and account routes are different groups.
- **Admin routes** (admin only):
  - `/api/admin/*` (users, invites, invite cancel, user update, resets)
  - `GET` and `POST /api/import/ha`
  - `POST /api/api-key`
  - the admin-only parts of `PUT /api/settings`: the shared keys (`shared_allowed`), the admin's own Home Assistant link (`ha_url`, `ha_token`) and LAN addresses for `ollama_url`/`ha_url` (`local_ok=is_admin()`)

  For an admin, all of `PUT /api/settings` is guarded, not only the shared keys. Members keep their ordinary settings.
- **Account routes** (every person, members included): `POST /api/me` when it changes email or password (a name change needs no password and is ordinary), `GET /api/tokens` and `DELETE /api/tokens/<id>`.

## Credentials that carry no `amr`
Only a central sign-in carries a signed `amr`. Three other credentials reach the app without one:
- **The old single API key** acts as the first admin, on the API port only. That port already refuses `/api/settings`, `/api/api-key` and `/api/test-discord` for every credential (`API_PORT_BLOCKED`), and the key is also refused for `/api/admin/` and `/api/tokens` (`LEGACY_KEY_BLOCKED`). The privileged routes it still reaches are `GET`/`POST /api/import/ha` and `POST /api/me`.
- **Device tokens** (kind `device`) and **browser sessions** (kind `web`, the `dh_session` cookie) issued by any old path: password sign-in, an invite, a reset or first-admin setup.

Rule: a request that carries no proven `pwd` and `mfa` is refused on every admin route, and on the account routes, for that reason, whatever role the account has. Ordinary routes keep working within #324's window. If a device or script needs admin access, that waits for a central device sign-in; it is not an exemption.

**While central login is off** (before rollout, or after #324's rollback), nobody can meet this rule. This is an open decision for the owner, to settle before implementation. The recommended default: while the central switch is off, the guard is not enforced and the app behaves as today. The audit log records each privileged action taken without `amr`. Turning the switch on enforces the guard. Without this, a rollback would lock the owner out of disabling an account during an incident.

## Prerequisites (real)
The app's central login (#323/#324). The mode setting itself (cougar-auth #404) is not needed to build this; test with fake tokens carrying each `amr`.

## Acceptance (valid control beside each rejection)
Each case below has a valid control beside it. Unless a step names the API port, everything runs on the web port with the central switch on.
- **Admin and account routes:**
  - accepted: a central sign-in with `["pwd","mfa"]`
  - refused, each for its own reason: `["mfa"]` alone, `["pwd"]` alone, and a stale `auth_time` (with the clear message)
- **Admin routes, other roles:** a member with `["pwd","mfa"]` is refused (403, "only the admin"), and so is a signed-out request (401).
- **Account routes, members:** a member with `["pwd","mfa"]` is accepted on their own account routes.
- **Old credentials, web port:** each admin and account route is refused with an old `web` session and with an old device token. Beside each, a central `["pwd","mfa"]` admin control is accepted on the same route.
- **Old API key, API port:** refused on `POST /api/import/ha` and on `POST /api/me` (email change). These refusals come from the new guard; the existing `API_PORT_BLOCKED` and `LEGACY_KEY_BLOCKED` refusals do not count. Beside each, an ordinary route (`GET /api/state`) still answers with the old key during the window.
- **`PUT /api/settings`:** an admin request without `pwd`+`mfa` is refused even when it changes only a LAN `ollama_url`. A member changing their own ordinary settings is accepted.
- **Central switch off:** the behaviour follows the owner's decision above, and a test pins it.
- **Mutation checks:** break the both-markers check once and a test fails; remove one privileged route from the guard and a test fails.

## Delivery
Implementation, review, merge, deployment and live verification are separate stages; do not close early. Playbook: https://github.com/sambena/cougar-auth/blob/main/docs/integration/adopt-central-login.md (also Library `central-login-adoption-playbook.md`; plans index: Library `central-login-app-plans.md`).
