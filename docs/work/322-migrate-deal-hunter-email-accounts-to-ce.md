# #322 Switch Deal Hunter to central login and turn off its own login

Status: todo. Rewritten 2026-10-25 by dexter-453-design-review-078ce7 for the one-step switch-over, at the owner's request (docs only). This document authorizes no code, account, network or deployment change. This repository is public: keep internal addresses, client IDs and secret paths out of commits.

## The model this follows
cougar-auth `docs/integration/central-login-app-plans.md` (section "Deal Hunter") and `docs/work/313-*.md`:
- One switch: central login on, Deal Hunter's own email-and-password login off. No coexistence window.
- The old login stays restorable for 1 week, then its passwords and tokens are deleted.
- The owner matches an existing Deal Hunter user to a central account by `(issuer, subject)`, never by email. There is no self-service linking.
- Back up and restore-test a copy first. A ban or removal survives a rollback.
- The switch waits for the login-service foundation (cougar-auth #442).

## Outcome
Deal Hunter signs people in only through central login. The owner's saved deals, preferences and role stay on the same Deal Hunter user ID. The one other existing account, which is unused, is deleted at the switch; that person can be invited fresh later.

## What to do
1. **Before the switch**
   - Owner link: the owner links the owner's Deal Hunter user to the owner's central account by subject.
   - Phone: Deal Hunter Android moves off its in-app password form first (deal-hunter-android #325 and #326).
   - Backup: take a backup that restores user IDs, saved deals, invites and bans, and restore-test it on a copy.
2. **Switch**
   - Turn central login on and the password login off (`/api/auth/login` and the web sign-in form refuse; sessions created by the old login are ended).
   - Turn off the self-service account linking page (`/auth/link`, from #323). Under the owner's decision, linking is done by the owner, not by the person.
   - Delete the unused account.
   - Keep the old password hashes and tokens unchanged, so the week below can bring the old login back.
3. **Restorable week**
   - Rollback is central off and the old login back on. A person removed or banned after the backup stays refused, and the subject mapping stays.
4. **After a week with no rollback**
   - Delete the old password hashes, reset/invite codes and device tokens issued by the old login.
   - Record the revisions and the deletion time on this item. Device tokens issued after a central sign-in stay.

## Prerequisites (real)
- cougar-auth #442 done.
- Deal Hunter Android off the password form (deal-hunter-android #325, #326).
- Back-channel logout in place (#411).
- The owner's go-ahead for switch day.

## Acceptance (valid control beside each rejection)
- After the switch, the old email-and-password sign-in is refused while the owner signs in through central login and still sees the same saved deals.
- `/auth/link` no longer links an account.
- A central account with no Deal Hunter grant is refused.
- The unused account is gone.
- A rollback rehearsal on a copy keeps a person removed after the backup refused and keeps the owner's mapping row.
- After the week, the old secrets are deleted and central sign-in still works.

## Not doing
- #323 (self-service linking) stays done; it is not the switch path.
- #324 (a dated compatibility window) stays dropped.

## Delivery and boundaries
Implementation, review, merge, deployment and live verification are separate stages; do not close early. The earlier plan for this item (migration playbook with a coexistence rollout) is in git history.
