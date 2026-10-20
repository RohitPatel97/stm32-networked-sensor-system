"""Verify the neighboring originals against the pre-integration source manifest."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads(
    (ROOT / "docs/originals-manifest.json").read_text(encoding="utf-8-sig")
)
for entry in manifest:
    original = ROOT.parent / entry["project"] / entry["path"]
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    if digest.lower() != entry["sha256"].lower():
        raise SystemExit(f"Original changed: {original}")
print(f"Verified {len(manifest)} original source files unchanged")
