"""LiveResolver: request construction and error handling, with mocked HTTP."""

import json
import urllib.parse

import pytest

from citation_preflight.core import run_check
from citation_preflight.extract import extract
from citation_preflight.resolvers import (API_URL, MAX_TEXT_CHARS, LiveResolver, ResolverError,
                                          ResolverThrottled, match_entries)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        status, body = self.responses.pop(0)
        return status, body if isinstance(body, bytes) else json.dumps(body).encode()


def api_entry(text, cite, status, clusters=(), normalized=None, msg=""):
    s = text.index(cite)
    return {"citation": cite, "normalized_citations": normalized or [cite], "start_index": s,
            "end_index": s + len(cite), "status": status, "error_message": msg, "clusters": list(clusters)}


TEXT = ("Ashcroft v. Iqbal, 550 U.S. 544, 570 (2009). "
        "Varghese v. China Southern Airlines Co., 925 F.3d 1339 (11th Cir. 2019).")
TWOMBLY = {"id": 1, "case_name": "Bell Atlantic Corp. v. Twombly", "case_name_full": "",
           "date_filed": "2007-05-21", "absolute_url": "/opinion/1/x/", "extra_field": "ignored"}


def test_request_is_documented_post_with_token_and_form_text():
    t = FakeTransport((200, []))
    LiveResolver("abc123", transport=t).lookup("Some text 1 U.S. 1", [])
    call = t.calls[0]
    assert call["url"] == API_URL == "https://www.courtlistener.com/api/rest/v4/citation-lookup/"
    assert call["headers"]["Authorization"] == "Token abc123"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(call["data"].decode()) == {"text": ["Some text 1 U.S. 1"]}


def test_default_transport_uses_post(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def read(self): return b"[]"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        seen["method"], seen["auth"] = req.get_method(), req.get_header("Authorization")
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    LiveResolver("tok").lookup("x", [])
    assert seen == {"method": "POST", "auth": "Token tok"}


@pytest.mark.parametrize("token", [None, "", "   "])
def test_missing_token_fails_cleanly_without_network(token):
    t = FakeTransport()
    with pytest.raises(ResolverError, match="COURTLISTENER_TOKEN"):
        LiveResolver(token, transport=t).lookup("x", [])
    assert t.calls == []


def test_text_over_documented_limit_is_refused_before_sending():
    t = FakeTransport()
    with pytest.raises(ResolverError, match="64,000"):
        LiveResolver("tok", transport=t).lookup("a" * (MAX_TEXT_CHARS + 1), [])
    assert t.calls == []


@pytest.mark.parametrize("key", ["wait_until", "wait_util"])
def test_http_429_reports_wait_until(key):
    t = FakeTransport((429, {key: "2026-10-31T12:00:00Z"}))
    with pytest.raises(ResolverThrottled) as ei:
        LiveResolver("tok", transport=t).lookup("x", [])
    assert ei.value.wait_until == "2026-10-31T12:00:00Z"


@pytest.mark.parametrize("status,body,match", [
    (401, {"detail": "Invalid token."}, "rejected the token"),
    (403, {"detail": "no"}, "rejected the token"),
    (500, b"boom", "HTTP 500"),
    (200, b"not json", "invalid JSON"),
    (200, {"not": "a list"}, "not a list"),
    (200, [{"citation": "x"}], "not a list"),
])
def test_http_errors(status, body, match):
    with pytest.raises(ResolverError, match=match):
        LiveResolver("tok", transport=FakeTransport((status, body))).lookup("x", [])


def test_network_error_is_resolver_error(monkeypatch):
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ResolverError, match="could not reach"):
        LiveResolver("tok").lookup("x", [])


