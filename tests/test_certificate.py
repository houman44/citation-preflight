import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest

from citation_preflight.certificate import envelope, verify
from citation_preflight.core import run_check
from citation_preflight.extract import eyecite_available
from citation_preflight.resolvers import FixtureResolver, canonical_json

ENGINES = ["regex"] + (["eyecite"] if eyecite_available() else [])


@pytest.mark.parametrize("engine", ENGINES)
def test_demo_verdicts(engine, demo_bytes):
    body = run_check(demo_bytes, FixtureResolver(), engine)
    assert body["overall"]["verdict"] == "BLOCK"
    assert [c["verdict"] for c in body["citations"]] == [
        "FAIL", "NOT CHECKED", "FAIL", "PASS", "PASS", "NOT CHECKED"]
    assert body["overall"]["counts"] == {"PASS": 2, "FAIL": 2, "WARN": 0, "NOT CHECKED": 2}
    assert body["resolver"]["synthetic"] is True


@pytest.mark.parametrize("engine", ENGINES)
def test_reproducible_byte_identical_body(engine, demo_bytes):
    a = run_check(demo_bytes, FixtureResolver(), engine)
    b = run_check(demo_bytes, FixtureResolver(), engine)
    assert canonical_json(a) == canonical_json(b)
    e1 = envelope(a, "x", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    e2 = envelope(b, "y", now=datetime(2027, 6, 1, tzinfo=timezone.utc))
    # timestamp and path are outside the hashed body
    assert e1["certificate_sha256"] == e2["certificate_sha256"]
    assert e1["generated_at"] != e2["generated_at"]


def test_body_has_no_timestamp_or_path(demo_bytes):
    body = run_check(demo_bytes, FixtureResolver(), "regex")
    blob = canonical_json(body).decode()
    assert "generated_at" not in blob and "/home/" not in blob


def test_input_hash_and_any_change_changes_certificate(demo_bytes):
    body = run_check(demo_bytes, FixtureResolver(), "regex")
    assert body["input"]["sha256"] == hashlib.sha256(demo_bytes).hexdigest()
    body2 = run_check(demo_bytes + b" ", FixtureResolver(), "regex")
    assert envelope(body, "x")["certificate_sha256"] != envelope(body2, "x")["certificate_sha256"]


def test_verify_detects_tampering(demo_bytes):
    env = envelope(run_check(demo_bytes, FixtureResolver(), "regex"), "x")
    assert verify(env)[0]
    forged = copy.deepcopy(env)
    forged["certificate"]["overall"]["verdict"] = "PASS"
    assert not verify(forged)[0]
    # editing unhashed metadata does not break verification (by design)
    env["generated_at"] = "1999-01-01T00:00:00Z"
    assert verify(env)[0]


def test_certificate_hash_is_sha256_of_canonical_body(demo_bytes):
    env = envelope(run_check(demo_bytes, FixtureResolver(), "regex"), "x")
    canon = json.dumps(env["certificate"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert env["certificate_sha256"] == hashlib.sha256(canon).hexdigest()


def test_fixture_change_changes_certificate(tmp_path, demo_bytes):
    from importlib import resources
    raw = json.loads(resources.files("citation_preflight.fixtures")
                     .joinpath("synthetic_courtlistener_lookups.json").read_text())
    raw["lookups"]["925 F.3d 1339"] = {"status": 200, "clusters": [
        {"case_name": "Varghese v. China Southern Airlines Co.", "date_filed": "2019-01-01"}]}
    p = tmp_path / "fx.json"
    p.write_text(json.dumps(raw))
    a = run_check(demo_bytes, FixtureResolver(), "regex")
    b = run_check(demo_bytes, FixtureResolver(p), "regex")
    assert canonical_json(a) != canonical_json(b)
    assert b["citations"][2]["verdict"] == "PASS"


def test_citation_not_in_fixtures_is_not_checked_not_failed():
    text = "Celotex Corp. v. Catrett, 477 U.S. 317 (1986)."
    body = run_check(text.encode(), FixtureResolver(), "regex")
    c = body["citations"][0]
    assert c["verdict"] == "NOT CHECKED"
    assert body["overall"]["verdict"] == "REVIEW"


def test_no_citations_is_review():
    body = run_check(b"No authorities here.", FixtureResolver(), "regex")
    assert body["overall"]["verdict"] == "REVIEW" and body["citations"] == []
