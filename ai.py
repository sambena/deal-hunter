"""Optional AI helpers. Only runs when Settings > AI is not "off", and only when
you press an AI button, so it never spends anything in the background.

Every paid request goes through one gate:
  1. estimate the cost from the prompt size and the model's price,
  2. refuse if the worst case would push this month's spend past the limit,
  3. call the provider and record what it actually billed.

Whose AI: the admin uses their own settings. Other people either use their own key ("own") or the
admin's AI ("shared"), within limits the admin sets per person: requests a day, and dollars a month of
paid AI. The admin's monthly limit covers everything run on the admin's key, by anyone.

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
import netguard


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
    "radar": {"label": "Deal radar", "typical_out": 2500, "max_out": 8000},
    # Up to REVIEW_MAX finds in one request, about 60 tokens of answer each.
    "review": {"label": "AI review of finds", "typical_out": 2500, "max_out": 9000},
}
REVIEW_MAX = 40


# ---- whose AI ---------------------------------------------------------------------

AI_KEYS = ("ai_provider", "ollama_url", "ollama_model", "anthropic_api_key", "claude_model", "openai_api_key",
           "openai_model", "gemini_api_key", "gemini_model", "gemini_free_tier")


def settings_for(user_id: int | None = None) -> dict:
    """The settings a person's AI requests run with: their own, or the admin's AI on the person's limits."""
    uid = user_id if user_id is not None else db.current_user_id()
    s = db.get_settings(uid)
    user = db.query("SELECT role, ai_shared, ai_allowance, ai_daily_cap FROM users WHERE id = ?", (uid,))[0]
    if user["role"] == "admin" or s.get("ai_source") == "own":
        # "trusted": the addresses in these settings are the admin's, so they may be on the home network.
        s["_ai"] = {"source": "own", "user_id": uid, "paid_by": uid, "trusted": user["role"] == "admin"}
        return s
    admin_id = db.admin_id()
    admin = db.get_settings(admin_id)
    s.update({k: admin[k] for k in AI_KEYS})
    s["ai_monthly_limit"] = float(user["ai_allowance"] or 0)
    owner = db.query("SELECT name FROM users WHERE id = ?", (admin_id,))[0]["name"] or "the admin"
    s["_ai"] = {"source": "shared", "user_id": uid, "paid_by": admin_id, "owner": owner, "trusted": True,
                "daily_cap": int(user["ai_daily_cap"] or 0), "pool_limit": float(admin.get("ai_monthly_limit") or 0)}
    if not user["ai_shared"]:
        s["ai_provider"] = "off"
        s["_ai"]["blocked"] = f"{owner} hasn't shared their AI with you. Use your own key in Settings > AI."
    elif s["ai_provider"] not in PROVIDERS:
        s["_ai"]["blocked"] = f"{owner}'s AI is off. Use your own key in Settings > AI."
    return s


def choices(settings: dict) -> list[dict]:
    """The AIs a person can pick for one request: every model of each provider they have a key for. On the
    admin's shared AI, only the model the admin chose (the admin decides what friends spend on)."""
    sc = _scope(settings)
    if sc["source"] == "shared":
        try:
            info = model_info(settings)
        except AIError:
            return []
        return [{"provider": info["provider"], "model": info["id"], "label": f"{info['label']} ({sc['owner']}'s)",
                 "free": info["free"], "current": True}]
    out = []
    current = (settings.get("ai_provider"), settings.get(PROVIDERS.get(settings.get("ai_provider"), {}).get("model", "")))
    for provider, p in PROVIDERS.items():
        if provider == "ollama":
            if settings.get("ollama_url") and settings.get("ollama_model"):
                out.append({"provider": "ollama", "model": settings["ollama_model"], "free": True,
                            "label": f"Ollama {settings['ollama_model']} (free)",
                            "current": current == ("ollama", settings["ollama_model"])})
            continue
        if not (settings.get(p["key"]) or (provider == "claude" and os.environ.get("ANTHROPIC_API_KEY"))):
            continue
        for m in MODELS:
            if m["provider"] == provider:
                free = provider == "gemini" and bool(settings.get("gemini_free_tier"))
                out.append({"provider": provider, "model": m["id"], "free": free,
                            "label": m["label"] + (", free tier" if free else ""), "current": current == (provider, m["id"])})
    return out


