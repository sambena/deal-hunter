# Deal Hunter

A local dashboard that watches used-hardware listings for you. Add searches
("watches") or list your machines and let it suggest drop-in upgrades, then
check back later. Everything runs on your PC and stores data in `data/`.

## Run

Double-click `start.bat`, or run `python server.py`, then open
http://localhost:8780. It checks every watch every 15 minutes (configurable)
while it's running. No packages to install, unless you turn on central login (below).

To reach it from your phone, copy `config.example.json` to `config.json` and
set `"host": "0.0.0.0"`. There's no login, so only do that on your home network.

`config.json` can also hold `"ollama_url"`, this install's Ollama address (the default is
`http://localhost:11434`), so a home address never has to be in the code.

## Sources

| Source | Key | Notes |
|---|---|---|
| eBay | Free: developer.ebay.com, create a Production keyset, paste the App ID and Cert ID in Settings | Official Browse API; newest listings first |
| eBay local pickup | Same eBay key | Listings you can pick up within your radius (Settings > Local area: ZIP + miles). Shown as "eBay local pickup" |
| KSL Classifieds | None | Utah classifieds near your ZIP (Settings > Local area) |
| Craigslist | None | Classifieds near your ZIP, anywhere in the US; listings outside your radius are dropped |
| OfferUp | None | Listings near your ZIP, anywhere in the US; OfferUp's radius tops out at 50 miles |
| Poshmark | None | Clothes and shoes in your department and size, shipped (clothing watches only) |
| KSL Cars | None | Utah used cars and trucks, dealers and private sellers (car and truck watches only) |
| Reddit | None | r/hardwareswap and r/homelabsales by default, via RSS. Prices are guessed from the post text |
| Best Buy open-box | Free: developer.bestbuy.com | Experimental |

Facebook Marketplace and Amazon aren't supported: Marketplace has no API and
blocks scrapers, and Amazon's API needs an affiliate account.

When several watches (yours or friends') run the same search, each check sends it
once and every watch gets the answer, so more people doesn't mean more eBay calls
(5000 a day) or more KSL requests. Hover "Last check" to see how many were shared.

## Cars and trucks

Pick "A car or truck" in the watch form: query = make and model (`toyota tacoma`), plus model years and
max miles. Vehicle watches search eBay Motors (Cars & Trucks), Craigslist cars+trucks, OfferUp vehicles
and KSL Cars (Utah). Finds show the year, miles and any salvage/rebuilt title; "% under typical" compares with
the watch's finds within two model years. Ask AI gives a second opinion on price, miles and red flags.
Body styles work as searches: `pickup` (or truck, suv, van, minivan, sedan, coupe, convertible,
hatchback, wagon) uses KSL's and Craigslist's own body-style filters, eBay searches the common models of
that style, and titles count if they name one (F-150, Silverado, Tacoma...). KSL Cars reads up to 5
pages (about 100 newest listings) and eBay up to 200 a check.
Cars.com, Autotrader, CarGurus, Carvana, CarMax, TrueCar and Facebook block programs, so they aren't searched.

## How matching works

- Query words must all be in the title; `a|b` means either; `"phrase"`; `-word` excludes.
- Case, dashes and spaces are ignored for model numbers (`10980xe` = "i9-10980 XE").
- Junk words ("for parts", "bent pins", ...) are always filtered; edit them in Settings.
- Price limits include shipping.
- Finds are grouped by watch (toggle on the Finds tab; click a heading to fold it). Each watch has a
  colour, automatic or picked in the watch form, shared with the Android app.
- "Keep only the cheapest" (3/5/10/20) on a watch keeps that many cheapest finds; the rest are hidden
  and never alert. Starred finds always stay. Hidden ones come back if a cheaper find goes.
- After a watch has seen 5+ prices, new finds are marked "good" (10%+ under the
  typical asking price) or "great" (25%+).

## My Stuff

Add a machine's parts and press **Find upgrades**. Built-in rules recognise
desktop platforms (X99, X299, Intel 100–800 series, AM4, AM5) from the
motherboard chipset or CPU and suggest better drop-in CPUs and RAM kits.

Vehicles in My Stuff take a VIN and odometer: **Look up** fills in make, model, year and trim from
NHTSA's free VIN decoder, **Recalls** lists NHTSA safety recalls for that model year, and **Find parts** starts a watch linked to the
vehicle: its eBay searches only show parts eBay lists as fitting that year/make/model.

Household things (kinds Appliance: fridge, stove, washer…; Kitchen: blender, microwave, coffee maker…)
and most other gear also get **Recalls**, from the Consumer Product Safety Commission's free database: it
searches the brand's recalls and keeps the ones naming your model ("Your model") or the same kind of
product ("check your model": compare the model number on the recall page).

## Clothes and shoes

Pick "Clothes or shoes" in the watch form: who it's for (men, women, kids) and a size ("10.5", "M",
"32x30"). Save **My sizes** in Settings once and the form fills them in with one click. Poshmark is
searched in that department and size; eBay, Slickdeals and local listings are matched by the size in the
title: another size is dropped, no size shown keeps it as "size not listed". Depop, thredUP and Mercari
block programs, so they aren't searched.

## Focus

