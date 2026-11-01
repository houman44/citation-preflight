import json
import re

from citation_preflight.cli import main

from conftest import DEMO


def test_check_writes_reports_and_exits_block(tmp_path, capsys):
    out, html = tmp_path / "r.json", tmp_path / "r.html"
    code = main(["check", str(DEMO), "--out", str(out), "--html", str(html)])
    assert code == 1
    env = json.loads(out.read_text())
    assert env["certificate"]["overall"]["verdict"] == "BLOCK"
    page = html.read_text()
    assert "BLOCK" in page and env["certificate_sha256"] in page
    assert env["certificate"]["input"]["sha256"] in page
    assert "SYNTHETIC" in page
    # single file: no external stylesheets, scripts or images
    assert not re.search(r"<(link|script|img)\b", page)
    term = capsys.readouterr().out
    assert "OVERALL: BLOCK" in term and "SYNTHETIC" in term


def test_verify_roundtrip_and_tamper(tmp_path, capsys):
    out = tmp_path / "r.json"
    main(["check", str(DEMO), "--out", str(out), "--quiet"])
    assert main(["verify", str(out)]) == 0
    env = json.loads(out.read_text())
    env["certificate"]["citations"][0]["verdict"] = "PASS"
    out.write_text(json.dumps(env))
    assert main(["verify", str(out)]) == 1


def test_live_without_token_exits_4_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("COURTLISTENER_TOKEN", raising=False)
    out = tmp_path / "r.json"
    assert main(["check", str(DEMO), "--live", "--out", str(out)]) == 4
    assert not out.exists()
    assert "COURTLISTENER_TOKEN" in capsys.readouterr().err


def test_pass_and_review_exit_codes(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("See Brown v. Board of Education, 347 U.S. 483 (1954).")
    assert main(["check", str(good), "--quiet"]) == 0
    review = tmp_path / "review.txt"
    review.write_text("See Brown v. Board of Education, 347 U.S. 483 (1955).")
    assert main(["check", str(review), "--quiet"]) == 3
