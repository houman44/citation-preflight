"""Pipeline: extract -> resolve -> check -> certificate body."""

from __future__ import annotations

from .certificate import build_body
from .extract import extract
from .resolvers import citation_from_entry, match_entries
from .rules import evaluate, overall


def run_check(input_bytes: bytes, resolver, engine: str = "auto") -> dict:
    text = input_bytes.decode("utf-8")
    citations, extractor_info = extract(text, engine)
    entries = resolver.lookup(text, citations)
    matches, unmatched = match_entries(citations, entries)

    records = []
    for i, c in enumerate(citations):
        records.append((c.start, evaluate(c, matches.get(i), resolver.missing_reason)))
    # Citations the resolver found that the local extractor missed are checked too,
    # so a fabricated citation cannot slip through an extractor gap.
    for e in unmatched:
        c = citation_from_entry(e, text)
        records.append((c.start, evaluate(c, e, resolver.missing_reason)))
    records.sort(key=lambda r: r[0])
    cits = [r for _, r in records]

    verdict, reason = overall([c["verdict"] for c in cits])
    return build_body(input_bytes=input_bytes, input_chars=len(text), extractor=extractor_info,
                      resolver=resolver.describe(), citations=cits,
                      overall=verdict, overall_reason=reason)
