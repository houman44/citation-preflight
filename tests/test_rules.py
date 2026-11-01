import pytest

from citation_preflight import names
from citation_preflight.extract import CASE, ID, STATUTE, SUPRA, Extracted
from citation_preflight.rules import (BLOCK, FAIL, NOT_CHECKED, PASS, REVIEW, WARN, check_case_name,
                                      check_resolves, check_year, combine, evaluate, overall)

from conftest import cluster, entry


def case(name="Brown v. Board of Education", year=1954):
    c = Extracted(CASE, "347 U.S. 483", 0, 12, volume="347", reporter="U.S.",
                  reporter_normalized="U.S.", page="483", year=year)
    if name:
        c.plaintiff, c.defendant = name.split(" v. ")
        c.case_name = name
    return c


# --- R1 resolves -----------------------------------------------------------

@pytest.mark.parametrize("status,clusters,expected", [
    (200, [cluster("A v. B", "2000-01-01")], PASS),
    (300, [cluster("A v. B", "2000-01-01"), cluster("C v. D", "2001-01-01")], PASS),
    (404, [], FAIL),
    (400, [], WARN),
    (429, [], NOT_CHECKED),
    (200, [], WARN),   # 'found' but no record: cannot verify, never PASS
    (500, [], NOT_CHECKED),
])
def test_resolves(status, clusters, expected):
    assert check_resolves(entry(status, clusters), "x")["result"] == expected


def test_resolves_missing_entry_is_not_checked():
    r = check_resolves(None, "not in fixture set")
    assert r["result"] == NOT_CHECKED and "not in fixture set" in r["evidence"]


def test_not_found_says_no_such_citation():
    r = check_resolves(entry(404, msg="Citation not found: '925 F.3d 1339'"), "x")
    assert "No such citation" in r["evidence"] and "925 F.3d 1339" in r["evidence"]


# --- R2 ambiguous ------------------------------------------------------------

def test_ambiguous_is_warn_and_lists_candidates():
    e = entry(300, [cluster("Louis v. Steamboat Buckeye", "1855-01-01"), cluster("Fell v. Parke", "1855-01-01")])
    rec = evaluate(case("Fell v. Parke", 1855), e, "x")
    r2 = next(c for c in rec["checks"] if c["rule"] == "R2")
    assert r2["result"] == WARN and "Fell v. Parke" in r2["evidence"]
    assert rec["verdict"] == WARN


# --- R3 case name ------------------------------------------------------------

@pytest.mark.parametrize("brief,resolved,match", [
    ("Brown v. Board of Education", "Brown v. Board of Education", True),
    ("Brown v. Board of Education", "Oliver Brown v. Board of Education of Topeka", True),
    ("Celotex Corp. v. Catrett", "Celotex Corp. v. Catrett, Administratrix of the Estate of Catrett", True),
    ("Later, Miranda v. Arizona", "Miranda v. Arizona", True),
    ("Arizona v. Miranda", "Miranda v. Arizona", True),               # reversed on appeal
    ("BROWN v. BOARD OF EDUCATION", "Brown v. Board of Education", True),
    ("Ashcroft v. Iqbal", "Bell Atlantic Corp. v. Twombly", False),
    ("United States v. Smith", "United States v. Jones", False),    # shared party is not enough
    ("Brown v. Delta Air Lines", "Brown v. Board of Education", False),
    ("In re Gault", "In re Gault", True),                              # overlap fallback
    ("In re Gault", "In re Winship", False),
])
def test_name_rule(brief, resolved, match):
    assert names.compare(brief, resolved)["match"] is match


def test_name_rule_uses_case_name_full_too():
    e = entry(200, [cluster("Short Name v. X", "2000-01-01", full="Brown v. Board of Education of Topeka")])
    r, matched = check_case_name(case(), e)
    assert r["result"] == PASS and matched == [0]


def test_name_mismatch_fails_with_evidence():
    e = entry(200, [cluster("Bell Atlantic Corp. v. Twombly", "2007-05-21")])
    r, _ = check_case_name(case("Ashcroft v. Iqbal", 2009), e)
    assert r["result"] == FAIL
    assert "Ashcroft v. Iqbal" in r["evidence"] and "Twombly" in r["evidence"]


def test_missing_name_in_brief_is_warn():
    r, _ = check_case_name(case(name=None), entry(200, [cluster("A v. B", "2000-01-01")]))
    assert r["result"] == WARN


# --- R4 year -----------------------------------------------------------------

@pytest.mark.parametrize("year,expected", [(1954, PASS), (1955, WARN), (1953, WARN), (1956, FAIL), (None, WARN)])
def test_year_rule(year, expected):
    e = entry(200, [cluster("Brown v. Board of Education", "1954-05-17")])
    assert check_year(case(year=year), e)["result"] == expected


def test_year_without_date_filed_is_warn():
    assert check_year(case(), entry(200, [cluster("Brown v. Board", "")]))["result"] == WARN


def test_year_uses_name_matched_candidate_when_ambiguous():
    e = entry(300, [cluster("Other v. Case", "1954-01-01"), cluster("Brown v. Board of Education", "1960-01-01")])
    rec = evaluate(case(year=1954), e, "x")
    r4 = next(c for c in rec["checks"] if c["rule"] == "R4")
    assert r4["result"] == FAIL      # the 1954 candidate is a different case


# --- unsupported forms and verdicts -------------------------------------------

@pytest.mark.parametrize("kind", [ID, SUPRA, STATUTE])
def test_unsupported_forms_never_pass(kind):
    rec = evaluate(Extracted(kind, "Id.", 0, 3), None, "x")
    assert rec["verdict"] == NOT_CHECKED
    assert rec["checks"][0]["result"] == NOT_CHECKED


def test_not_found_skips_name_and_year():
    rec = evaluate(case("Varghese v. China Southern Airlines Co.", 2019), entry(404), "x")
    assert rec["verdict"] == FAIL
    assert [c["result"] for c in rec["checks"]] == [FAIL, NOT_CHECKED, NOT_CHECKED]


def test_genuine_citation_passes():
    rec = evaluate(case(), entry(200, [cluster("Brown v. Board of Education", "1954-05-17")]), "x")
    assert rec["verdict"] == PASS
    assert [c["result"] for c in rec["checks"]] == [PASS, PASS, PASS, PASS]


@pytest.mark.parametrize("results,expected", [
    ([PASS, PASS], PASS), ([PASS, WARN], WARN), ([WARN, FAIL], FAIL),
    ([PASS, NOT_CHECKED], NOT_CHECKED), ([NOT_CHECKED, WARN], WARN),
])
def test_combine(results, expected):
    assert combine(results) == expected


@pytest.mark.parametrize("verdicts,expected", [
    ([PASS, PASS], PASS),
    ([PASS, WARN], REVIEW),
    ([PASS, NOT_CHECKED], REVIEW),
    ([PASS, NOT_CHECKED, FAIL], BLOCK),
    ([FAIL], BLOCK),
    ([], REVIEW),            # nothing found is not a pass
])
def test_overall(verdicts, expected):
    assert overall(verdicts)[0] == expected
