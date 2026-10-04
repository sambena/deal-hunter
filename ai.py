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
]

PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "key": "anthropic_api_key", "model": "claude_model"},
    "ollama": {"label": "Ollama (free, local)", "key": None, "model": "ollama_model"},
}

# Token allowances per button. `max_out` is a hard cap sent to the provider, so it also caps the cost.
ACTIONS = {
    "judge": {"label": "Ask AI", "typical_out": 700, "max_out": 3000},
    "draft": {"label": "Suggest watches", "typical_out": 2500, "max_out": 8000},
    "upgrades": {"label": "Find upgrades with AI", "typical_out": 3000, "max_out": 8000},
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
            return {**m, "free": False}
    raise AIError(f"Pick a model from the list in Settings > AI (\"{model}\" has no known price, "
                  "so its cost can't be capped).")


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
    if not info["free"] and b["spent"] + worst > b["limit"]:
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
            "models": [{k: m[k] for k in ("provider", "id", "label", "in", "out")} for m in MODELS],
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


CALLERS = {"claude": _claude, "ollama": _ollama}


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
    lines = [f"Machine: {machine['name']}"]
    lines += [f"- {p.get('category', 'other')}: {p.get('model', '')}" for p in machine.get("parts", [])]
    if machine.get("notes"):
        lines.append(f"Notes: {machine['notes']}")
    return "\n".join(lines)


def upgrades_prompt(machine: dict) -> tuple[str, dict]:
    return f"""You help someone find used PC hardware upgrades.

{_describe_machine(machine)}

Suggest the best drop-in upgrades for this exact machine: parts that work without
replacing the motherboard (CPU, RAM, GPU, storage, anything else worthwhile). For each,
mention BIOS, power, cooling or physical-fit caveats in `reason`, and set max_price to a
sensible used-market ceiling in USD, or null if you're unsure. Only suggest parts you
are confident are compatible. Give 3 to 10 suggestions, best value first.

{QUERY_RULES}""", SUGGESTION_SCHEMA


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
