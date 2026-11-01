import pytest

from citation_preflight.extract import (CASE, ID, SHORT, STATUTE, SUPRA, case_name_before,
                                        extract, eyecite_available, year_and_pin_after)

ENGINES = ["regex"] + (["eyecite"] if eyecite_available() else [])

EXPECTED_DEMO = [
    (CASE, "550 U.S. 544", "Ashcroft v. Iqbal", 2009),
    (ID, None, None, None),
    (CASE, "925 F.3d 1339", "Varghese v. China Southern Airlines Co.", 2019),
    (CASE, "347 U.S. 483", "Brown v. Board of Education", 1954),
    (CASE, "384 U.S. 436", "Miranda v. Arizona", 1966),
    (STATUTE, None, None, None),
]


@pytest.mark.parametrize("engine", ENGINES)
def test_demo_brief_extraction(engine, demo_text):
    cits, info = extract(demo_text, engine)
    got = [(c.kind, c.key, c.case_name, c.year) for c in cits]
    assert got == EXPECTED_DEMO
    assert info["name"] in ("eyecite", "regex-fallback")


@pytest.mark.parametrize("engine", ENGINES)
def test_spans_point_at_the_citation(engine, demo_text):
    cits, _ = extract(demo_text, engine)
    for c in cits:
        assert demo_text[c.start:c.end] == c.text
    ash = cits[0]
    assert (ash.volume, ash.page) == ("550", "544")
    assert ash.pin_cite == "570"


@pytest.mark.parametrize("engine", ENGINES)
def test_unsupported_forms_are_classified(engine):
    text = ("Brown v. Board of Education, 347 U.S. 483, 495 (1954). Id. at 495. "
            "Brown, 347 U.S. at 494. See Brown, supra, at 494. 42 U.S.C. § 1983.")
    kinds = [c.kind for c in extract(text, engine)[0]]
    assert kinds[0] == CASE
    assert ID in kinds and SUPRA in kinds and STATUTE in kinds and SHORT in kinds


@pytest.mark.skipif(not eyecite_available(), reason="eyecite not installed")
def test_year_is_not_borrowed_from_the_next_citation():
    # eyecite 2.7.8 attaches the *next* citation's year (2019) here; our rule must not.
    text = ("Ashcroft v. Iqbal, 550 U.S. 544, 570 (2009). Id. at 556. "
            "Varghese v. China Southern Airlines Co., 925 F.3d 1339 (11th Cir. 2019).")
    c = extract(text, "eyecite")[0][0]
    assert c.year == 2009
    assert any("2019" in n for n in c.notes)


def test_year_rule_requires_adjacent_parenthetical():
    t = "347 U.S. 483 and later (1954)"
    assert year_and_pin_after(t, len("347 U.S. 483"))[0] is None
    t = "347 U.S. 483, 495 (1954)"
    assert year_and_pin_after(t, len("347 U.S. 483")) == (1954, "495")
    t = "925 F.3d 1339 (11th Cir. 2019)"
    assert year_and_pin_after(t, len("925 F.3d 1339")) == (2019, None)


def test_case_name_lookback_and_signals():
    t = "as held. See also Smith & Co. v. Jones Trucking, Inc., 100 F.3d 1"
    s = t.index("100 F.3d")
    assert case_name_before(t, s, 0) == ("Smith & Co.", "Jones Trucking, Inc.")
    t = "In re Gault, 387 U.S. 1"
    assert case_name_before(t, t.index("387"), 0) == (None, None)  # no 'v.' -> no name


def test_case_name_does_not_cross_previous_citation():
    t = "Roe v. Wade, 1 U.S. 1 (1900); 2 U.S. 2 (1901)"
    s = t.index("2 U.S. 2")
    prev_end = t.index("(1900)") + len("(1900)")
    assert case_name_before(t, s, prev_end) == (None, None)


def test_regex_fallback_handles_multiword_reporters():
    cits, _ = extract("Doe v. Roe, 100 F. Supp. 2d 200 (S.D.N.Y. 2000).", "regex")
    assert cits[0].key == "100 F. Supp. 2d 200"
    assert cits[0].year == 2000


def test_unknown_engine_rejected():
    with pytest.raises(ValueError):
        extract("x", "nope")
