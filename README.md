# Deal Hunter

A local dashboard that watches used-hardware listings for you. Add searches
("watches") or list your machines and let it suggest drop-in upgrades, then
check back later. Everything runs on your PC and stores data in `data/`.

## Run

Double-click `start.bat`, or run `python server.py`, then open
http://localhost:8780. It checks every watch every 15 minutes (configurable)
while it's running. No packages to install.

To reach it from your phone, copy `config.example.json` to `config.json` and
set `"host": "0.0.0.0"`. There's no login, so only do that on your home network.

## Sources

| Source | Key | Notes |
|---|---|---|
| eBay | Free: developer.ebay.com, create a Production keyset, paste the App ID and Cert ID in Settings | Official Browse API; newest listings first |
| eBay local pickup | Same eBay key | Listings you can pick up within your radius (Settings > Local area: ZIP + miles). Shown as "eBay local pickup" |
| KSL Classifieds | None | Utah classifieds near your ZIP (Settings > Local area) |
| Craigslist | None | Classifieds near your ZIP, anywhere in the US; listings outside your radius are dropped |
| Reddit | None | r/hardwareswap and r/homelabsales by default, via RSS. Prices are guessed from the post text |
| Best Buy open-box | Free: developer.bestbuy.com | Experimental |

Facebook Marketplace and Amazon aren't supported: Marketplace has no API and
blocks scrapers, and Amazon's API needs an affiliate account.

When several watches (yours or friends') run the same search, each check sends it
once and every watch gets the answer, so more people doesn't mean more eBay calls
(5000 a day) or more KSL requests. Hover "Last check" to see how many were shared.

## How matching works

- Query words must all be in the title; `a|b` means either; `"phrase"`; `-word` excludes.
- Case, dashes and spaces are ignored for model numbers (`10980xe` = "i9-10980 XE").
- Junk words ("for parts", "bent pins", ...) are always filtered; edit them in Settings.
- Price limits include shipping.
- After a watch has seen 5+ prices, new finds are marked "good" (10%+ under the
  typical asking price) or "great" (25%+).

## My hardware

Add a machine's parts and press **Find upgrades**. Built-in rules recognise
desktop platforms (X99, X299, Intel 100–800 series, AM4, AM5) from the
motherboard chipset or CPU and suggest better drop-in CPUs and RAM kits.

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

## Discord

Settings > Discord: paste a channel webhook URL and tick the toggle. By default it
only posts deals, and never posts the first batch a new watch finds.

## Tests

`python -m unittest discover tests`

## License

GPL-3.0. See [LICENSE](LICENSE).
