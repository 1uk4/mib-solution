#!/usr/bin/env python3
"""L3 filter census over the 1000-packet corpus (warm OCR cache).

Measures per-filter fire rates, the illegibility ratio distribution
(threshold-margin analysis around 0.30), and two interplay questions:
does L4's _reject_placeholder still fire post-sanitization, and does
SPONSOR_ATTESTS_RE's [SPONSOR ID BLANK] alternative ever match sanitized
text?
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Users/lukaflores/Code/mib-solution")

from v4.acquire import acquire_sources, IMAGE, TEXT_STREAM
from v4.extract import extract_content
from v4.filters.injection import sanitize_injection
from v4.filters.redaction import sanitize_redactions
from v4.filters.illegibility import detect_illegibility, _TOKEN_RE, _DATE_RE, _ID_RE, _VOCAB, _MIN_TOKENS
from v4.patterns import PLACEHOLDER_RE, SPONSOR_ATTESTS_RE

PDF_DIR = Path("/tmp/mib-dev/pdfs")
OUT = Path(__file__).parent / "filter_census.json"

def illeg_ratio(text):
    tokens = _TOKEN_RE.findall(text)
    n_dates = len(_DATE_RE.findall(text))
    n_ids = len(_ID_RE.findall(text))
    total = len(tokens) + n_dates + n_ids
    if total < _MIN_TOKENS:
        return None, total
    rec = n_dates + n_ids + sum(1 for t in tokens if t.lower() in _VOCAB)
    return rec / total, total

stats = {
    "packets": 0, "text_sources": 0, "image_sources": 0,
    "inj_sources_modified": 0, "inj_lines_dropped": 0,
    "inj_marker_hist": Counter(), "inj_on_images": 0,
    "red_sources_modified": 0, "red_chars_stripped": 0, "red_on_images": 0,
    "illeg_excluded": 0, "illeg_too_few_tokens": 0,
    "ratio_hist": Counter(),            # 0.05-wide bins
    "band_025_035": 0,                  # threshold-sensitive images
    "band_examples": [],
    "placeholder_post_sanitize": 0,     # PLACEHOLDER_RE matches in sanitized content
    "sponsor_blank_alternative": 0,     # [SPONSOR ID BLANK] branch matches post-sanitize
}

pdfs = sorted(PDF_DIR.glob("*.pdf"))
for i, p in enumerate(pdfs, 1):
    stats["packets"] += 1
    sources = acquire_sources(p)
    extract_content(sources)
    for s in sources:
        if s.type == TEXT_STREAM:
            stats["text_sources"] += 1
        else:
            stats["image_sources"] += 1
        pre = s.content
        # injection
        _, reason = sanitize_injection(s)
        if reason:
            stats["inj_sources_modified"] += 1
            n, markers = reason.split(":")[1], reason.split(":")[2]
            stats["inj_lines_dropped"] += int(n)
            for mk in markers.split(","):
                stats["inj_marker_hist"][mk] += 1
            if s.type == IMAGE:
                stats["inj_on_images"] += 1
        # redaction
        _, reason = sanitize_redactions(s)
        if reason:
            stats["red_sources_modified"] += 1
            stats["red_chars_stripped"] += int(reason.split(":")[1].split("_")[0])
            if s.type == IMAGE:
                stats["red_on_images"] += 1
        # illegibility (post-sanitize, as in production)
        if s.type == IMAGE and s.content:
            r, total = illeg_ratio(s.content)
            if r is None:
                stats["illeg_too_few_tokens"] += 1
            else:
                stats["ratio_hist"][f"{int(r*20)/20:.2f}"] += 1
                if 0.25 <= r < 0.35:
                    stats["band_025_035"] += 1
                    if len(stats["band_examples"]) < 12:
                        stats["band_examples"].append(
                            {"case": p.stem, "src": s.id, "ratio": round(r, 3), "tokens": total})
        fired, _ = detect_illegibility(s)
        if fired:
            stats["illeg_excluded"] += 1
        # interplay checks on sanitized content
        if s.content:
            for line in s.content.split("\n"):
                if PLACEHOLDER_RE.match(line):
                    stats["placeholder_post_sanitize"] += 1
            m = SPONSOR_ATTESTS_RE.search(s.content)
            if m and "BLANK" in m.group(0).upper():
                stats["sponsor_blank_alternative"] += 1
    if i % 200 == 0:
        print(f"{i}/1000...", flush=True)

stats["inj_marker_hist"] = dict(stats["inj_marker_hist"].most_common())
stats["ratio_hist"] = dict(sorted(stats["ratio_hist"].items()))
OUT.write_text(json.dumps(stats, indent=2))
print(json.dumps(stats, indent=2))
