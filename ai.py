"""Tiny AI helper: Anthropic Claude or Google Gemini through plain HTTPS (no extra packages)."""
import json
import os
import urllib.error
import urllib.request


class AIError(Exception):
    pass


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


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise AIError("The AI key was rejected. Check the API key on the server.")
        if err.code == 429:
            raise AIError("The AI service is busy or the free limit was reached. Try again in a minute.")
        raise AIError(f"The AI service returned an error ({err.code}).")
    except (urllib.error.URLError, TimeoutError):
        raise AIError("Could not reach the AI service. Please try again.")


def complete(system, messages, max_tokens=700):
    """messages: [{'role': 'user'|'assistant', 'content': str}] starting with a user message."""
    if not ai_enabled():
        raise AIError("AI is not set up on this server yet.")
    if provider() == "anthropic":
        data = _post("https://api.anthropic.com/v1/messages",
                     {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                     {"model": os.environ.get("AI_MODEL", "claude-haiku-5-5"), "max_tokens": max_tokens,
                      "system": system, "messages": messages})
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    else:
        model = os.environ.get("AI_MODEL", "gemini-2.5-flash")
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                     {"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                                    "parts": [{"text": m["content"]}]} for m in messages],
                      "generationConfig": {"maxOutputTokens": max_tokens}})
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
    if not text.strip():
        raise AIError("The AI did not return an answer. Please try again.")
    return text.strip()
