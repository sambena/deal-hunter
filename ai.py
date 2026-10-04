"""Optional AI helpers. Only runs when Settings > AI is not "off", and only when
you press an AI button, so it never spends anything in the background.

Every paid request goes through one gate:
  1. estimate the cost from the prompt size and the model's price,
  2. refuse if the worst case would push this month's spend past the limit,
  3. call the provider and record what it actually billed.

Providers:
  claude  Anthropic API, needs `pip install anthropic` and an API key
  openai  OpenAI API key
  gemini  Google Gemini API key
  ollama  free, local (https://ollama.com)
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

import db


class AIError(Exception):
    pass


# US dollars per million tokens, checked 2026-10-04. Thinking/reasoning tokens bill as output.
# `effort`: the model takes a reasoning-effort setting (we always ask for the lowest useful one).
MODELS = [
    {"provider": "claude", "id": "claude-opus-5-5", "label": "Claude Opus 5.5 (best)", "in": 4.00, "out": 20.00, "effort": True},
    {"provider": "claude", "id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "in": 2.00, "out": 10.00, "effort": True},
    {"provider": "claude", "id": "claude-haiku-4-5", "label": "Claude Haiku 4.5 (cheapest)", "in": 1.00, "out": 5.00, "effort": False},
    {"provider": "claude", "id": "claude-opus-5", "label": "Claude Opus 5 (older)", "in": 5.00, "out": 25.00, "effort": True},
    {"provider": "openai", "id": "gpt-6-astra", "label": "GPT-6 Astra (best)", "in": 10.00, "out": 50.00, "effort": True},
    {"provider": "openai", "id": "gpt-6.1-sol", "label": "GPT-6.1 Sol", "in": 2.00, "out": 10.00, "effort": True},
    {"provider": "openai", "id": "gpt-6-luna", "label": "GPT-6 Luna (cheapest)", "in": 0.10, "out": 0.50, "effort": True},
    {"provider": "gemini", "id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro (best, preview)", "in": 2.00, "out": 12.00, "effort": True},
    # Half price until the end of 2026, then $1.50 / $7.50.
    {"provider": "gemini", "id": "gemini-3.8-flash", "label": "Gemini 3.8 Flash", "in": 1.50, "out": 7.50, "effort": True,
     "promo": {"until": "2027-01-01", "in": 0.75, "out": 3.75}},
    {"provider": "gemini", "id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite (cheapest)", "in": 0.30, "out": 2.50, "effort": True},
]

PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "key": "anthropic_api_key", "model": "claude_model"},
    "openai": {"label": "OpenAI (ChatGPT)", "key": "openai_api_key", "model": "openai_model"},
    "gemini": {"label": "Google Gemini", "key": "gemini_api_key", "model": "gemini_model"},
    "ollama": {"label": "Ollama (free, local)", "key": None, "model": "ollama_model"},
}

# Token allowances per button. `max_out` is a hard cap sent to the provider, so it also caps the cost.
ACTIONS = {
    # typical_out: first real Haiku answer was 60 tokens; 250 leaves room for models that think a little.
    "judge": {"label": "Ask AI", "typical_out": 250, "max_out": 3000},
    "draft": {"label": "Suggest watches", "typical_out": 2500, "max_out": 8000},
    "upgrades": {"label": "Find upgrades with AI", "typical_out": 3000, "max_out": 8000},
    "specs": {"label": "Read specs with AI", "typical_out": 300, "max_out": 2000},
}


# ---- cost and budget ----------------------------------------------------------

def model_info(settings: dict) -> dict:
    provider = settings.get("ai_provider", "off")
    if provider not in PROVIDERS:
        raise AIError("AI is off. Turn it on in Settings.")
    model = (settings.get(PROVIDERS[provider]["model"]) or "").strip()
    if provider == "ollama":
        return {"provider": provider, "id": model, "label": f"Ollama {model}", "in": 0.0, "out": 0.0,
                "effort": False, "free": True}
    for m in MODELS:
        if m["provider"] == provider and m["id"] == model:
            if provider == "gemini" and settings.get("gemini_free_tier"):
                # Google bills nothing on a key without billing; its replies don't say which tier, so the user does.
                return {**m, "label": f"{m['label']}, free tier", "in": 0.0, "out": 0.0, "free": True}
            return {**m, **_price_today(m), "free": False}
    raise AIError(f"Pick a model from the list in Settings > AI (\"{model}\" has no known price, "
                  "so its cost can't be capped).")


def _price_today(m: dict) -> dict:
    promo = m.get("promo")
    if promo and time.strftime("%Y-%m-%d") < promo["until"]:
        return {"in": promo["in"], "out": promo["out"]}
    return {"in": m["in"], "out": m["out"]}


def cost(info: dict, tokens_in: int, tokens_out: int) -> float:
    return (tokens_in * info["in"] + tokens_out * info["out"]) / 1_000_000


def estimate_input_tokens(prompt: str, schema: dict) -> int:
    # About 3.5 characters per token for English, plus the provider's own wrapping of the schema.
    # Deliberately on the high side.
    return int((len(prompt) + len(json.dumps(schema))) / 3.5) + 400


def month_start(now: float | None = None) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def budget(settings: dict) -> dict:
    spent = db.query("SELECT COALESCE(SUM(cost), 0) AS s FROM ai_usage WHERE at >= ?", (month_start(),))[0]["s"]
    limit = max(0.0, float(settings.get("ai_monthly_limit") or 0))
    return {"spent": round(spent, 6), "limit": limit, "remaining": round(max(0.0, limit - spent), 6)}


def estimate(settings: dict, action: str, prompt: str, schema: dict) -> dict:
    info = model_info(settings)
    a = ACTIONS[action]
    tokens_in = estimate_input_tokens(prompt, schema)
    typical, worst = cost(info, tokens_in, a["typical_out"]), cost(info, tokens_in, a["max_out"])
    b = budget(settings)
    allowed, reason = True, ""
    key_setting = PROVIDERS[info["provider"]]["key"]
    env_key = info["provider"] == "claude" and os.environ.get("ANTHROPIC_API_KEY")
    if key_setting and not (settings.get(key_setting) or env_key):
        allowed, reason = False, f"Add your {PROVIDERS[info['provider']]['label']} API key in Settings > AI first."
    elif not info["free"] and b["spent"] + worst > b["limit"]:
        allowed = False
        reason = (f"Monthly AI limit: ${b['spent']:.2f} of ${b['limit']:.2f} used, and this could cost up to "
                  f"${worst:.3f}. Raise the limit or pick a cheaper model in Settings > AI.")
    return {"action": action, "action_label": a["label"], "provider": info["provider"], "model": info["id"],
            "model_label": info["label"], "free": info["free"], "typical": round(typical, 5),
            "max": round(worst, 5), **b, "allowed": allowed, "reason": reason}


_call_lock = threading.Lock()  # one paid call at a time, so two clicks can't both slip under the limit


def run(settings: dict, action: str, prompt: str, schema: dict) -> dict:
    with _call_lock:
        est = estimate(settings, action, prompt, schema)
        if not est["allowed"]:
            raise AIError(est["reason"])
        info = model_info(settings)
        call = CALLERS[info["provider"]]
        text, tokens_in, tokens_out, problem = call(settings, info, prompt, schema, ACTIONS[action]["max_out"])
        # Record before parsing: a cut-off or refused answer is still billed.
        spent = 0.0 if info["free"] else cost(info, tokens_in, tokens_out)
        db.execute("""INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost)
                      VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (time.time(), info["provider"], info["id"], action, tokens_in, tokens_out, spent))
    if problem:
        raise AIError(problem)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError(f"{info['label']} returned an unreadable answer") from e


