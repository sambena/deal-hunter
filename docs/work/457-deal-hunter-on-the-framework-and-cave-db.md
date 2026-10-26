# #457 Deal Hunter on the framework and cave-db

Owner: dexter (worker). Review, merge and deploy: cougar-reviewer. Deal Hunter is the last app to convert, after
RaidRoster's website and Player, one app at a time. Pattern followed: RaidRoster #563 (brief, data copy with checker,
API port compared against the Python output).

## Outcome
Deal Hunter runs on the Cougar Cave web framework (`@cougarcave/server` 1.0.5, `@cougarcave/ui`).
- Its data lives in the `deals` schema in cave-db.
- People sign in only through Cougar Cave central login and see the shared bar.
- Every watch, find, device, setting and AI record people have today is still there afterwards, unchanged.
- Everything else works as it does today. The only change is the account system.

## Starting evidence (read 2026-10-26, deal-hunter main e9e3b0b)
- **One Python app, about 7,000 lines, stdlib HTTP server** (`server.py`, 1,494 lines):
  - Web port (config `port`): the page, `/api/*`, central login routes, `/auth/devices/*`, `/download/android`.
  - API port (config `api_port`, 8781 on the server): `/api/*` only, Bearer credentials only, no cookies.
    `/api/settings`, `/api/api-key` and `/api/test-discord` are blocked there.
