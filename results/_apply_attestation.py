"""Flip verification_status to public-regrade-verified for exactly the bundles
named in the maintainer attestation comment (read from _attestation_comment.md)."""
import json
import os
import re

WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
body = open(os.path.join(WT, "results", "_attestation_comment.md"), encoding="utf-8").read()

# Pull the fenced ```json ... ``` block.
fence = "```"
start = body.index(fence + "json") + len(fence + "json")
end = body.index(fence, start)
att = json.loads(body[start:end].strip())

paths = [r["path"] for r in att["results"]]
print("attested bundles:", len(paths))
flipped, missing, already = 0, 0, 0
for p in paths:
    fp = os.path.join(WT, p.replace("/", os.sep))
    if not os.path.exists(fp):
        print("MISSING", p)
        missing += 1
        continue
    txt = open(fp, encoding="utf-8").read()
    needle = '"verification_status": "unverified"'
    repl = '"verification_status": "public-regrade-verified"'
    if needle in txt:
        txt = txt.replace(needle, repl, 1)
        with open(fp, "w", encoding="utf-8", newline="") as fh:
            fh.write(txt)
        flipped += 1
    elif repl in txt:
        already += 1
    else:
        print("NO STATUS FIELD", p)
print(f"flipped={flipped} already={already} missing={missing}")
