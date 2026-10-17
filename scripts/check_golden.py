#!/usr/bin/env python3
"""Golden-sample regression gate: every change to templates/rules/cards must keep
all golden samples passing. Exit 0 = all green, exit 1 = regression detected.

Golden samples live in testdata/style-cards/<brand>.json (style channel) and
testdata/golden/golden.json (element channel, when populated).
"""
import base64
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from sirius_vision.templates import TEMPLATES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(ROOT, "testdata", "style-cards")

key = None
with open(os.path.expanduser("~/.hermes/.env")) as f:
    for line in f:
        m = re.match(r"^(MINIMAX_API_KEY|MINIMAX_CN_API_KEY)=(.+)$", line.strip())
        if m:
            key = m.group(2).strip().strip('"').strip("'")
            break
assert key, "provider key not found in ~/.hermes/.env"

STYLE_T = TEMPLATES["brand_risk_style"]


def ask(prompt: str, fp: str) -> str:
    b64 = base64.b64encode(open(fp, "rb").read()).decode()
    body = json.dumps({
        "model": "MiniMax-M3",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
    }).encode()
    req = urllib.request.Request(
        "https://api.minimaxi.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    return re.sub(r"<think>.*?</think>", "", out["choices"][0]["message"]["content"], flags=re.S).strip()


def style_verdict(parsed: dict, card: dict) -> str:
    n = parsed.get("match_count", 0)
    conf = parsed.get("style_confidence", 0)
    if n >= 5 and conf >= 0.9:
        return "block"
    if n >= 4:
        return "review"
    return "pass"


def main() -> int:
    failures = []
    checked = 0
    for cf in sorted(os.listdir(CARDS_DIR)):
        if not cf.endswith(".json"):
            continue
        card = json.load(open(os.path.join(CARDS_DIR, cf)))
        card_text = (f"BRAND: {card['brand']} (watchlist: {card['watchlist_name']}, "
                     f"reason: {card['risk_reason']})\nSignature elements:\n" +
                     "\n".join(f"{i+1}. {e}" for i, e in enumerate(card["signature_elements"])))
        prompt = STYLE_T.prompt + "\n" + card_text + "\nSchema: " + json.dumps(STYLE_T.schema)
        for g in card.get("golden", []):
            fp = os.path.join(ROOT, "testdata", g["file"])
            if not os.path.exists(fp):
                failures.append((g["file"], "MISSING FILE"))
                continue
            checked += 1
            try:
                parsed = STYLE_T.parse(ask(prompt, fp))
                got = style_verdict(parsed, card)
                status = "OK" if got == g["expect"] else "REGRESSION"
                print(f"[{status}] {g['file']}: expect={g['expect']} got={got} "
                      f"(count={parsed.get('match_count')} conf={parsed.get('style_confidence')})")
                if status == "REGRESSION":
                    failures.append((g["file"], f"expect {g['expect']} got {got}"))
            except Exception as e:
                failures.append((g["file"], f"{type(e).__name__}: {e}"))
                print(f"[ERROR] {g['file']}: {e}")

    print(f"\nchecked {checked} golden samples, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