def usage_summary(settings: dict) -> dict:
    rows = db.query("""SELECT model, action, COUNT(*) AS n, SUM(cost) AS cost FROM ai_usage
                       WHERE at >= ? GROUP BY model, action ORDER BY cost DESC""", (month_start(),))
    return {**budget(settings), "breakdown": rows}


def catalog() -> dict:
    """Everything the Settings page needs to show models with their prices."""
    return {"providers": {k: v["label"] for k, v in PROVIDERS.items()},
            "models": [{"provider": m["provider"], "id": m["id"], "label": m["label"], **_price_today(m)}
                       for m in MODELS],
            "actions": ACTIONS}


# ---- providers ------------------------------------------------------------------
# Each returns (json text, input tokens, output tokens, problem or None).

def _claude(settings: dict, info: dict, prompt: str, schema: dict, max_out: int):
    try:
        import anthropic
    except ImportError as e:
        raise AIError("Claude mode needs the SDK: run  pip install anthropic") from e
    key = settings.get("anthropic_api_key") or None  # None -> SDK reads ANTHROPIC_API_KEY
    client = anthropic.Anthropic(api_key=key, timeout=180)
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if info["effort"]:
        output_config["effort"] = "low"
    # No server-side fallback: it can re-run a declined request on a pricier model, past the budget.
    try:
        response = client.messages.create(model=info["id"], max_tokens=max_out,
                                          messages=[{"role": "user", "content": prompt}],
                                          output_config=output_config)
    except anthropic.AuthenticationError as e:
        raise AIError("Claude API key is invalid") from e
    except anthropic.RateLimitError as e:
        raise AIError("Claude rate limit hit; try again in a minute") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Can't reach the Claude API") from e
    u = response.usage
    tokens_in = u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
    problem = None
    if response.stop_reason == "refusal":
        problem = "Claude declined this request"
    elif response.stop_reason == "max_tokens":
        problem = "Claude's answer was cut off; try again"
    text = next((b.text for b in response.content if b.type == "text"), "")
    return text, tokens_in, u.output_tokens, problem


