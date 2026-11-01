"""Deterministic per-citation checks and verdict logic.

Results: PASS, FAIL, WARN, NOT CHECKED.

R1 resolves      status 200/300 -> PASS; 404 -> FAIL ("no such citation");
                 400 (reporter not recognised) -> WARN (cannot verify);
                 429 (over per-request cap) or no result -> NOT CHECKED.
R2 unambiguous   more than one candidate case (status 300 or >1 cluster) -> WARN.
R3 case name     brief's "X v. Y" vs resolved case_name / case_name_full,
                 see names.NAME_RULE -> PASS or FAIL. No name in brief -> WARN.
R4 year          brief's year vs year of date_filed: equal -> PASS;
                 off by one -> WARN (term/reporting-year differences);
                 off by more -> FAIL. No year in brief -> WARN.
R0 supported     Id., supra, short forms, statutes, journals -> NOT CHECKED.

Citation verdict: FAIL if any check FAILs, else WARN if any WARN, else
NOT CHECKED if any NOT CHECKED, else PASS.
Overall: BLOCK if any FAIL; REVIEW if any WARN or NOT CHECKED, or if no
citations were found at all; PASS otherwise.
"""

from __future__ import annotations

from typing import Optional

from . import names
from .extract import CASE, UNSUPPORTED_REASONS, Extracted

RULES_VERSION = "1.0.0"

PASS, FAIL, WARN, NOT_CHECKED = "PASS", "FAIL", "WARN", "NOT CHECKED"
BLOCK, REVIEW = "BLOCK", "REVIEW"

YEAR_WARN_TOLERANCE = 1

RULE_TEXT = {
    "R0": "Only full case-law citations are checked; other forms are listed as NOT CHECKED.",
    "R1": "Citation must resolve in the case-law database (404 => FAIL).",
    "R2": "Citation must resolve to exactly one case (several => WARN).",
    "R3": names.NAME_RULE + " Mismatch => FAIL.",
    "R4": f"Year in brief must equal year of date_filed; off by {YEAR_WARN_TOLERANCE} => WARN; more => FAIL.",
}


def _check(rule: str, label: str, result: str, evidence: str) -> dict:
    return {"rule": rule, "check": label, "result": result, "evidence": evidence}


def _candidates(entry: dict) -> list[dict]:
    out = []
    for cl in entry.get("clusters") or []:
        out.append({
            "case_name": cl.get("case_name") or "",
            "case_name_full": cl.get("case_name_full") or "",
            "date_filed": cl.get("date_filed") or "",
            "absolute_url": cl.get("absolute_url") or "",
        })
    return out


def check_resolves(entry: Optional[dict], missing_reason: str) -> dict:
    if entry is None:
        return _check("R1", "resolves", NOT_CHECKED, f"No lookup result: {missing_reason}.")
    st = entry.get("status")
    msg = entry.get("error_message") or ""
    n = len(entry.get("clusters") or [])
    if st == 200 and n >= 1:
        return _check("R1", "resolves", PASS, f"Found (status 200, {n} case{'s' if n != 1 else ''}).")
    if st == 300 and n >= 1:
        return _check("R1", "resolves", PASS, f"Found (status 300, {n} possible cases).")
    if st in (200, 300) and n == 0:
        return _check("R1", "resolves", WARN,
                      f"Lookup reported status {st} but returned no case record; cannot verify.")
    if st == 404:
        return _check("R1", "resolves", FAIL,
                      f"No such citation: the database has no case at this volume/reporter/page "
                      f"(status {st}{': ' + msg if msg else ''}).")
    if st == 400:
        return _check("R1", "resolves", WARN,
                      f"Reporter not recognised, so the citation cannot be verified (status 400{': ' + msg if msg else ''}).")
    if st == 429:
        return _check("R1", "resolves", NOT_CHECKED,
                      f"Not looked up: over the per-request citation cap (status 429{': ' + msg if msg else ''}).")
    return _check("R1", "resolves", NOT_CHECKED, f"Unexpected lookup status {st!r}{': ' + msg if msg else ''}.")


def check_unambiguous(entry: dict) -> dict:
    cands = _candidates(entry)
    if entry.get("status") == 300 or len(cands) > 1:
        names_ = "; ".join(f"{c['case_name'] or '?'} ({c['date_filed'] or '?'})" for c in cands)
        return _check("R2", "unambiguous", WARN, f"Matches {len(cands)} cases: {names_}. A person must pick one.")
    return _check("R2", "unambiguous", PASS, "Matches exactly one case.")


