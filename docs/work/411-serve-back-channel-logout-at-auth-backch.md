# Serve back-channel logout at /auth/backchannel (as Relay)

Parent: #322. Status: todo, unassigned, not started. Written 2026-10-24 by dexter-cougar-auth-3 from the Agent Relay adoption and the sign-in-mode proof (cougar-auth #405). Creating it authorizes no code, account, network or deployment change.

## Outcome
When the login service signs a person out, Deal Hunter ends that person's sessions there, so a copied session cookie stops working. The client's `/auth/backchannel` URL is already registered; Deal Hunter does not serve it yet.

## Scope and reference
Same behaviour as Agent Relay (claude-relay PR 18: `validate_logout_token`, `backchannel`, `backchannel_logout`): a signed token checked against the pinned keys, the exact issuer and this client as audience, an `events` entry for back-channel logout, a `jti` used once, no `nonce`, `iat` within five minutes; ends only that linked person's sessions; unknown person, replay, bad method, oversize body and throttled callers get a bare error and change nothing. Audit line without the token.

## Prerequisites (real)
The app's central login (#323/#324) exists first; admission group has at least one member (cougar-auth item on admission groups).

## Acceptance (valid control beside each rejection, fake provider)
A valid logout token ends that person's sessions and not another person's. Rejected, each for its own reason: wrong issuer, wrong audience, forged signature, expired or too old, no `events`, wrong event, a `nonce` (an ID token), no `jti`, no subject, unknown person, replay, GET instead of POST. Throttle returns 429. Breaking each check once makes a test fail.

## Not shown anywhere yet
Whether the real login service sends a logout token on a central account disable (not only on sign-out), and the latency (tracked in claude-relay #401).

## Delivery
Implementation, review, merge, deployment and live verification are separate stages; do not close early. Playbook: https://github.com/sambena/cougar-auth/blob/main/docs/integration/adopt-central-login.md (also Library `central-login-adoption-playbook.md`; plans index: Library `central-login-app-plans.md`).
