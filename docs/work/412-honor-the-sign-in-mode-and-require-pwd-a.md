# Honor the sign-in mode and require pwd and mfa for privileged actions

Parent: #322. Status: todo, unassigned, not started. Written 2026-10-24 by dexter-cougar-auth-3 from the Agent Relay adoption and the sign-in-mode proof (cougar-auth #405). Creating it authorizes no code, account, network or deployment change.

## Outcome
The owner can choose, on the login service, how people sign in to Deal Hunter (password, code or both; cougar-auth epic #404). Deal Hunter never manages that, but it verifies the signed `amr` and refuses a sign-in that is too weak for what is asked.

## Rules
`amr` is truthful but bare: password-only gives `["pwd"]`, both gives `["pwd","mfa"]`, code-only gives `["mfa"]` with no `pwd`. Anything that needs two factors (account and admin routes) must require BOTH `pwd` and `mfa`. Ordinary use follows the configured mode (default `both`). Until the login service allows re-authentication (cougar-auth re-authentication item), a privileged action from a stale sign-in is refused with a clear message and must not loop; never send `prompt=login`.

## Prerequisites (real)
The app's central login (#323/#324). The mode setting itself (cougar-auth #404) is not needed to build this; test with fake tokens carrying each `amr`.

## Acceptance (valid control beside each rejection)
account and admin routes: a token with `["pwd","mfa"]` is accepted; `["mfa"]` alone and `["pwd"]` alone are each refused for that reason; a stale `auth_time` is refused with the clear message; a guest or ordinary person cannot reach the privileged area. Break the both-markers check once and a test fails.

## Delivery
Implementation, review, merge, deployment and live verification are separate stages; do not close early. Playbook: https://github.com/sambena/cougar-auth/blob/main/docs/integration/adopt-central-login.md (also Library `central-login-adoption-playbook.md`; plans index: Library `central-login-app-plans.md`).