def test_client_side_throttle_waits_for_budget():
    now = [0.0]
    slept = []
    sixty = [{"status": 200, "citation": f"{i} U.S. 1", "clusters": []} for i in range(60)]
    t = FakeTransport((200, sixty), (200, []))
    r = LiveResolver("tok", transport=t, clock=lambda: now[0],
                     sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    r.lookup("x", [])
    now[0] = 10.0
    r.lookup("y", [])
    assert slept == [50.0]            # waited until the 60-second window freed up
    assert len(t.calls) == 2


def test_client_side_throttle_gives_up_when_wait_too_long():
    sixty = [{"status": 404, "citation": "1 U.S. 1", "clusters": []}] * 60
    r = LiveResolver("tok", transport=FakeTransport((200, sixty)), clock=lambda: 0.0,
                     sleep=lambda s: None, max_wait_s=5)
    r.lookup("x", [])
    with pytest.raises(ResolverThrottled):
        r.lookup("y", [])


def test_end_to_end_with_mocked_response():
    resp = [api_entry(TEXT, "550 U.S. 544", 200, [TWOMBLY]),
            api_entry(TEXT, "925 F.3d 1339", 404, msg="Citation not found: '925 F.3d 1339'")]
    r = LiveResolver("tok", transport=FakeTransport((200, resp)))
    body = run_check(TEXT.encode(), r, "regex")
    assert [c["verdict"] for c in body["citations"]] == ["FAIL", "FAIL"]
    assert body["resolver"]["name"] == "courtlistener-v4"
    assert len(body["resolver"]["response_sha256"]) == 64
    # only the documented fields we use are copied into the certificate
    assert "extra_field" not in json.dumps(body)


def test_matching_uses_normalized_citations():
    from citation_preflight.extract import CASE, Extracted
    resp = [{"citation": "576 US 644", "normalized_citations": ["576 U.S. 644"], "start_index": 21,
             "end_index": 31, "status": 200, "error_message": "", "clusters": []}]
    c = Extracted(CASE, "576 US 644", 21, 31, volume="576", reporter="US",
                  reporter_normalized="U.S.", page="644")
    matches, unmatched = match_entries([c], resp)
    assert matches[0] is resp[0] and not unmatched


def test_citation_found_only_by_resolver_is_still_checked():
    text = "Obergefell v. Hodges, 576 US 644 (2016)."
    resp = [{"citation": "576 US 644", "normalized_citations": ["576 U.S. 644"], "start_index": 21,
             "end_index": 31, "status": 200, "error_message": "",
             "clusters": [{"case_name": "Obergefell v. Hodges", "date_filed": "2015-06-26"}]}]
    body = run_check(text.encode(), LiveResolver("t", transport=FakeTransport((200, resp))), "regex")
    c = body["citations"][0]
    assert c["parsed"]["case_name_in_brief"] == "Obergefell v. Hodges"
    assert c["verdict"] == "WARN"  # year off by one
    assert "found by the resolver" in c["extraction_notes"][0]


def test_per_citation_429_is_not_checked():
    resp = [api_entry(TEXT, "550 U.S. 544", 429, msg="Too many citations requested."),
            api_entry(TEXT, "925 F.3d 1339", 429, msg="Too many citations requested.")]
    body = run_check(TEXT.encode(), LiveResolver("t", transport=FakeTransport((200, resp))), "regex")
    assert [c["verdict"] for c in body["citations"]] == ["NOT CHECKED", "NOT CHECKED"]
    assert body["overall"]["verdict"] == "REVIEW"


def test_same_citation_twice_matches_by_position():
    text = "A v. B, 1 U.S. 1 (1800). C v. D, 1 U.S. 1 (1800)."
    s2 = text.rindex("1 U.S. 1")
    resp = [{"citation": "1 U.S. 1", "normalized_citations": ["1 U.S. 1"], "start_index": s2,
             "end_index": s2 + 8, "status": 404, "error_message": "", "clusters": []},
            {"citation": "1 U.S. 1", "normalized_citations": ["1 U.S. 1"], "start_index": 8,
             "end_index": 16, "status": 200, "error_message": "", "clusters": []}]
    cits, _ = extract(text, "regex")
    matches, unmatched = match_entries(cits, resp)
    assert matches[0]["start_index"] == 8 and matches[1]["start_index"] == s2 and not unmatched