def with_choice(settings: dict, provider: str | None, model: str | None) -> dict:
    """These settings, set to use the AI picked for one request (it must be one of choices())."""
    if not provider:
        return settings
    if not any(c["provider"] == provider and c["model"] == model for c in choices(settings)):
        raise AIError("That AI isn't available to you; pick one from the list")
    return {**settings, "ai_provider": provider, PROVIDERS[provider]["model"]: model}


def _scope(settings: dict) -> dict:
    # Settings that didn't come through settings_for() are the current person's own.
    if settings.get("_ai"):
        return settings["_ai"]
    uid = db.current_user_id()
    admin = db.query("SELECT role FROM users WHERE id = ?", (uid,))[0]["role"] == "admin"
    return {"source": "own", "user_id": uid, "paid_by": uid, "trusted": admin}


def _day_start(now: float | None = None) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


# ---- cost and budget ----------------------------------------------------------

def model_info(settings: dict) -> dict:
    provider = settings.get("ai_provider", "off")
    if provider not in PROVIDERS:
        raise AIError(_scope(settings).get("blocked") or "AI is off. Turn it on in Settings.")
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


def _spent(where: str, args: tuple, since: float) -> float:
    return db.query(f"SELECT COALESCE(SUM(cost), 0) AS s FROM ai_usage WHERE at >= ? AND {where}",
                    (since, *args))[0]["s"]


def budget(settings: dict) -> dict:
    """Own key: everything run on it this month (for the admin, that includes people sharing it).
    Shared: what this person ran on the admin's AI, against the allowance the admin gave them."""
    sc = _scope(settings)
    if sc["source"] == "own":
        spent = _spent("paid_by = ?", (sc["paid_by"],), month_start())
    else:
        spent = _spent("user_id = ? AND paid_by = ?", (sc["user_id"], sc["paid_by"]), month_start())
    limit = max(0.0, float(settings.get("ai_monthly_limit") or 0))
    out = {"spent": round(spent, 6), "limit": limit, "remaining": round(max(0.0, limit - spent), 6),
           "source": sc["source"]}
    if sc["source"] == "shared":
        today = db.query("SELECT COUNT(*) AS n FROM ai_usage WHERE at >= ? AND user_id = ? AND paid_by = ?",
                         (_day_start(), sc["user_id"], sc["paid_by"]))[0]["n"]
        out.update(owner=sc["owner"], daily_cap=sc["daily_cap"], today=today, blocked=sc.get("blocked", ""))
        try:
            info = model_info(settings)
            out.update(model_label=info["label"], free=info["free"])
        except AIError:
            pass
    return out


