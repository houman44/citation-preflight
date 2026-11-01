"""Deterministic citation extraction.

Two engines:

* ``eyecite`` (preferred): Free Law Project's open-source citation parser,
  the same parser CourtListener's citation-lookup API uses.
* ``regex`` (fallback): a small, documented regex for common reporters,
  used only when eyecite is not installed (or when asked for explicitly).

Whatever the engine, two fields are always derived by this module's own
rules rather than taken from the engine, because they feed the checks:

* ``year`` -- taken ONLY from a parenthetical that immediately follows the
  citation (after an optional pin cite), e.g. ``550 U.S. 544, 570 (2007)``.
  The year is the last 4-digit number in that parenthetical. eyecite 2.7.8
  can attach the *next* citation's year to a citation (its court/date regex
  can run past the closing parenthesis), so its year is recorded only as a
  note when it disagrees.
* ``case_name`` -- the "X v. Y" text between the previous citation and this
  one. eyecite's plaintiff/defendant are used only if they actually appear
  in that window; otherwise this module's look-back rule is used.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

# Kinds of citation. Only "case" citations are looked up and checked.
CASE = "case"
SHORT = "short_case"   # e.g. "Brown, 347 U.S. at 495"
ID = "id"              # "Id." / "Ibid."
SUPRA = "supra"
STATUTE = "statute"    # statutes and regulations
JOURNAL = "journal"
UNKNOWN = "unknown"

UNSUPPORTED_REASONS = {
    SHORT: "Short-form case citation; refers back to an earlier citation and is not looked up.",
    ID: "'Id.' reference; points to the previous authority and is not looked up.",
    SUPRA: "'Supra' reference; points to an earlier authority and is not looked up.",
    STATUTE: "Statute or regulation; this tool checks case law only.",
    JOURNAL: "Law journal citation; this tool checks case law only.",
    UNKNOWN: "Citation-like text the extractor could not classify.",
}


@dataclass
class Extracted:
    kind: str
    text: str                 # exactly as written in the brief
    start: int
    end: int
    volume: Optional[str] = None
    reporter: Optional[str] = None       # as written
    reporter_normalized: Optional[str] = None
    page: Optional[str] = None
    pin_cite: Optional[str] = None
    plaintiff: Optional[str] = None
    defendant: Optional[str] = None
    case_name: Optional[str] = None      # "Plaintiff v. Defendant" as written
    year: Optional[int] = None
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> Optional[str]:
        """Normalized lookup key: 'volume reporter page'."""
        if self.kind != CASE or not (self.volume and self.page):
            return None
        rep = self.reporter_normalized or self.reporter or ""
        return normalize_cite_string(f"{self.volume} {rep} {self.page}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d


def normalize_cite_string(s: str) -> str:
    """Collapse whitespace so '347  U.S.  483' == '347 U.S. 483'."""
    return re.sub(r"\s+", " ", s.strip())


# ---------------------------------------------------------------------------
# Shared rules: year from adjacent parenthetical, case name from look-back.
# ---------------------------------------------------------------------------

# Optional pin cite: ", 570" / ", 570-71" / ", at 495" / ", 494 n.5"
_PIN = r"(?:,\s*(?:at\s+)?\d+(?:\s*[-–]\s*\d+)?(?:\s*(?:,|&)\s*\d+(?:\s*[-–]\s*\d+)?)*(?:\s*n+\.\s*\d+)?)"
_ADJ_PAREN = re.compile(r"^(?P<pin>" + _PIN + r")?\s*\((?P<paren>[^()]{0,80})\)")
_YEAR = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")


def year_and_pin_after(text: str, end: int) -> tuple[Optional[int], Optional[str]]:
    """Year from the parenthetical immediately after a citation, if any."""
    m = _ADJ_PAREN.match(text[end:end + 160])
    if not m:
        pm = re.match(_PIN, text[end:end + 40])
        return None, (_clean_pin(pm.group(0)) if pm else None)
    years = _YEAR.findall(m.group("paren"))
    pin = _clean_pin(m.group("pin")) if m.group("pin") else None
    return (int(years[-1]) if years else None), pin


def _clean_pin(p: str) -> str:
    return re.sub(r"^,\s*", "", p).strip()


# "Plaintiff v. Defendant," immediately before the citation.
# Plaintiff: a run of capitalised words (plus a few connectors).
# Defendant: anything except ; ( ) and newlines, and no second " v. ".
_CONNECT = r"(?:of|the|and|for|ex|rel\.|de|la|du|von|van|&)"
_CAPWORD = r"(?:[A-Z0-9][\w.'’&\-]*,?)"
_NAME_RE = re.compile(
    r"(?P<pl>" + _CAPWORD + r"(?:\s+(?:" + _CAPWORD + "|" + _CONNECT + r"))*)"
    r"\s+v(?:s)?\.?\s+"
    r"(?P<df>(?:(?!\sv(?:s)?\.\s)[^;()\n])+?)"
    r",\s*$"
)
_SIGNALS = re.compile(
    r"^(?:(?:see|see also|see, e\.g\.,|see generally|cf\.|but see|but cf\.|accord|"
    r"compare|contra|e\.g\.,|also|and|in)\s+)+",
    re.IGNORECASE,
)


def case_name_before(text: str, start: int, window_start: int) -> tuple[Optional[str], Optional[str]]:
    """Look back from ``start`` (not past ``window_start``) for 'X v. Y,'."""
    lo = max(window_start, start - 240)
    window = text[lo:start]
    m = _NAME_RE.search(window)
    if not m:
        return None, None
    pl = _strip_signals(m.group("pl").strip().rstrip(","))
    df = m.group("df").strip().rstrip(",")
    if not pl or not df:
        return None, None
    return pl, df


def _strip_signals(s: str) -> str:
    # Do not strip "In" from "In re ...".
    if re.match(r"^in\s+re\b", s, re.IGNORECASE):
        return s
    return _SIGNALS.sub("", s).strip()


def _finish_case(c: Extracted, text: str, window_start: int,
                 engine_pl: Optional[str], engine_df: Optional[str],
                 engine_year: Optional[str]) -> None:
    # Year: adjacent-parenthetical rule.
    year, pin = year_and_pin_after(text, c.end)
    c.year = year
    if pin and not c.pin_cite:
        c.pin_cite = pin
    if engine_year and (year is None or str(year) != str(engine_year)):
        c.notes.append(
            f"extractor reported year {engine_year}; using year from the adjacent "
            f"parenthetical ({year if year is not None else 'none found'})"
        )
    # Case name: accept engine's only if it is really in the window before the cite.
    window = text[window_start:c.start]
    if engine_pl and engine_df and engine_pl in window and engine_df in window:
        c.plaintiff, c.defendant = _strip_signals(engine_pl.strip()), engine_df.strip()
    else:
        pl, df = case_name_before(text, c.start, window_start)
        if (engine_pl or engine_df) and (pl, df) != (engine_pl, engine_df):
            c.notes.append("extractor's case name was not found next to the citation; used look-back rule")
        c.plaintiff, c.defendant = pl, df
    if c.plaintiff and c.defendant:
        c.case_name = f"{c.plaintiff} v. {c.defendant}"


# ---------------------------------------------------------------------------
# eyecite engine
# ---------------------------------------------------------------------------

def eyecite_available() -> bool:
    try:
        import eyecite  # noqa: F401
        return True
    except Exception:
        return False


def eyecite_version() -> Optional[str]:
    try:
        from importlib.metadata import version
        return version("eyecite")
    except Exception:
        return None


def _extract_eyecite(text: str) -> list[Extracted]:
    logging.getLogger("eyecite").setLevel(logging.ERROR)
    from eyecite import get_citations
    from eyecite import models as m

    out: list[Extracted] = []
    prev_end = 0
    for cit in get_citations(text):
        start, end = cit.span()
        raw = text[start:end]
        md = cit.metadata
        if isinstance(cit, m.FullCaseCitation):
            g = cit.groups
            try:
                rep_norm = cit.corrected_reporter()
            except Exception:
                rep_norm = g.get("reporter")
            c = Extracted(CASE, raw, start, end, volume=g.get("volume"),
                          reporter=g.get("reporter"), reporter_normalized=rep_norm,
                          page=g.get("page"), pin_cite=getattr(md, "pin_cite", None))
            _finish_case(c, text, prev_end, getattr(md, "plaintiff", None),
                         getattr(md, "defendant", None), getattr(md, "year", None))
        elif isinstance(cit, m.ShortCaseCitation):
            c = Extracted(SHORT, raw, start, end, volume=cit.groups.get("volume"),
                          reporter=cit.groups.get("reporter"), page=cit.groups.get("page"))
        elif isinstance(cit, m.IdCitation):
            c = Extracted(ID, raw, start, end)
        elif isinstance(cit, m.SupraCitation):
            c = Extracted(SUPRA, raw, start, end)
        elif isinstance(cit, m.FullLawCitation):
            c = Extracted(STATUTE, raw, start, end)
        elif isinstance(cit, m.FullJournalCitation):
            c = Extracted(JOURNAL, raw, start, end)
        else:
            c = Extracted(UNKNOWN, raw, start, end)
        out.append(c)
        prev_end = end
    return out


# ---------------------------------------------------------------------------
# regex fallback engine
# ---------------------------------------------------------------------------

# Common reporters, canonical spellings only. Longest first so that
# "F. Supp. 2d" wins over "F.".
REGEX_REPORTERS = sorted([
    "U.S.", "S. Ct.", "L. Ed.", "L. Ed. 2d",
    "F.", "F.2d", "F.3d", "F.4th", "F. Supp.", "F. Supp. 2d", "F. Supp. 3d",
    "F. App'x", "Fed. Cl.", "B.R.",
    "A.", "A.2d", "A.3d", "P.", "P.2d", "P.3d",
    "N.E.", "N.E.2d", "N.E.3d", "N.W.", "N.W.2d", "S.E.", "S.E.2d",
    "S.W.", "S.W.2d", "S.W.3d", "So.", "So. 2d", "So. 3d",
    "Cal. Rptr.", "Cal. Rptr. 2d", "Cal. Rptr. 3d", "N.Y.S.2d", "N.Y.S.3d",
], key=len, reverse=True)

_REP_ALT = "|".join(re.escape(r).replace(r"\ ", r"\s+") for r in REGEX_REPORTERS)
_CASE_RE = re.compile(
    r"(?<![\w.])(?P<volume>\d{1,4})\s+(?P<reporter>" + _REP_ALT + r")\s+(?P<page>\d{1,5})(?![\w])"
)
_SHORT_RE = re.compile(
    r"(?<![\w.])(?P<volume>\d{1,4})\s+(?P<reporter>" + _REP_ALT + r")\s+at\s+(?P<page>\d{1,5})"
)
_STATUTE_RE = re.compile(
    r"\b\d+\s+(?:U\.S\.C\.A?|C\.F\.R\.)\s*§{1,2}\s*[\w.\-()]+"
    r"|§{1,2}\s*\d[\w.\-()]*"
)
_ID_RE = re.compile(r"(?<![\w.])(?:Id\.|id\.|Ibid\.|ibid\.)")
_SUPRA_RE = re.compile(r"\bsupra\b")


def _extract_regex(text: str) -> list[Extracted]:
    found: list[Extracted] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in taken)

    for rx, kind in ((_STATUTE_RE, STATUTE), (_SHORT_RE, SHORT), (_CASE_RE, CASE),
                     (_ID_RE, ID), (_SUPRA_RE, SUPRA)):
        for mt in rx.finditer(text):
            s, e = mt.span()
            if not free(s, e):
                continue
            taken.append((s, e))
            c = Extracted(kind, text[s:e], s, e)
            if kind in (CASE, SHORT):
                c.volume = mt.group("volume")
                c.reporter = normalize_cite_string(mt.group("reporter"))
                c.reporter_normalized = c.reporter
                c.page = mt.group("page")
            found.append(c)
    found.sort(key=lambda c: c.start)
    prev_end = 0
    for c in found:
        if c.kind == CASE:
            _finish_case(c, text, prev_end, None, None, None)
        prev_end = c.end
    return found


# ---------------------------------------------------------------------------

def extract(text: str, engine: str = "auto") -> tuple[list[Extracted], dict]:
    """Return (citations, extractor_info)."""
    if engine == "auto":
        engine = "eyecite" if eyecite_available() else "regex"
    if engine == "eyecite":
        if not eyecite_available():
            raise RuntimeError("eyecite is not installed (pip install 'citation-preflight[eyecite]')")
        cits = _extract_eyecite(text)
        info = {"name": "eyecite", "version": eyecite_version()}
    elif engine == "regex":
        cits = _extract_regex(text)
        info = {"name": "regex-fallback", "version": "1",
                "reporters": len(REGEX_REPORTERS)}
    else:
        raise ValueError(f"unknown extractor {engine!r}")
    return cits, info
