# 1084 Deal Hunter: framework server 1.2.8, so Sign out ends the central sign-in and returns to Deal Hunter

Task, medium. The Deal Hunter side of framework bug 1078 (cougar-cave-ui PR 74). Den did the same in sambena/den PR 33 (1082).

## Outcome

Clicking Sign out in Deal Hunter ends Deal Hunter's session and the sign-in at the login service, and the browser comes back to Deal Hunter
signed out. Before, the login service answered "Bad Request" and stayed signed in.

## What changes

`site/vendor/cougarcave-server-1.2.2.tgz` becomes `cougarcave-server-1.2.8.tgz`, the same file Den has (built from cougar-cave-ui
main 086f112). No app code changes; the UI package is left as it was.

1.2.8 keeps the sign-in's ID token with the session (a new nullable column `cave_sessions.id_token` in Deal Hunter's schema,
added at start) and sends it as `id_token_hint` at sign-out. The bump also brings what Deal Hunter had not taken yet:

- 1.2.3: a late logout notice no longer ends a sign-in made since (#905).
- 1.2.4 also has #957: `createApp` refuses an invalid `aiSpendView` option before opening storage. Deal Hunter passes
  `aiSpendView: 'app'`, which is valid.
- 1.2.4: the owner's overall AI limit in the framework's AI helper (#925). Deal Hunter keeps its own AI ledger
  (`aiSpendView: 'app'`) and already checks the overall limit itself, so nothing changes in what it spends. The
  framework now makes `core.ai_month_total` in the local test database at start, so the one test that needs it
  missing drops it first (test only).
- 1.2.5, 1.2.6: AI tool calls (Deal Hunter doesn't offer tools).
- 1.2.7: a reauth reuses a fresh shared sign-in instead of always showing the form.

## Acceptance

- Sign out on the live Deal Hunter: the login service answers its end-session address with a redirect (not 400), the
  browser ends on Deal Hunter, signed out, and Deal Hunter's audit has a `logout_backchannel` row.
- A session made before the deploy still signs out of Deal Hunter; that one time the person stays on the login service's
  signed-out page.

## Risk and rollback

One new nullable column. Rollback: put the previous image back; the older framework never reads the column.