The picker at the top (Everything / Tech / Cars & trucks / Home & gear / Clothes & shoes) is saved to each person's account.
It shows only that category's watches, finds and My Stuff, starts new watches in it (Cars & trucks starts a
car or truck watch), and changes the colour and names ("My Garage" for cars). A watch's category is
automatic (car watches are cars; a watch for one of your things takes that thing's category, so parts for
your truck are cars; anything else is tech) unless you pick one in the watch form.

## Admin and members

The first account is the admin. The **Admin** tab (admin only) holds People (invites, limits), how often
checks run, every source's switch and keys, Home Assistant and the API key. Everyone else's **Settings**
has only their own: account, junk words, ZIP and radius, Discord, and their AI (shared or their own key).
The server enforces this too: members can't read or change the shared settings or import from Home Assistant.

## AI (optional, off by default)

Settings > AI. It only runs when you press an AI button:
**Suggest watches** (describe what you want), **Find upgrades with AI**
(covers GPUs, storage and anything the rules don't know), and **Ask AI** on a listing.

Providers: **Claude**, **OpenAI**, **Google Gemini** (API keys, pay per use) and **Ollama** (free, local).
Each section in Settings has step-by-step key instructions, and every model shows its price.

Spending is capped:
- Before each paid request the page shows the expected and maximum cost and asks first (can be turned off).
- **Monthly limit** (default $5): a request is refused if its worst case could pass it. $0 turns paid AI off.
- What each request actually cost is recorded, and Settings shows this month's total.
- Each button has an output cap, which also caps its worst-case cost.

Prices live in `ai.py` (`MODELS`), checked 2026-10-04. A model with no known price can't be used.
A Claude Pro/Max or ChatGPT subscription can't be used here; these need API keys.

The admin's AI comes from the owner console's shared settings file (`COUGAR_AI_SETTINGS`). Its entry can pin a
`model`, or give a `tier` and an `effort`:
- `cheapest` (the default) is the provider's lowest-priced model in `MODELS`. `balanced` and `best` are the newest
  Sonnet or Opus, Gemini Flash or Pro. OpenAI's models here are in neither family, so those tiers give its cheapest.
- A pinned model wins over the tier. A model with no price turns the admin's AI off.
- `effort` (`low`, `medium`, `high`) is sent only to models that take one; without it, requests ask for `low`.

**AI review** (Finds, when AI is on) looks at the finds shown (up to 40) in one request: say anything
specific in the dialog (or leave it blank), pick which AI, and choose whether to hide the bad deals (to
Dismissed; Undo brings them back; starred finds are never hidden). Each find gets a verdict and reason, and
the AI can suggest tightening a watch (exclude words, a price limit, a minimum year, a mileage cap) that you
apply with one click.

A new watch is searched as soon as it's saved. If a watch finds nothing, it stays open with what each
site returned on its last check (e.g. "eBay: 40 returned, 0 kept (38 over the max price)"), and **✨ Improve
with AI** on the watch form suggests a better search (query, excludes, limits, sites) with a reason; it
fills the form, and nothing changes until you save.

## Discord

Settings > Discord: paste a channel webhook URL and tick the toggle. By default it
only posts deals, and never posts the first batch a new watch finds.

## Central login (optional)

Deal Hunter can also sign people in through a Cougar Cave login service (OpenID Connect with PKCE). It is
off unless `config.json` turns it on, and the app's own email-and-password sign-in keeps working beside it.
It needs the pinned packages: `pip install -r requirements.txt`.

```json
"oidc": {
  "enabled": true,
  "issuer": "https://login.example.test/application/o/your-app/",
  "client_id": "your-client-id",
  "callback": "https://deals.example.test/auth/callback",
  "authorize_url": "https://login.example.test/application/o/authorize/",
  "token_url": "https://login.example.test/application/o/token/",
  "jwks_url": "https://login.example.test/application/o/your-app/jwks/",
  "client_secret_file": "/path/to/a/private/file"
}
```

The issuer, its endpoints and the callback must be HTTPS, with the callback exactly `/auth/callback` on this
site. The client secret lives only in that file, readable by its owner only (`chmod 600` on Linux). Relative
paths are relative to Deal Hunter's folder. Login transactions are kept in `data/oidc/` (or `"tx_db"`). On
Linux that folder must be owned by the user running Deal Hunter, with mode 700. Missing or unsafe settings stop
the server at start-up.

Nobody gets in just by having a central account: the owner links each one to an existing account here by
its exact central user id, never by email, with `python central_admin.py link ACCOUNT SUBJECT`. Admins also
need a sign-in that used both their password and their authenticator code. Run `python central_admin.py` for
every command.

To roll back, run `python central_admin.py central off`. It ends every central session at once and refuses
new ones, keeps the links, and `central on` restores sign-in without reviving anyone who was disabled.
Setting `"enabled": false` and restarting also refuses every central session, but only while it stays off:
the sessions are not deleted, so turning it back on lets them in again. To end them for good, use `central
off`. Before going back to a Deal
Hunter version without central login, run `central off` first: older versions don't know which sessions came
from central sign-in.

## Tests

`python -m unittest discover tests` (the central-login tests need `pip install -r requirements.txt`)

## License

GPL-3.0. See [LICENSE](LICENSE).