def check_case_name(c: Extracted, entry: dict) -> tuple[dict, list[int]]:
    """Returns (check, indexes of candidates whose name matched)."""
    cands = _candidates(entry)
    if not c.case_name:
        return _check("R3", "case name", WARN,
                      "No 'X v. Y' case name found next to the citation, so the name was not compared."), []
    matched, details = [], []
    for i, cand in enumerate(cands):
        options = [n for n in dict.fromkeys([cand["case_name"], cand["case_name_full"]]) if n]
        for n in options:
            r = names.compare(c.case_name, n)
            if r["match"]:
                matched.append(i)
                details.append(f"“{c.case_name}” matches “{n}” ({r['detail']})")
                break
        else:
            if options:
                r = names.compare(c.case_name, options[0])
                details.append(f"“{options[0]}”: {r['detail']}")
    if not any(cand["case_name"] or cand["case_name_full"] for cand in cands):
        return _check("R3", "case name", WARN, "Resolved case has no name on record; not compared."), []
    if matched:
        return _check("R3", "case name", PASS, details[0] if len(cands) == 1 else
                      f"{len(matched)} of {len(cands)} candidates match. " + " | ".join(details)), matched
    resolved = " / ".join(f"“{cand['case_name']}”" for cand in cands if cand["case_name"])
    why = details[0].split(": ", 1)[-1] if len(details) == 1 else "; ".join(details)
    return _check("R3", "case name", FAIL,
                  f"Brief says “{c.case_name}” but {c.key} is {resolved} ({why})."), []


def check_year(c: Extracted, entry: dict, only: Optional[list[int]] = None) -> dict:
    cands = _candidates(entry)
    pool = [cands[i] for i in only] if only else cands
    if c.year is None:
        return _check("R4", "year", WARN, "No year in a parenthetical right after the citation; not compared.")
    years = []
    for cand in pool:
        d = cand["date_filed"]
        if len(d) >= 4 and d[:4].isdigit():
            years.append((abs(int(d[:4]) - c.year), d))
    if not years:
        return _check("R4", "year", WARN, "Resolved case has no date_filed; not compared.")
    diff, date = min(years)
    if diff == 0:
        return _check("R4", "year", PASS, f"Brief says {c.year}; date_filed {date}.")
    if diff <= YEAR_WARN_TOLERANCE:
        return _check("R4", "year", WARN, f"Brief says {c.year}; date_filed {date} (off by {diff} year).")
    return _check("R4", "year", FAIL, f"Brief says {c.year}; date_filed {date} (off by {diff} years).")


def combine(results: list[str]) -> str:
    if FAIL in results:
        return FAIL
    if WARN in results:
        return WARN
    if NOT_CHECKED in results:
        return NOT_CHECKED
    return PASS


def evaluate(c: Extracted, entry: Optional[dict], missing_reason: str) -> dict:
    """Run all checks for one extracted citation. Returns a JSON-ready dict."""
    rec = {
        "kind": c.kind,
        "text": c.text,
        "span": [c.start, c.end],
        "parsed": {
            "key": c.key, "volume": c.volume, "reporter": c.reporter,
            "reporter_normalized": c.reporter_normalized, "page": c.page,
            "pin_cite": c.pin_cite, "case_name_in_brief": c.case_name,
            "year_in_brief": c.year,
        },
        "extraction_notes": list(c.notes),
        "lookup": None,
        "checks": [],
    }
    if c.kind != CASE:
        rec["checks"].append(_check("R0", "supported form", NOT_CHECKED, UNSUPPORTED_REASONS[c.kind]))
        rec["verdict"] = NOT_CHECKED
        return rec

    r1 = check_resolves(entry, missing_reason)
    rec["checks"].append(r1)
    if entry is not None:
        rec["lookup"] = {
            "status": entry.get("status"),
            "error_message": entry.get("error_message") or "",
            "normalized_citations": list(entry.get("normalized_citations") or []),
            "candidates": _candidates(entry),
        }
    if r1["result"] == PASS:
        rec["checks"].append(check_unambiguous(entry))
        r3, matched = check_case_name(c, entry)
        rec["checks"].append(r3)
        rec["checks"].append(check_year(c, entry, matched or None))
    else:
        why = "not compared: the citation did not resolve to a case"
        rec["checks"].append(_check("R3", "case name", NOT_CHECKED, why.capitalize() + "."))
        rec["checks"].append(_check("R4", "year", NOT_CHECKED, why.capitalize() + "."))
    rec["verdict"] = combine([ch["result"] for ch in rec["checks"]])
    return rec


def overall(verdicts: list[str]) -> tuple[str, str]:
    if not verdicts:
        return REVIEW, "No citations were found; nothing was verified."
    if FAIL in verdicts:
        return BLOCK, "At least one citation failed a check. Do not file until fixed."
    if WARN in verdicts or NOT_CHECKED in verdicts:
        return REVIEW, "No failures, but some citations need a person to look at them."
    return PASS, "Every citation resolved and matched on name and year."
