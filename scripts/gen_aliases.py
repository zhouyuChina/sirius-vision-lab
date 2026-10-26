#!/usr/bin/env python3
"""Generate brand alias table (rights-holder -> known IPs/works/sub-brands) via Kimi."""
import json
import os
import re
import urllib.request

key = None
for line in open(os.path.expanduser("~/.hermes/.env")):
    m = re.match(r"^KIMI_API_KEY=(.+)$", line.strip())
    if m:
        key = m.group(1).strip().strip('"').strip("'")
        break
assert key, "KIMI_API_KEY not found"

SRC = "/Users/zhouyu/workspace/sirius-vision-lab/testdata/yuque-brand-avoidlist.json"
OUT = "/Users/zhouyu/workspace/sirius-vision-lab/testdata/brand-aliases.json"

brands = sorted({r["brand"] for r in json.load(open(SRC))})

PROMPT = """Below is a list of brands/rights-holders from a womenswear infringement watchlist.
For EACH brand, list its well-known owned IPs, characters, works, groups, sub-brands or
common alternative names (EN/CN), MAX 8 entries. Only include names a vision model could
plausibly see printed on clothing or packaging. If the brand is obscure with no famous IPs,
return an empty list. Output ONLY a JSON object mapping each brand name to its list of
aliases, no prose, no markdown fences.

Brands:
"""


def ask(batch):
    body = json.dumps({
        "model": "kimi-for-coding",
        "messages": [{"role": "user", "content": PROMPT + "\n".join(batch)}],
    }).encode()
    req = urllib.request.Request(
        "https://api.kimi.com/coding/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    raw = out["choices"][0]["message"]["content"].strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


aliases = {}
BATCH = 25
for i in range(0, len(brands), BATCH):
    batch = brands[i:i + BATCH]
    try:
        got = ask(batch)
        aliases.update(got)
        known = sum(1 for b in batch if got.get(b))
        print(f"batch {i//BATCH+1}: {len(batch)} brands, {known} with aliases")
    except Exception as e:
        print(f"batch {i//BATCH+1} FAILED: {e}")

# drop self-references and empty lists, dedupe
clean = {}
for b, al in aliases.items():
    if not isinstance(al, list):
        continue
    vals = sorted({a.strip() for a in al
                   if isinstance(a, str) and a.strip() and a.strip().lower() != b.lower()})
    if vals:
        clean[b] = vals

json.dump(clean, open(OUT, "w"), ensure_ascii=False, indent=1)
print(f"\nsaved {OUT}: {len(clean)} brands with aliases, "
      f"{sum(len(v) for v in clean.values())} total aliases")
