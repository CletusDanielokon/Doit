"""Tiny AI helper: Google Gemini (free) or Anthropic Claude, through plain HTTPS (no extra packages).

Model names change often (Google retires models every few months). So for Gemini this tries the
'latest' alias first, then other known names, then asks Google which models your key can use.
"""
import json
import os
import time
import urllib.error
import urllib.request


class AIError(Exception):
    pass


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_DEFAULTS = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-2.5-flash"]
_good = {"gemini": None}  # remembers the model that worked last time


def provider():
    chosen = (os.environ.get("AI_PROVIDER") or "").lower()
    if chosen in ("anthropic", "gemini"):
        return chosen
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return ""


def ai_enabled():
    p = provider()
    return bool(p and os.environ.get("ANTHROPIC_API_KEY" if p == "anthropic" else "GEMINI_API_KEY"))


def _http(method, url, headers, body=None):
    """Returns (status_code, parsed_json_or_{}). Never raises for HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read().decode() or "{}")
        except ValueError:
            return err.code, {}
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AIError("Could not reach the AI service from the server. Please try again.")


def _detail(payload):
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return str(err.get("message", ""))[:220]
    return ""


def _explain(status, payload):
    detail = _detail(payload)
    if status in (401, 403) or "API key not valid" in detail:
        return "The AI key was rejected. Check that the API key on the server is copied correctly and still active."
    if status == 429:
        return "The free AI limit was reached for now. Please wait a minute and try again."
    if status == 404:
        return "The AI model name is not available any more. " + detail
    return f"The AI service returned an error ({status}). {detail}".strip()


# ---------------------------------------------------------------- Gemini
def _gemini_body(system, messages, max_tokens, json_mode=False):
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages],
            # newer models spend part of this budget "thinking", so keep it generous
            "generationConfig": {"maxOutputTokens": max(2048, max_tokens * 3)}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    return body


def _gemini_text(payload):
    parts = (payload.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def _discover_models(key):
    status, payload = _http("GET", f"{GEMINI_BASE}/models?pageSize=200", {"x-goog-api-key": key})
    if status != 200:
        return []
    names = [m["name"].split("/", 1)[1] for m in payload.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    bad = ("preview", "live", "tts", "image", "audio", "embed", "robotics", "computer", "veo", "imagen", "exp")
    flash = sorted((n for n in names if "flash" in n and not any(b in n for b in bad)), reverse=True)
    return flash[:4]


def _gemini(system, messages, max_tokens, json_mode=False):
    key = os.environ["GEMINI_API_KEY"]
    wanted = os.environ.get("AI_MODEL")
    order = [m for m in [wanted, _good["gemini"], *GEMINI_DEFAULTS] if m]
    tried, last = [], (0, {})
    body = _gemini_body(system, messages, max_tokens, json_mode)

    def attempt(model):
        nonlocal last
        for retry in range(2):
            status, payload = _http("POST", f"{GEMINI_BASE}/models/{model}:generateContent", {"x-goog-api-key": key}, body)
            last = (status, payload)
            if status == 200:
                text = _gemini_text(payload)
                if text:
                    _good["gemini"] = model
                    return text
                return ""
            if status == 503 and retry == 0:  # model busy: wait a moment, retry once
                time.sleep(2)
                continue
            return None
        return None

    for model in dict.fromkeys(order):
        tried.append(model)
        out = attempt(model)
        if out:
            return out
        if last[0] in (401, 403) or "API key not valid" in _detail(last[1]):
            raise AIError(_explain(*last))
    for model in _discover_models(key):  # last resort: ask Google what this key can use
        if model in tried:
            continue
        out = attempt(model)
        if out:
            return out
    if last[0] == 200:
        raise AIError("The AI did not return an answer. Please try again.")
    raise AIError(_explain(*last))


# ---------------------------------------------------------------- Anthropic
def _anthropic(system, messages, max_tokens, json_mode=False):
    status, payload = _http("POST", "https://api.anthropic.com/v1/messages",
                            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                            {"model": os.environ.get("AI_MODEL", "claude-haiku-5-5"), "max_tokens": max_tokens, "system": system, "messages": messages})
    if status != 200:
        raise AIError(_explain(status, payload))
    return "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text").strip()


def complete(system, messages, max_tokens=700, json_mode=False):
    """messages: [{'role': 'user'|'assistant', 'content': str}] starting with a user message."""
    if not ai_enabled():
        raise AIError("AI is not set up on this server yet.")
    text = (_anthropic if provider() == "anthropic" else _gemini)(system, messages, max_tokens, json_mode)
    if not text:
        raise AIError("The AI did not return an answer. Please try again.")
    return text


def diagnose():
    """Used by the 'Test AI' button: returns a short human-readable result."""
    if not ai_enabled():
        return {"ok": False, "message": "No AI key found. Add GEMINI_API_KEY (free) or ANTHROPIC_API_KEY on the server."}
    try:
        reply = complete("Reply with the single word: ready", [{"role": "user", "content": "Are you working?"}], 20)
        model = _good["gemini"] if provider() == "gemini" else os.environ.get("AI_MODEL", "claude-haiku-5-5")
        return {"ok": True, "message": f"AI is working ({provider()}, model {model}). It replied: {reply[:60]}"}
    except AIError as err:
        return {"ok": False, "message": str(err)}
