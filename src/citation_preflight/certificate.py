"""Certificate: a checksummed, reproducible record of one pre-flight run.

Layout of report.json:

    {
      "certificate": { ...body... },          # everything that is hashed
      "certificate_sha256": "<hex>",          # SHA-256 of canonical(body)
      "generated_at": "2026-10-31T...Z",      # NOT hashed
      "input_path": "demo/brief_ai_draft.txt" # NOT hashed
    }

canonical(body) = UTF-8 JSON with sorted keys, no insignificant whitespace,
non-ASCII kept as-is. The body contains no timestamps or absolute paths, so the
same input + same resolver data + same tool/rules/extractor versions always
gives byte-identical canonical bodies and the same hash.

This is a checksum, not a signature: it proves the report has not been edited
since it was produced only to someone who can re-run the check. No keys are
involved.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import __version__
from .resolvers import canonical_json
from .rules import RULES_VERSION, RULE_TEXT

FORMAT = "citation-preflight/certificate/v1"


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_body(*, input_bytes: bytes, input_chars: int, extractor: dict, resolver: dict,
               citations: list[dict], overall: str, overall_reason: str) -> dict:
    counts = {k: 0 for k in ("PASS", "FAIL", "WARN", "NOT CHECKED")}
    for c in citations:
        counts[c["verdict"]] += 1
    numbered = [{"id": f"C{i + 1}", **c} for i, c in enumerate(citations)]
    return {
        "format": FORMAT,
        "tool": {"name": "citation-preflight", "version": __version__},
        "rules": {"version": RULES_VERSION, "text": RULE_TEXT},
        "extractor": extractor,
        "resolver": resolver,
        "input": {"sha256": sha256_hex(input_bytes), "bytes": len(input_bytes), "chars": input_chars},
        "overall": {"verdict": overall, "reason": overall_reason, "counts": counts},
        "citations": numbered,
    }


def envelope(body: dict, input_path: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "certificate": body,
        "certificate_sha256": sha256_hex(canonical_json(body)),
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_path": input_path,
        "note": "certificate_sha256 = SHA-256 of the canonical JSON of `certificate` "
                "(sorted keys, compact separators, UTF-8). generated_at and input_path are not hashed. "
                "Checksummed and reproducible; not cryptographically signed.",
    }


def verify(env: dict) -> tuple[bool, str, str]:
    """Recompute the hash. Returns (ok, recorded, recomputed)."""
    recomputed = sha256_hex(canonical_json(env["certificate"]))
    recorded = env.get("certificate_sha256", "")
    return recomputed == recorded, recorded, recomputed


def dumps(env: dict) -> str:
    return json.dumps(env, indent=2, ensure_ascii=False) + "\n"
