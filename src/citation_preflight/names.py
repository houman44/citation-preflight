"""Deterministic case-name comparison.

Normalization (applied to both names):
  1. Unicode NFKD, drop accents, lower-case, '&' -> 'and', drop "'s".
  2. Replace every non-alphanumeric character with a space; split on spaces.
  3. Drop the stop-tokens in ``STOP_TOKENS`` (party-agnostic words such as
     'v', 'the', 'of', 'inc', 'co', 'corp', citation signals like 'see').

Rule (``NAME_RULE``), in order:
  A. If BOTH names split into two parties on ' v. ' (or ' v ' / ' vs. '):
     MATCH when each party in the brief shares at least one significant
     token with a different party of the resolved name -- i.e.
     (P_brief & P_resolved and D_brief & D_resolved) or, for names
     reversed on appeal, (P_brief & D_resolved and D_brief & P_resolved).
     This is why 'United States v. Smith' does NOT match
     'United States v. Jones'.
  B. Otherwise (e.g. 'In re Gault', 'Ex parte Milligan'): overlap
     coefficient |B & R| / min(|B|, |R|) >= OVERLAP_THRESHOLD (0.5).
"""

from __future__ import annotations

import re
import unicodedata

OVERLAP_THRESHOLD = 0.5

STOP_TOKENS = frozenset("""
v vs versus the of and a an in re ex rel parte et al
inc co corp corporation company companies ltd llc llp lp plc na
for on to at by
see also cf accord but compare contra e g
""".split())

NAME_RULE = (
    "Normalize both names (lower-case, strip punctuation and accents, '&'->'and', "
    "drop stop-tokens). If both have two parties ('X v. Y'), MATCH iff each party in the "
    "brief shares >=1 significant token with the corresponding resolved party (or with the "
    "opposite party, for names reversed on appeal). Otherwise MATCH iff overlap coefficient "
    f"|B∩R|/min(|B|,|R|) >= {OVERLAP_THRESHOLD}."
)

_SPLIT_V = re.compile(r"\s+v(?:s|ersus)?\.?\s+", re.IGNORECASE)


def tokens(name: str) -> list[str]:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"['’]s\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in s.split() if t and t not in STOP_TOKENS]


def split_parties(name: str) -> tuple[str, str] | None:
    parts = _SPLIT_V.split(name, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0], parts[1]
    return None


def compare(brief_name: str, resolved_name: str) -> dict:
    """Return {'match': bool, 'method': str, 'detail': str} -- deterministic."""
    bp, rp = split_parties(brief_name), split_parties(resolved_name)
    if bp and rp:
        bP, bD = set(tokens(bp[0])), set(tokens(bp[1]))
        rP, rD = set(tokens(rp[0])), set(tokens(rp[1]))
        straight = (bP & rP, bD & rD)
        crossed = (bP & rD, bD & rP)
        if straight[0] and straight[1]:
            return {"match": True, "method": "party-wise",
                    "detail": f"shared tokens: first party {sorted(straight[0])}, second party {sorted(straight[1])}"}
        if crossed[0] and crossed[1]:
            return {"match": True, "method": "party-wise (reversed)",
                    "detail": f"shared tokens (parties reversed): {sorted(crossed[0])}, {sorted(crossed[1])}"}
        return {"match": False, "method": "party-wise",
                "detail": f"first party shares {sorted(straight[0]) or 'nothing'}, "
                          f"second party shares {sorted(straight[1]) or 'nothing'}"}
    B, R = set(tokens(brief_name)), set(tokens(resolved_name))
    if not B or not R:
        return {"match": False, "method": "overlap", "detail": "no significant tokens to compare"}
    inter = B & R
    score = len(inter) / min(len(B), len(R))
    return {"match": score >= OVERLAP_THRESHOLD, "method": "overlap",
            "detail": f"overlap {len(inter)}/{min(len(B), len(R))} = {score:.2f} "
                      f"(threshold {OVERLAP_THRESHOLD}); shared {sorted(inter)}"}
