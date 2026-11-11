# 1120 Deal Hunter without ceremony (epic 1113, Den Library 175)

## Outcome
A family member added to Deal Hunter in the owner console signs in with a password and uses it. The owner is always
an admin; other admins are switched on in People. Nobody is told to use a private window or clear cookies.

## What changed
- **Switched off:** a member switched off in People gets one sentence: "Your Deal Hunter account is switched off.
  <admin names> can switch it on again in Deal Hunter's People screen." It is a 403 `switched_off` (was a 401, which
  brought the sign-in card back and looped). The page shows the sentence, no sign-in card. Phones get the same code.
- **Admin:** the Cougar Cave owner (the framework's owner role, today its owner list; framework 1.2.9 reads the owner
  console's list) is made admin on their next visit; People shows "owner" and refuses taking that admin away or
  switching the owner off. People has Make admin / Remove admin
  for others; you can't remove your own admin and there is always one admin. A new admin has no watch limit; someone
  who stops being one gets the default 10 back.
- **Sign-in:** password alone is enough for everyone, admins included. Reading People needs nothing more. A change in
  People, the Home Assistant import, or a change to a shared setting needs a confirmed sign-in (one that used the
  second factor; no age check). Without one: 403 `needs_confirm` with `fix: {kind: 'confirm', href: '/auth/login?confirm=1&next=…'}`.
  The page asks "Confirm it's you", keeps the change in sessionStorage (admin paths only, same person, 15 minutes),
  and sends it once again on return; if the sign-in still lacks the code it says so in one sentence, no second round.
  An admin saving only their own settings (area, focus, sizes…) is not asked; only a shared value that actually
  changes counts. People and shared settings say "open Deal Hunter in a browser" to a phone (`browser_needed`).
- **Phones:** the app's own "a phone PIN needs a code in the sign-in" hook is gone. The framework's own pairing
  rules are 1114's.
- **AI:** new members start with 10 watches, 20 AI requests a day and $1 a month of the admin's paid AI (paid AI only
  runs when the member presses an AI button; the overall monthly limit still applies). Existing members are unchanged.

## Depends on
Framework 1.2.9 (item 1114) and the login service (1115) make `/auth/login?confirm=1` ask for the second factor.
Until then the link is an ordinary sign-in. Today Deal Hunter's sign-in rule on the login service decides whether the
shared sign-in already used the code; if it did, nobody is ever asked to confirm. Adopting 1.2.9 means swapping the
local `isOwner`, `isConfirmed`, `confirmHref` and `Fix` in `src/api.ts` for the framework's.

## Not changed
"only the admin can do that" and "you can't disable yourself" keep the Python app's words (the parity test records
them), so they say why but offer no button.

`store.adminId()` (whose AI members share, whose name pages show) is still the lowest-id admin. Making someone with a
lower id an admin would move that; today the owner's row is the oldest.

## Risk and rollback
Sign-in rules (higher). No migration. Rollback: put the previous image back.
