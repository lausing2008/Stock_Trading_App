#!/usr/bin/env bash
# T382-CLAUDEMD-REINDEX: enforce the index-entry cap that the writing convention states.
#
# WHY A SCRIPT AND NOT JUST THE CONVENTION: the convention already said "one line" and the
# index still grew to 86% of the file (~18,577 of ~21,596 tokens), with one bullet reaching
# 1,831 tokens. A rule with no number and no check degrades gradually while every individual
# addition looks reasonable. This makes it checkable.
#
# CLAUDE.md is read at the START OF EVERY SESSION and re-paid on every prompt-cache rebuild —
# one measured session spent ~7.3M premium cache-creation tokens on this file's own unchanged
# content (see the T322-CLAUDE-MD-CORE-SPLIT note in the file itself).
set -euo pipefail
CM="${1:-.claude/CLAUDE.md}"
MAX_ENTRY_CHARS="${MAX_ENTRY_CHARS:-300}"
MAX_TOTAL_TOKENS="${MAX_TOTAL_TOKENS:-11000}"

python3 - "$CM" "$MAX_ENTRY_CHARS" "$MAX_TOTAL_TOKENS" <<'PY'
import pathlib, re, sys
cm, cap, tok_cap = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
s = cm.read_text()
approx = len(s) / 3.6
i = s.index("## Topic File Index")
entries = re.findall(r"^- \*\*.*?(?=\n- \*\*|\n### |\Z)", s[i:], re.M | re.S)
over = [(len(e), e.split("**")[1][:60]) for e in entries if len(e) > cap]
bad_ptr = [p for p in re.findall(r"^- \*\*`(docs/[^`]+)`\*\*", s, re.M)
           if not pathlib.Path(p).exists() and "{" not in p and "*" not in p]

print(f"  size    : {len(s):,} chars  ~{approx:,.0f} tokens  (cap {tok_cap:,})")
print(f"  entries : {len(entries)}  over {cap} chars: {len(over)}")
print(f"  pointers: broken {len(bad_ptr)}")
fail = False
for n, name in sorted(over, reverse=True):
    print(f"    TOO LONG {n:>6,} chars  {name}")
    fail = True
for b in bad_ptr:
    print(f"    BROKEN POINTER {b}")
    fail = True
if approx > tok_cap:
    print(f"    OVER TOKEN BUDGET by ~{approx - tok_cap:,.0f}")
    fail = True
if fail:
    print("\n  Move the detail into the topic file the entry points at; keep the pointer short.")
    sys.exit(1)
print("  OK")
PY
