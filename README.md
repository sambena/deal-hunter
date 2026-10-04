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
| Reddit | None | r/hardwareswap and r/homelabsales by default, via RSS. Prices are guessed from the post text |
| Best Buy open-box | Free: developer.bestbuy.com | Experimental |

Facebook Marketplace and Amazon aren't supported: Marketplace has no API and
blocks scrapers, and Amazon's API needs an affiliate account.

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

- **Ollama**: free and local. Install from ollama.com, `ollama pull qwen3:8b`.
- **Claude**: `pip install anthropic`, add an API key. Costs a few cents per use.

## Discord

Settings > Discord: paste a channel webhook URL and tick the toggle. By default it
only posts deals, and never posts the first batch a new watch finds.

## Tests

`python -m unittest discover tests`

## License

GPL-3.0. See [LICENSE](LICENSE).