def estimate(settings: dict, action: str, prompt: str, schema: dict) -> dict:
    info = model_info(settings)
    a = ACTIONS[action]
    tokens_in = estimate_input_tokens(prompt, schema)
    typical, worst = cost(info, tokens_in, a["typical_out"]), cost(info, tokens_in, a["max_out"])
    b = budget(settings)
    sc = _scope(settings)
    shared = sc["source"] == "shared"
    allowed, reason = True, ""
    key_setting = PROVIDERS[info["provider"]]["key"]
    env_key = info["provider"] == "claude" and os.environ.get("ANTHROPIC_API_KEY")
    if key_setting and not (settings.get(key_setting) or env_key):
        allowed = False
        reason = (f"{sc['owner']}'s AI has no {PROVIDERS[info['provider']]['label']} key yet." if shared else
                  f"Add your {PROVIDERS[info['provider']]['label']} API key in Settings > AI first.")
    elif shared and b["today"] >= b["daily_cap"]:
        allowed = False
        reason = (f"You've used today's {b['daily_cap']} AI requests on {sc['owner']}'s AI. More tomorrow, "
                  "or use your own key in Settings > AI.")
    elif not info["free"] and b["spent"] + worst > b["limit"]:
        allowed = False
        if shared:
            reason = (f"{sc['owner']} gave you ${b['limit']:.2f} a month of paid AI; ${b['spent']:.2f} is used and "
                      f"this could cost up to ${worst:.3f}. Ask {sc['owner']} for more, or use your own key.")
        else:
            reason = (f"Monthly AI limit: ${b['spent']:.2f} of ${b['limit']:.2f} used, and this could cost up to "
                      f"${worst:.3f}. Raise the limit or pick a cheaper model in Settings > AI.")
    elif shared and not info["free"] and (
            _spent("paid_by = ?", (sc["paid_by"],), month_start()) + worst > sc["pool_limit"]):
        allowed = False
        reason = f"{sc['owner']}'s AI budget for this month is used up. Try again next month, or use your own key."
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
        sc = _scope(settings)
        db.execute("""INSERT INTO ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, user_id,
                                           paid_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (time.time(), info["provider"], info["id"], action, tokens_in, tokens_out, spent,
                    sc["user_id"], sc["paid_by"]))
    if problem:
        raise AIError(problem)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError(f"{info['label']} returned an unreadable answer") from e


def usage_summary(settings: dict) -> dict:
    sc = _scope(settings)
    where, args = (("paid_by = ?", (sc["paid_by"],)) if sc["source"] == "own"
                   else ("user_id = ? AND paid_by = ?", (sc["user_id"], sc["paid_by"])))
    rows = db.query(f"""SELECT model, action, COUNT(*) AS n, SUM(cost) AS cost FROM ai_usage
                        WHERE at >= ? AND {where} GROUP BY model, action ORDER BY cost DESC""",
                    (month_start(), *args))
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
    base = settings["ollama_url"].strip()
    if "?" in base or "#" in base:
        raise AIError("The Ollama address can't contain ? or #")
    trusted = _scope(settings).get("trusted", False)
    req = urllib.request.Request(base.rstrip("/") + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with netguard.urlopen(req, 300, local_ok=trusted, what="Ollama address") as resp:
            data = json.loads(resp.read())
    except netguard.BlockedURL as e:
        raise AIError(str(e)) from e
    except urllib.error.HTTPError as e:
        detail = f": {e.read().decode('utf-8', 'replace')[:200]}" if trusted else ""
        raise AIError(f"Ollama returned HTTP {e.code}{detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise AIError(f"Can't reach Ollama at {base} ({getattr(e, 'reason', e)}). Is it running?" if trusted
                      else "Can't reach Ollama at that address") from e
    except ValueError as e:
        raise AIError("Ollama's answer wasn't readable") from e
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
    make_model = " ".join(x for x in (machine.get("make"), machine.get("model")) if x)
    if make_model:
        lines.append(f"Make/model: {make_model}")
    if machine.get("year"):
        lines.append(f"Year: {machine['year']}")
    if machine.get("msrp"):
        lines.append(f"MSRP: ${machine['msrp']:,.0f}")
    if machine.get("price_paid"):
        lines.append(f"Paid: ${machine['price_paid']:,.0f}" + (f" ({machine['purchased']})" if machine.get("purchased") else ""))
    lines += [f"- {p.get('category', 'other')}: {p.get('model', '')}" for p in machine.get("parts", [])]
    lines += [f"{f['label']}: {f['value']}" for f in machine.get("custom") or [] if f.get("value")]
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


RADAR_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "device_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "category": {"type": "string"},
                    "query": {"type": "string"},
                    "exclude": {"type": "array", "items": {"type": "string"}},
                    "max_price": {"type": ["number", "null"]},
                    "reason": {"type": "string"},
                },
                "required": ["device_id", "name", "category", "query", "exclude", "max_price", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def radar_prompt(devices: list[dict]) -> tuple[str, dict]:
    """One request for the whole deal radar: the single best upgrade to watch for each device."""
    listing = "\n\n".join(f"[device_id {d['id']}]\n{_describe_machine(d)}" for d in devices)
    return f"""You help someone keep an eye out for deals that would upgrade the things they own.

For EACH device below, suggest the single most worthwhile upgrade or replacement to watch for
(one per device, using its device_id): a clearly better model that's now affordable used or on
sale, or a genuinely useful accessory if the device is already excellent. Skip a device (leave
it out) if nothing is worth watching for. Set max_price to a good-deal price in USD, or null if
unsure, and explain in `reason` why it's a real step up. Use the device kind as `category`.

{listing}

{QUERY_RULES}""", RADAR_SCHEMA


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
    if watch.get("kind") == "vehicle":
        return vehicle_judge_prompt(listing, watch, price)
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


def vehicle_judge_prompt(listing: dict, watch: dict, price: str) -> tuple[str, dict]:
    miles = f"{listing['miles']:,}" if listing.get("miles") is not None else "not stated"
    years = "–".join(str(y) for y in (watch.get("year_min"), watch.get("year_max")) if y) or "any"
    return f"""Is this used car or truck listing worth pursuing?

Listing title: {listing['title']}
Model year: {listing.get('year') or 'unknown'}
Odometer: {miles} miles
Title status: {listing.get('title_status') or 'not stated (assume clean unless the title says otherwise)'}
Asking price: {price}
Location: {listing.get('location') or 'unknown'}
Seller / source: {listing.get('buying') or listing['source']}
Searched for: {watch['name']} (query "{watch['query']}", years {years}, max miles {watch.get('max_miles') or 'any'})

Answer with verdict good / ok / skip and a short note (2 sentences max): is the price fair for that
year and mileage in the US used market, and any red flags (salvage/rebuilt title, mileage too high
for the year, a price far below market that looks like a scam, "needs work" wording).""", VERDICT_SCHEMA


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"reviews": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "verdict": {"type": "string", "enum": ["good", "ok", "skip"]},
                       "reason": {"type": "string"}},
        "required": ["id", "verdict", "reason"], "additionalProperties": False}},
        # Changes to a watch that would stop the bad finds coming back; null/[] = leave as is.
        "watch_changes": {"type": "array", "items": {
            "type": "object",
            "properties": {"watch_id": {"type": "integer"}, "add_excludes": {"type": "array", "items": {"type": "string"}},
                           "max_price": {"type": ["number", "null"]}, "min_price": {"type": ["number", "null"]},
                           "year_min": {"type": ["integer", "null"]}, "max_miles": {"type": ["integer", "null"]},
                           "reason": {"type": "string"}},
            "required": ["watch_id", "add_excludes", "max_price", "min_price", "year_min", "max_miles", "reason"],
            "additionalProperties": False}}},
    "required": ["reviews", "watch_changes"],
    "additionalProperties": False,
}


def _review_line(l: dict) -> str:
    price = f"${l['total']:.0f}" if l.get("total") is not None else "no price"
    facts = [f"year {l['year']}" if l.get("year") else "", f"{l['miles']:,} miles" if l.get("miles") is not None else "",
             f"{l['title_status']} title" if l.get("title_status") else "", f"size {l['size']}" if l.get("size") else "",
             l.get("condition") or "", l.get("location") or "", l.get("buying") or l.get("source") or ""]
    return f"- id {l['id']}: {l['title']} | {price} | " + " | ".join(f for f in facts if f)


def review_prompt(listings: list[dict], watches: dict, instructions: str) -> tuple[str, dict]:
    """One request judging many finds: which are worth a look and which are obvious bad deals."""
    groups = {}
    for l in listings:
        groups.setdefault(l["watch_id"], []).append(l)
    parts = []
    for wid, ls in groups.items():
        w = watches.get(wid) or {}
        want = [f'query "{w.get("query", "")}"']
        if w.get("max_price"):
            want.append(f"max ${w['max_price']:.0f}")
        if w.get("year_min") or w.get("year_max"):
            want.append(f"years {w.get('year_min') or 'any'}-{w.get('year_max') or 'any'}")
        if w.get("max_miles"):
            want.append(f"max {w['max_miles']:,} miles")
        if w.get("size"):
            want.append(f"size {w['size']}")
        parts.append(f"Watch id {wid} \"{w.get('name', '?')}\" ({', '.join(want)}):\n"
                     + "\n".join(_review_line(l) for l in ls))
    asked = instructions.strip() or "(nothing extra: use your best judgement)"
    return f"""You're helping someone weed out bad deals from listings their watches found on used and retail
marketplaces (eBay, KSL, Craigslist, OfferUp, Poshmark, Slickdeals...). Prices are in US dollars.

Their own requirements, which outrank your defaults:
{asked}

For EVERY listing below give a verdict:
- "good": a fair or better price for what it is, worth a look
- "ok": nothing wrong, but not a deal
- "skip": an obvious bad deal or not what they want: overpriced for the used market, wrong item or a part/accessory
  instead of the thing, scam signs (price far too low, vague title), salvage/rebuilt title or very high miles for a
  car without a matching low price, wrong size, or it breaks one of their requirements above.
Be decisive but don't skip something only because details are missing. The reason is one short sentence
(under 20 words) a person can act on. Use each listing's id exactly.

Then, in watch_changes, suggest changes to a watch only where they'd stop the same kind of bad find coming
back: words to exclude (short, specific: "floor mats", "salvage", "parts"; never a word the good finds use), a
price limit, a minimum model year or a mileage cap. Use null (or []) for anything to leave as it is, and leave
watch_changes empty when the watch is fine. Never loosen a limit the person set.

{chr(10).join(parts)}""", REVIEW_SCHEMA
