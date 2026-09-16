"""Optional AI helpers. Only runs when Settings > AI is not "off", and only when
you press an AI button, so it never spends anything in the background.

Providers:
  ollama  free, local (https://ollama.com)
  claude  Anthropic API, needs `pip install anthropic` and an API key
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class AIError(Exception):
    pass


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


def _ollama(settings: dict, prompt: str, schema: dict) -> dict:
    body = json.dumps({
        "model": settings["ollama_model"],
        "messages": [{"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
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
    try:
        return json.loads(data["message"]["content"])
    except (KeyError, json.JSONDecodeError) as e:
        raise AIError("Ollama didn't return valid JSON; try a bigger model") from e


def _claude(settings: dict, prompt: str, schema: dict) -> dict:
    try:
        import anthropic
    except ImportError as e:
        raise AIError("Claude mode needs the SDK: run  pip install anthropic") from e
    key = settings.get("anthropic_api_key") or None  # None -> SDK reads ANTHROPIC_API_KEY
    client = anthropic.Anthropic(api_key=key)
    request = dict(
        model=settings.get("claude_model") or "claude-opus-5",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    )
    try:
        try:
            # Server-side fallback: if the request is declined, the API retries on another model.
            response = client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except TypeError:  # older SDK without the `fallbacks` parameter
            response = client.messages.create(**request)
    except anthropic.AuthenticationError as e:
        raise AIError("Claude API key is invalid") from e
    except anthropic.RateLimitError as e:
        raise AIError("Claude rate limit hit; try again in a minute") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Can't reach the Claude API") from e
    if response.stop_reason == "refusal":
        raise AIError("Claude declined this request")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("Claude returned an unreadable answer") from e


def _ask(settings: dict, prompt: str, schema: dict) -> dict:
    provider = settings.get("ai_provider", "off")
    if provider == "ollama":
        return _ollama(settings, prompt, schema)
    if provider == "claude":
        return _claude(settings, prompt, schema)
    raise AIError("AI is off. Turn it on in Settings.")


def _describe_machine(machine: dict) -> str:
    lines = [f"Machine: {machine['name']}"]
    lines += [f"- {p.get('category', 'other')}: {p.get('model', '')}" for p in machine.get("parts", [])]
    if machine.get("notes"):
        lines.append(f"Notes: {machine['notes']}")
    return "\n".join(lines)


def suggest_upgrades(settings: dict, machine: dict) -> list[dict]:
    prompt = f"""You help someone find used PC hardware upgrades.

{_describe_machine(machine)}

Suggest the best drop-in upgrades for this exact machine: parts that work without
replacing the motherboard (CPU, RAM, GPU, storage, anything else worthwhile). For each,
mention BIOS, power, cooling or physical-fit caveats in `reason`, and set max_price to a
sensible used-market ceiling in USD, or null if you're unsure. Only suggest parts you
are confident are compatible. Give 3 to 10 suggestions, best value first.

{QUERY_RULES}"""
    return _ask(settings, prompt, SUGGESTION_SCHEMA)["suggestions"]


def draft_watches(settings: dict, description: str, machines: list[dict]) -> list[dict]:
    owned = "\n\n".join(_describe_machine(m) for m in machines) or "(none listed)"
    prompt = f"""You help someone set up saved searches for used hardware.

They want: {description}

Their current machines, in case it's relevant:
{owned}

Turn this into 1 to 6 watches. If they describe a need rather than a product
(e.g. "low power box for Frigate NVR"), pick the specific models that fit best
and make a watch per model family. Set max_price if they gave a budget, else a sensible
used-market ceiling in USD, or null if you're unsure. Explain each pick in `reason`.

{QUERY_RULES}"""
    return _ask(settings, prompt, SUGGESTION_SCHEMA)["suggestions"]


def judge_listing(settings: dict, listing: dict, watch: dict, machine: dict | None) -> dict:
    price = f"${listing['total']:.2f}" if listing.get("total") is not None else "not stated"
    prompt = f"""Is this used-hardware listing worth pursuing?

Listing title: {listing['title']}
Price incl. shipping: {price}
Condition: {listing.get('condition') or 'unknown'}
Source: {listing['source']}
Searched for: {watch['name']} (query "{watch['query']}")
{('It is meant for this machine:' + chr(10) + _describe_machine(machine)) if machine else ''}

Answer with verdict good / ok / skip and a short note (2 sentences max) covering
compatibility problems, red flags in the title, and whether the price is fair for
the used market."""
    return _ask(settings, prompt, VERDICT_SCHEMA)
