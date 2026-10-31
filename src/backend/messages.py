"""Alert message writer. Uses the Featherless LLM when FEATHERLESS_API_KEY is set, else template text.
Returns (text, source) so the dashboard can say honestly which one it is."""
import json
import os
import urllib.request

from risk import template_message


def make_message(alert, cfg):
    base = template_message(alert)
    key = os.environ.get("FEATHERLESS_API_KEY")
    if not key:
        return base, "template"
    try:
        body = json.dumps({
            "model": cfg["llm"]["model"],
            "max_tokens": 120,
            "messages": [
                {"role": "system", "content": "You write one short, plain-English wildlife intrusion alert for a forest ranger. Max 2 sentences. Use only the facts given."},
                {"role": "user", "content": json.dumps({k: alert[k] for k in ("species", "count", "camera_id", "village", "captured_at", "night", "level", "response", "person")})},
            ]}).encode()
        req = urllib.request.Request(cfg["llm"]["endpoint"], data=body, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=cfg["llm"]["timeout_seconds"]) as r:
            text = json.load(r)["choices"][0]["message"]["content"].strip()
        text = " ".join(text.split())[:400]            # cap length; the dashboard renders it as plain text
        return (text, "llm") if text else (base, "template")
    except Exception:
        return base, "template"