def _ollama(settings: dict, info: dict, prompt: str, schema: dict, max_out: int):
    body = json.dumps({
        "model": info["id"],
        "messages": [{"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        # Thinking models (qwen3.5) otherwise spend the whole allowance thinking and return no answer.
        "think": False,
        "options": {"num_predict": max_out},
    }).encode()
    url = settings["ollama_url"].rstrip("/") + "/api/chat"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise AIError(f"Ollama returned HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except urllib.error.URLError as e:
        raise AIError(f"Can't reach Ollama at {settings['ollama_url']} ({e.reason}). Is it running?") from e
    text = (data.get("message") or {}).get("content", "")
    return text, data.get("prompt_eval_count", 0), data.get("eval_count", 0), None


def _post_json(url: str, headers: dict, body: dict, name: str) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = detail[:200]
        if e.code in (401, 403):
            raise AIError(f"{name} rejected the API key ({detail})") from e
        if e.code == 429:
            raise AIError(f"{name} rate limit or quota hit; try again later ({detail})") from e
        raise AIError(f"{name} API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise AIError(f"Can't reach the {name} API ({e.reason})") from e


def _require_key(settings: dict, provider: str, name: str) -> str:
    key = (settings.get(PROVIDERS[provider]["key"]) or "").strip()
    if not key:
        raise AIError(f"Add your {name} API key in Settings > AI")
    return key


def _openai(settings: dict, info: dict, prompt: str, schema: dict, max_out: int):
    key = _require_key(settings, "openai", "OpenAI")
    data = _post_json("https://api.openai.com/v1/responses", {"Authorization": f"Bearer {key}"}, {
        "model": info["id"],
        "input": [{"role": "user", "content": prompt}],
        "reasoning": {"effort": "low"},
        "max_output_tokens": max_out,  # includes reasoning tokens
        "text": {"format": {"type": "json_schema", "name": "answer", "strict": True, "schema": schema}},
    }, "OpenAI")
    usage = data.get("usage") or {}
    text, problem = "", None
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue  # reasoning items
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                text += part.get("text", "")
            elif part.get("type") == "refusal":
                problem = "OpenAI declined this request"
    if data.get("status") == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason")
        problem = "OpenAI's answer was cut off; try again" if reason == "max_output_tokens"             else f"OpenAI stopped early ({reason})"
    return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0), problem


def _gemini(settings: dict, info: dict, prompt: str, schema: dict, max_out: int):
    key = _require_key(settings, "gemini", "Gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{info['id']}:generateContent"
    config = {"responseMimeType": "application/json", "responseJsonSchema": schema,
              "maxOutputTokens": max_out, "thinkingConfig": {"thinkingLevel": "low"}}
    data = _post_json(url, {"x-goog-api-key": key},
                      {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config},
                      "Gemini")
    usage = data.get("usageMetadata") or {}
    # Thinking tokens bill as output but aren't in candidatesTokenCount.
    tokens_out = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    tokens_in = usage.get("promptTokenCount", 0)
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no answer")
        return "", tokens_in, tokens_out, f"Gemini declined this request ({reason})"
    c = candidates[0]
    text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
    problem = None
    if c.get("finishReason") == "MAX_TOKENS":
        problem = "Gemini's answer was cut off; try again"
    elif c.get("finishReason") not in (None, "STOP"):
        problem = f"Gemini stopped early ({c['finishReason']})"
    return text, tokens_in, tokens_out, problem


CALLERS = {"claude": _claude, "openai": _openai, "gemini": _gemini, "ollama": _ollama}


# ---- prompts ----------------------------------------------------------------------

SUGGESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "query": {"type": "string"},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "max_price": {"type": ["number", "null"]},
                    "reason": {"type": "string"},
                },
                "required": ["name", "category", "query", "exclude", "max_price", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["good", "ok", "skip"]},
        "note": {"type": "string"},
    },
    "required": ["verdict", "note"],
    "additionalProperties": False,
}

QUERY_RULES = """Watch query syntax (matched against listing titles, case/punctuation-insensitive):
- space-separated terms must ALL appear, so keep queries short: usually just the model number (e.g. "10980xe")
- a|b means either term (e.g. "n100|n150|n97")
- "exact phrase" in quotes
- exclude is a list of words that disqualify a listing (e.g. "rdimm", "ecc", "laptop")
Used-market sellers write titles inconsistently, so prefer model numbers over full product names."""


def _describe_machine(machine: dict) -> str:
    kind = machine.get("kind") or "pc"
    lines = [f"{'Machine' if kind in ('pc', 'server') else kind.capitalize()}: {machine['name']}"]
    if machine.get("model"):
        lines.append(f"Make/model: {machine['model']}")
    lines += [f"- {p.get('category', 'other')}: {p.get('model', '')}" for p in machine.get("parts", [])]
    if machine.get("notes"):
        lines.append(f"Notes: {machine['notes']}")
    return "\n".join(lines)


def upgrades_prompt(machine: dict) -> tuple[str, dict]:
    if (machine.get("kind") or "pc") not in ("pc", "server"):
        return gear_prompt(machine)
    return f"""You help someone find used PC hardware upgrades.

{_describe_machine(machine)}

Suggest the best drop-in upgrades for this exact machine: parts that work without
replacing the motherboard (CPU, RAM, GPU, storage, anything else worthwhile). For each,
mention BIOS, power, cooling or physical-fit caveats in `reason`, and set max_price to a
sensible used-market ceiling in USD, or null if you're unsure. Only suggest parts you
are confident are compatible. Give 3 to 10 suggestions, best value first.

{QUERY_RULES}""", SUGGESTION_SCHEMA


def gear_prompt(device: dict) -> tuple[str, dict]:
    """Upgrade/replacement ideas for anything that isn't a PC: TVs, phones, network gear, appliances..."""
    return f"""You help someone find good deals on things they own, new or used.

{_describe_machine(device)}

Work out what this device is from its make and model (model codes like OLED65B2AUA or U7NHD are
fine to interpret). Suggest worthwhile upgrades or replacements to watch for: clearly better
models a generation or two newer that are now affordable, plus genuinely useful accessories
(e.g. a soundbar for a TV, a case for a phone). For each, say in `reason` why it's a real step up
from what they have and any compatibility caveats, use the device kind as `category`, and set
max_price to a sensible deal price in USD (used or on sale), or null if you're unsure.
Give 3 to 8 suggestions, best value first. If the device is already top of its class, say so in
the reasons and suggest fewer.

{QUERY_RULES}""", SUGGESTION_SCHEMA


PART_CATEGORIES = ["cpu", "motherboard", "ram", "gpu", "storage", "psu", "cooler", "case", "network", "other"]

PARTS_SCHEMA = {
    "type": "object",
    "properties": {
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": PART_CATEGORIES},
                    "model": {"type": "string"},
                },
                "required": ["category", "model"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["parts"],
    "additionalProperties": False,
}


def specs_prompt(machine: dict, text: str) -> tuple[str, dict]:
    """Parts from whatever the user pasted: System Information, a receipt, notes."""
    return f"""Pull a computer's parts out of the text below, which describes the computer "{machine['name']}".

Return each part once with a short, searchable model name, e.g. "AMD Ryzen 7 3700X",
"MSI MPG B550 GAMING PLUS", "32GB (2x16GB) DDR4-3200", "NVIDIA GeForce RTX 3060 12GB", "Samsung 980 Pro 1TB NVMe".
Combine RAM sticks into one entry. Leave out anything the text doesn't actually say; don't guess.

Text:
---
{text[:6000]}
---""", PARTS_SCHEMA


def draft_prompt(description: str, machines: list[dict]) -> tuple[str, dict]:
    owned = "\n\n".join(_describe_machine(m) for m in machines) or "(none listed)"
    return f"""You help someone set up saved searches for used hardware.

They want: {description}

Their current machines, in case it's relevant:
{owned}

Turn this into 1 to 6 watches. If they describe a need rather than a product
(e.g. "low power box for Frigate NVR"), pick the specific models that fit best
and make a watch per model family. Set max_price if they gave a budget, else a sensible
used-market ceiling in USD, or null if you're unsure. Explain each pick in `reason`.

{QUERY_RULES}""", SUGGESTION_SCHEMA


def judge_prompt(listing: dict, watch: dict, machine: dict | None) -> tuple[str, dict]:
    price = f"${listing['total']:.2f}" if listing.get("total") is not None else "not stated"
    return f"""Is this used-hardware listing worth pursuing?

Listing title: {listing['title']}
Price incl. shipping: {price}
Condition: {listing.get('condition') or 'unknown'}
Source: {listing['source']}
Searched for: {watch['name']} (query "{watch['query']}")
{('It is meant for this machine:' + chr(10) + _describe_machine(machine)) if machine else ''}

Answer with verdict good / ok / skip and a short note (2 sentences max) covering
compatibility problems, red flags in the title, and whether the price is fair for
the used market.""", VERDICT_SCHEMA