- **Data: one SQLite file**, `data/deal-hunter.db` (`db.py`), migrated in place by `PRAGMA user_version` (now 9).
  Tables: `settings`, `watches`, `listings`, `ai_usage`, `machines`, `users`, `user_settings`, `tokens`, `invites`,
  `central_identities`, `central_state`, `central_link_states`, `central_logout_jti`, `central_logout_attempts`,
  `central_audit`, `pair_codes`, `paired_devices`.
  - Other files in `data/`: `device.key` (own sign-in's "seen this browser" mark), `oidc/` (login transactions),
    `downloads/` (the Android APK and `android.json`, or config `downloads_dir`).
  - Secrets (eBay, Best Buy, Discord webhooks, people's own AI keys, the Home Assistant token) are stored in
    `settings` and `user_settings` and only ever sent to the page as true/false.
- **Accounts today: two systems side by side.**
  - The app's own: email and password (scrypt), first-admin setup, invite and reset links, browser sessions and
    device tokens (`dht_`), the old single API key (acts as the first admin on the API port), and the People admin
    screen (invites, limits, disable, reset).
  - Central login (#322 to #324, #411, #412): an owner links a central subject to an account
    (`central_admin.py link`); admins need both factors (`amr` has `pwd` and `mfa`) to sign in at all, and a privileged
    change needs a sign-in from the last 5 minutes. Back-channel logout ends only the token's `sid` (#551).
  - Whether the own sign-in is switched off on the live server (`central_state.own_login`) is not known from the repo.
- **Per-person limits that are not accounts:** `users.role` (admin or member), `disabled`, `watch_limit` (10 for
  invited friends, none for the admin), `ai_shared`, `ai_allowance` (US $ a month of the admin's paid AI),
  `ai_daily_cap` (requests a day on the admin's AI).
- **Settings:** shared ones (the admin's: sources and their keys, check schedule, Reddit subs, Home Assistant) in
  `settings`; each person's own (ZIP and radius, junk words, Discord, their AI, focus, sizes) in `user_settings`.
  `MEMBER_KEYS` and `MEMBER_READS` say what a member may change and see.
- **Sources** (`sources.py`): eBay and eBay local pickup (Browse API), KSL Classifieds, KSL Cars, Craigslist, OfferUp,
  Poshmark, Reddit (RSS), Slickdeals, r/buildapcsales, Best Buy open-box. Each has its own gaps between requests.
- **Matching and deals** (`matching.py`, `vehicles.py`, `hardware.py`, `specs.py`): query syntax, junk words,
  size and vehicle rules, "good"/"great" deal marks after 5+ prices, keep-only-the-cheapest, price history.
- **The schedule** (`poller.py`): one in-process thread. Every `poll_minutes` (default 15), or at the day's set times
  in a time zone; a restart on set times waits for the next one. One pass checks every enabled watch with its owner's
  settings, shares identical fetches across watches (`fetch_key`), posts Discord alerts, and purges Best Buy finds
  after 72 hours. "Check now" for one watch, one person's watches, or (admin) everyone's.
- **AI** (`ai.py`, `shared_ai.py`): Claude, OpenAI, Gemini and Ollama; estimate-and-confirm before each paid call;
  monthly limit; each call recorded in `ai_usage` with who paid. The admin's AI comes from the owner console's shared
  settings file (app `deals`, with tier and effort, #494, #517). Members use the admin's AI on their allowance and daily
  cap, or their own key.
- **Other outside calls:** NHTSA (VIN decode, vehicle recalls), CPSC (product recalls), Home Assistant (device import,
  admin only), Discord webhooks. `netguard.py` checks every address a person types.
- **Phone pairing** (`pairing.py`, #541, #551): the pairing contract v1, `dhp_` credentials on both ports.
- **The page:** `static/index.html` and `static/app.js` (about 120 KB of plain HTML and JS), `login.html`,
  `link.html`, `api.html` (API docs), and the vendored Python-era kit and shared bar under `static/cave/` (#540).
- **Tests:** 49 files, 374 `unittest` tests.
- **The Android app** (`deal-hunter-android`) signs in with email and password (`/api/auth/login`) or the old API key.
  It does not pair through `/auth/devices` and does not send `X-Cave-Request`.

## Decisions (proposed for cougar-reviewer, all within port-as-is)
1. **Port as-is, with one login system (Sam's rule).** Every route and feature stays except the app's own accounts.
   - Retired: email and password, first-admin setup, invite and reset links (and their admin screen), the app's own
     browser sessions and device tokens, the old single API key and its Admin > API screen, "change my email or
     password", the "where you're signed in" token list, and self-service linking (#323) with `central_admin.py`.
   - Replaced by the framework: sign-in, sign-out, sessions, back-channel logout, phone pairing.
   - Kept: everything else, the People screen's limits included (decision 4).
2. **Where the new app lives.** A stamped framework app under `site/` in this repository (own `package.json`,
   Dockerfile, tests), like RaidRoster. The Python app stays at the root, untouched, until the cutover; a last PR
   removes it after the restorable week.
3. **Everything moves to TypeScript, one runtime.** The scrapers, matching, scheduler and AI move to Node in the same
   container. Each piece is tested against the Python output on the same saved fixtures before the Python is retired.
   - The alternative, keeping the scrapers in a Python worker that writes to cave-db, would leave two runtimes and
     two images. Not proposed.
4. **People.** A person is their central subject. `deals.members(id, subject, admin, disabled, watch_limit,
   ai_shared, ai_allowance, ai_daily_cap)` keeps today's values and today's account id (the API's `id`); names and
   emails come from `core.people`.
   - Admin: `members.admin`, copied from `users.role`. The app's admin is also listed in `OWNERS` for the
     framework's owner role. Proposed: the app checks `members.admin`, not `OWNERS`, so the copy decides.
   - Disabling someone also ends their sessions and phones at once, as today.
   - Who may sign in: the login service's access list for Deal Hunter. Someone with access and no member row gets one
     on first sign-in with today's invite defaults (`watch_limit` 10, `ai_shared` on, allowance 0, cap 20).
   - Admin factors: today an admin sign-in without both factors is refused. The framework can't refuse a sign-in, so
     an admin session without both factors is refused on every `/api` route with today's message. Open: or treated as
     a member (RaidRoster's later choice). Port-as-is says refuse.
   - Privileged actions (shared settings, Home Assistant import, People) keep "both factors, and a sign-in from the
     last 5 minutes for a change", with the framework's `bothFactors` and `isFresh`.
5. **Phones and sessions start again once.** The framework keeps sessions and phones in its own `cave.db`, with
   `cave_` credentials. Existing browser sessions, device tokens and `dhp_` phones are not copied; everyone signs in
   once and pairs a phone again. The copy reports how many there were, for the record.
6. **The API port 8781 stays.** A second listener in the same process serving only `/api/*`, with paired-phone
   credentials only (no cookies), the same three paths blocked, and the same JSON shapes as today.
   - **Open, needs cougar-reviewer:** the framework requires `X-Cave-Request: 1` on every write, and today's
     Android app sends neither that header nor a paired credential. After cutover it can't sign in until it pairs.
     Proposed: an Android item in `deal-hunter-android` (pair through `/auth/devices`, send the header) that ships
     before the cutover. A framework exemption for Bearer writes is the alternative; not proposed (framework change).
   - How the second listener is made (a second Fastify instance sharing the routes and store, or a framework
     option) is settled in the API PR, without framework changes if possible.
7. **AI.** Deal Hunter's own AI module is ported as it is: four providers, people's own keys and Ollama, estimate
   and confirm, allowances and daily caps, the `ai_usage` ledger in the `deals` schema. The admin's entry is read with
   the framework's `aiSettings('deals', AI_SETTINGS_FILE)`, same file, same app key.
   - The framework's AI helper is not used: it has no own keys, no Ollama and no per-person allowances.
   - Tests use a stand-in provider; no test calls a paid API.
8. **The schedule is the existing job, ported.** One in-process loop with the same rules: interval or set times in
   a time zone, restart behaviour, shared fetches, the 72-hour Best Buy purge, and the per-source gaps. No new jobs,
   timers or polling.
9. **The page moves to the kit.** Lit components and the shared bar from `@cougarcave/ui`, every tab and dialog
   kept (Finds, Watches, My Stuff, Settings, Admin, Focus themes, AI dialogs, Android download, Add a phone).
   `login.html`, `link.html` and the vendored `static/cave/` go (the framework serves sign-in and `/cave/`).
   `api.html` stays as the API page, updated to say how to pair. The page policy needs `img-src https:` for listing
   pictures from the shops; added with the framework's `policy.extend`.
10. **The Android download stays.** `GET /api/app/android` and `/download/android` for signed-in people, from the
    same downloads folder (a read-only mount). The invite-code way in goes with invites.

## Data mapping (proposed)
| Today (SQLite) | In cave-db (`deals` schema) |
| --- | --- |
| `users` linked through `central_identities` (active) | `members(id, subject uuid, admin, disabled, watch_limit, ai_shared, ai_allowance, ai_daily_cap)` |
| `settings` | `settings(key, value jsonb)` |
| `user_settings` | `user_settings(member_id, key, value jsonb)` |
| `watches` | `watches(id, member_id, ...)` every column, same ids |
| `listings` | `listings(id, watch_id, ...)` every column, same ids |
| `machines` | `machines(id, member_id, ...)` every column, same ids |
| `ai_usage` | `ai_usage(id, member_id, paid_by, ...)` every column, same ids |
| `tokens`, `invites`, `pair_codes`, `paired_devices`, `central_*` | retired (framework sessions and phones); counts reported; `central_audit` exported to a file kept with the backup |
| `device.key`, `oidc/` | retired |
| `downloads/` | stays files on a read-only mount |

- Ids are kept, so the Android app's cached ids and links stay valid. Sequences restart after the highest id.
- Times stay epoch seconds (`double precision`), so the API's numbers are byte-for-byte the same.
- JSON columns stored as text today (`exclude`, `sources`, `parts`, `custom`, `last_check`) become `jsonb`; the
  checker compares them parsed.
- The one-time copy opens `deal-hunter.db` read-only. An account with no active central link is listed by id and
  name for cougar-reviewer to decide (link, or leave its rows out) and is never dropped silently; with any unlinked
  account owning rows, the copy stops unless told what to do with each.
- Migrations never contain `CREATE SCHEMA` (Add app makes the schema), guarded by a test.

## Routes (kept, same paths and JSON)
- **Watches and finds:** `/api/state`, `/api/watches` (create, edit, delete, run, history), `/api/poll`,
  `/api/listings` (list, star/dismiss/seen, mark-seen, ask-ai).
- **My Stuff:** `/api/machines` (create, edit, delete, recalls, suggest, specs), `/api/vin`, `/api/specs/commands`,
  `/api/import/ha` (admin).
- **AI:** `/api/ai/estimate`, `/api/ai/choices`, `/api/ai/review`, `/api/ai/tune-watch`, `/api/ai/usage`,
  `/api/ai/draft-watches`, `/api/radar`.
- **Settings:** `PUT /api/settings` (members their own keys only), `/api/test-discord`, `GET /api/me`.
- **People (admin):** `GET /api/admin/users` (without invites), `PUT /api/admin/users/:id` (disable, watch limit,
  AI allowed, allowance and daily cap).
- **Android:** `/api/app/android`, `/download/android`.
- **From the framework:** `/auth/login`, `/auth/callback`, `/auth/signout`, `/auth/logout`, `/auth/backchannel`,
  `/auth/me`, `/auth/devices/*`, `/cave/`, `/healthz`.
- **Retired (410 or 404):** `/api/auth/*`, `POST /api/me` email and password, `/api/tokens`, `/api/admin/invites`,
  `/api/admin/users/:id/reset`, `/api/api-key`, `/auth/central`, `/auth/link`.

## Acceptance
1. **Every record survives.** For each table in the mapping, every row and column matches between the old SQLite file
   and cave-db, compared by a checker that exits nonzero on any difference. A self-check plants one changed value, one
   missing row and one extra row, and the checker must fail on each.
2. **The API answers as today.** For the same fixture data, each kept route's JSON matches the Python app's output
   (recorded from the Python code on made-up data), apart from the account fields that change by design.
3. **People's data stays theirs.** A person can't read or change another person's watches, finds, devices, settings
   or AI usage, tested over real HTTP beside the owner's own working requests. A member can't read or change shared
   settings, People or the Home Assistant import.
4. **Privileged actions** need an admin with both factors, and a sign-in from the last 5 minutes for a change;
   refused otherwise, each beside a valid control, with break-checks for the important ones.
5. **Sources and matching** give the same finds as the Python code on the same saved, made-up pages for every source.
   No test calls a real shop, NHTSA, CPSC, Discord, Home Assistant or a paid AI.
6. **The schedule** runs on the same rules: interval, set times with a time zone, restart behaviour, shared fetches,
   the Best Buy purge. Unit tests with a fake clock.
7. **AI** keeps estimate and confirm, the monthly limit, allowances and daily caps, own keys, and the owner console's
   entry with tier and effort; tested with a stand-in provider.
8. **Phones:** pairing, rotation and sid-scoped back-channel logout work on both ports (the framework's contract),
   and a paired phone works on port 8781.
9. **The page** carries every current feature, checked in the browser on desktop and at 375 px, light and dark,
   signed in and out, as admin and as a member.
10. **Every PR:** `npm run typecheck`, tests and build clean. Nothing private in code, tests, docs or PR text.
11. **Restore and rollback:** the restore drill shows the `deals` tables; the old container and volume stay restorable
    for one week.

## Delivery
- **The brief:** this PR, reviewed first. No code until it is merged.
- **The code:** one branch and PR each, in order:
  1. the stamped app under `site/` and the `deals` schema;
  2. the one-time copy and the checker, with the planted-difference self-check;
  3. the pure logic (matching, vehicles, hardware, specs) against Python output;
  4. the API (both ports) against Python output;
  5. the sources and the schedule;
  6. AI;
  7. the page on the kit (may split by tab).
- **Outside this repository:** the Android pairing item (decision 6), before cutover.
- **Live steps (cougar-reviewer's):** Add app for `deals` and the access list, deploy, the copy and checker on the real
  data, cutover. Before the switch, a backup of `data/`. Rollback is the old container on the old volume.

## Open for cougar-reviewer
- Decision 4: an admin without both factors refused (proposed, as today) or treated as a member.
- Decision 6: the Android app must learn pairing and `X-Cave-Request` before cutover; who takes that item.
- The copy: what to do with accounts that have no active central link, once the real counts are known.
- Decision 2: removing the Python app after the restorable week, in this item or a follow-up.

## Settled (cougar-reviewer, 2026-10-26, on PR #83)
1. An admin session without both factors is refused on every `/api` route, as today.
2. The Android pairing and header are deal-hunter-android #537 (its PR #49). The cutover waits on it; Sam decides that
   step. Nothing in the web PRs waits on it, and this item doesn't touch the Android repository.
3. The copy stops and lists accounts with no active central link; cougar-reviewer settles them at cutover.
4. Removing the Python app is a follow-up item after the restorable week.
- The one-time sign-in and re-pairing are accepted. Port 8781 stays as a second listener for paired-phone credentials
  only.

## Handoff
The brief is merged. Delivery step 1 (the stamped app and the `deals` schema) is the next PR.
