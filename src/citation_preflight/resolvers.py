"""Pluggable citation resolvers.

Every resolver returns a list of per-citation objects in the shape documented
for CourtListener's Citation Lookup API v4
(https://wiki.free.law/c/courtlistener/help/api/rest/v4/citation-lookup):

    {
      "citation": "576 U.S. 644",
      "normalized_citations": ["576 U.S. 644"],
      "start_index": 22,
      "end_index": 34,
      "status": 200,            # 200 found | 300 ambiguous | 400 bad reporter
                                # 404 not found | 429 not looked up (over per-request cap)
      "error_message": "",
      "clusters": [ {...CourtListener cluster object...} ]
    }

Only these cluster fields are read: case_name, case_name_full, date_filed,
absolute_url.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Callable, Optional

from . import __version__
from .extract import CASE, Extracted, normalize_cite_string

API_URL = "https://www.courtlistener.com/api/rest/v4/citation-lookup/"
MAX_TEXT_CHARS = 64_000          # documented cap on `text`
THROTTLE_CITATIONS = 60          # documented: 60 valid citations ...
THROTTLE_PERIOD_S = 60           # ... per minute
MAX_PER_REQUEST = 250            # documented: later citations get status 429
VALID_STATUSES = (200, 300, 404)  # what counts as a "valid citation" for the throttle

DEFAULT_FIXTURES = "synthetic_courtlistener_lookups.json"


class ResolverError(Exception):
    """The resolver could not produce results; the check must not pass."""


class ResolverThrottled(ResolverError):
    def __init__(self, message: str, wait_until: Optional[str] = None):
        super().__init__(message)
        self.wait_until = wait_until


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Fixture resolver (offline, synthetic data)
# ---------------------------------------------------------------------------

class FixtureResolver:
    """Answers from a JSON file of per-citation objects keyed by 'vol reporter page'.

    The default file is SYNTHETIC: written by hand in CourtListener's documented
    format. Run with --live and a token for real lookups.
    """

    name = "fixture"
    missing_reason = "not in the synthetic fixture set (offline mode); run --live for a real lookup"

    def __init__(self, path: Optional[str | Path] = None):
        if path is None:
            raw = resources.files("citation_preflight.fixtures").joinpath(DEFAULT_FIXTURES).read_bytes()
            self.source = DEFAULT_FIXTURES
        else:
            raw = Path(path).read_bytes()
            self.source = Path(path).name
        self.sha256 = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        self.synthetic = bool(data.get("_synthetic", False))
        self.lookups = {normalize_cite_string(k): v for k, v in data["lookups"].items()}

    def lookup(self, text: str, citations: list[Extracted]) -> list[dict]:
        out = []
        for c in citations:
            if c.kind != CASE or c.key is None or c.key not in self.lookups:
                continue
            fx = self.lookups[c.key]
            out.append({
                "citation": c.text,
                "normalized_citations": list(fx.get("normalized_citations", [c.key])),
                "start_index": c.start,
                "end_index": c.end,
                "status": fx["status"],
                "error_message": fx.get("error_message", ""),
                "clusters": list(fx.get("clusters", [])),
            })
        return out

    def describe(self) -> dict:
        return {"name": "fixture", "synthetic": self.synthetic,
                "source": self.source, "source_sha256": self.sha256}


# ---------------------------------------------------------------------------
# Live resolver: CourtListener citation-lookup API v4
# ---------------------------------------------------------------------------

Transport = Callable[[str, bytes, dict, float], tuple[int, bytes]]


def urllib_transport(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ResolverError(f"could not reach CourtListener: {e}") from e


class LiveResolver:
    """POST the brief's text to CourtListener and return the documented response list.

    Client-side throttle: keeps an in-process log of valid citations sent and,
    before each request, waits until fewer than 60 have been sent in the last
    60 seconds (or raises if that would take longer than ``max_wait_s``). The
    log is per process; the server's own 429 is handled as an error.
    """

    name = "courtlistener-v4"
    missing_reason = "CourtListener did not return a result for this citation"

    def __init__(self, token: Optional[str], transport: Optional[Transport] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 30.0, max_wait_s: float = 90.0,
                 url: str = API_URL):
        self.token = (token or "").strip()
        self.transport = transport or urllib_transport
        self.clock, self.sleep = clock, sleep
        self.timeout, self.max_wait_s, self.url = timeout, max_wait_s, url
        self._sent: list[tuple[float, int]] = []
        self.response_sha256: Optional[str] = None

    # -- throttle ---------------------------------------------------------
    def _wait_for_budget(self) -> None:
        now = self.clock()
        self._sent = [(t, n) for t, n in self._sent if now - t < THROTTLE_PERIOD_S]
        used = sum(n for _, n in self._sent)
        if used < THROTTLE_CITATIONS:
            return
        wait = self._sent[0][0] + THROTTLE_PERIOD_S - now
        if wait > self.max_wait_s:
            raise ResolverThrottled(f"client-side throttle: {used} citations sent in the last minute")
        self.sleep(max(wait, 0.0))
        self._wait_for_budget()

    # -- request ----------------------------------------------------------
    def build_request(self, text: str) -> tuple[str, bytes, dict]:
        body = urllib.parse.urlencode({"text": text}).encode("utf-8")
        headers = {
            "Authorization": f"Token {self.token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": f"citation-preflight/{__version__}",
        }
        return self.url, body, headers

    def lookup(self, text: str, citations: list[Extracted]) -> list[dict]:
        if not self.token:
            raise ResolverError(
                "COURTLISTENER_TOKEN is not set. Live mode needs a CourtListener API token "
                "(free account at courtlistener.com). Without it, run offline with fixtures."
            )
        if len(text) > MAX_TEXT_CHARS:
            raise ResolverError(
                f"input is {len(text):,} characters; CourtListener accepts at most "
                f"{MAX_TEXT_CHARS:,} per request (chunking is not implemented in this prototype)"
            )
        self._wait_for_budget()
        url, body, headers = self.build_request(text)
        status, raw = self.transport(url, body, headers, self.timeout)

        if status == 429:
            wait_until = None
            try:
                j = json.loads(raw or b"{}")
                # Docs name the key `wait_util` in prose; accept either spelling.
                wait_until = j.get("wait_until") or j.get("wait_util")
            except Exception:
                pass
            raise ResolverThrottled(
                f"CourtListener throttled the request (HTTP 429); retry after {wait_until or 'a minute'}",
                wait_until)
        if status in (401, 403):
            raise ResolverError(f"CourtListener rejected the token (HTTP {status})")
        if status != 200:
            snippet = (raw or b"")[:200].decode("utf-8", "replace")
            raise ResolverError(f"CourtListener returned HTTP {status}: {snippet}")
        try:
            data = json.loads(raw)
        except Exception as e:
            raise ResolverError(f"CourtListener returned invalid JSON: {e}") from e
        if not isinstance(data, list) or not all(isinstance(x, dict) and "status" in x for x in data):
            raise ResolverError("CourtListener response is not a list of citation objects")

        valid = sum(1 for x in data if x.get("status") in VALID_STATUSES)
        self._sent.append((self.clock(), valid))
        self.response_sha256 = hashlib.sha256(canonical_json(data)).hexdigest()
        return data

    def describe(self) -> dict:
        return {"name": self.name, "endpoint": self.url, "synthetic": False,
                "response_sha256": self.response_sha256}


# ---------------------------------------------------------------------------
# Matching resolver output back to extracted citations
# ---------------------------------------------------------------------------

_TRIAD = re.compile(r"^(?P<volume>\d+)\s+(?P<reporter>.+?)\s+(?P<page>\d+)$")


def entry_keys(entry: dict) -> set[str]:
    keys = {normalize_cite_string(entry.get("citation", ""))}
    keys |= {normalize_cite_string(x) for x in entry.get("normalized_citations") or []}
    keys.discard("")
    return keys


def match_entries(citations: list[Extracted], entries: list[dict]) -> tuple[dict[int, dict], list[dict]]:
    """Map citation index -> resolver entry by normalized key, nearest start_index.

    Returns (matches, unmatched_entries). Deterministic: ties broken by order.
    """
    used: set[int] = set()
    matches: dict[int, dict] = {}
    for i, c in enumerate(citations):
        if c.kind != CASE:
            continue
        wanted = {k for k in (c.key, normalize_cite_string(c.text)) if k}
        best, best_d = None, None
        for j, e in enumerate(entries):
            if j in used or not (entry_keys(e) & wanted):
                continue
            d = abs(int(e.get("start_index") or 0) - c.start)
            if best is None or d < best_d:
                best, best_d = j, d
        if best is not None:
            used.add(best)
            matches[i] = entries[best]
    unmatched = [e for j, e in enumerate(entries) if j not in used]
    return matches, unmatched


def citation_from_entry(entry: dict, text: str) -> Extracted:
    """Build an Extracted record for a citation the resolver found but we did not."""
    from .extract import case_name_before, year_and_pin_after
    cite = normalize_cite_string((entry.get("normalized_citations") or [entry.get("citation", "")])[0])
    m = _TRIAD.match(cite)
    s, e = entry.get("start_index"), entry.get("end_index")
    c = Extracted(CASE, entry.get("citation", cite), int(s or 0), int(e or 0))
    if m:
        c.volume, c.reporter, c.page = m["volume"], m["reporter"], m["page"]
        c.reporter_normalized = m["reporter"]
    c.notes.append("found by the resolver but not by the local extractor")
    sane = isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text)
    if sane:
        c.year, c.pin_cite = year_and_pin_after(text, e)
        pl, df = case_name_before(text, s, 0)
        if pl and df:
            c.plaintiff, c.defendant, c.case_name = pl, df, f"{pl} v. {df}"
    return c
