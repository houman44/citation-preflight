"""Command line: `preflight check` and `preflight verify`.

Exit codes: 0 PASS, 1 BLOCK, 3 REVIEW, 4 tool/resolver error, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

from . import __version__
from .certificate import dumps, envelope, verify
from .core import run_check
from .report_html import render
from .resolvers import FixtureResolver, LiveResolver, ResolverError

EXIT = {"PASS": 0, "BLOCK": 1, "REVIEW": 3}
EXIT_ERROR = 4


def _summary(env: dict, out=None) -> None:
    out = out or sys.stdout
    b = env["certificate"]
    res, ext = b["resolver"], b["extractor"]
    if res["name"] == "fixture":
        src = f"fixtures {res['source']}" + (" (SYNTHETIC; use --live for real lookups)" if res.get("synthetic") else "")
    else:
        src = f"CourtListener live ({res['endpoint']})"
    w = out.write
    w(f"Citation Pre-flight {b['tool']['version']}  rules {b['rules']['version']}  "
      f"extractor {ext['name']} {ext.get('version')}\n")
    w(f"Resolver: {src}\n")
    w(f"Input:    {env['input_path']}  sha256 {b['input']['sha256']}\n\n")
    for c in b["citations"]:
        p = c["parsed"]
        said = ""
        if p.get("case_name_in_brief"):
            said = f"  “{p['case_name_in_brief']}”"
        if p.get("year_in_brief"):
            said += f" ({p['year_in_brief']})"
        w(f"{c['id']:<4}{'[' + c['verdict'] + ']':<14}{c['text']}{said}\n")
        rows = [(ch["check"], ch["result"], ch["evidence"]) for ch in c["checks"]]
        rows += [("note", "", n) for n in c.get("extraction_notes", [])]
        for label, result, evidence in rows:
            lines = textwrap.wrap(evidence, width=66) or [""]
            w(f"{'':4}{label:<15}{result:<12}{lines[0]}\n")
            for ln in lines[1:]:
                w(f"{'':31}{ln}\n")
        w("\n")
    ov = b["overall"]
    k = ov["counts"]
    w(f"OVERALL: {ov['verdict']}  ({k['FAIL']} FAIL, {k['WARN']} WARN, {k['NOT CHECKED']} NOT CHECKED, {k['PASS']} PASS)\n")
    w(f"         {ov['reason']}\n")
    w(f"Certificate sha256: {env['certificate_sha256']}\n")


def cmd_check(a) -> int:
    path = Path(a.brief)
    try:
        data = path.read_bytes()
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"error: cannot read {path} as UTF-8 text: {e}", file=sys.stderr)
        return EXIT_ERROR
    if a.live:
        resolver = LiveResolver(os.environ.get("COURTLISTENER_TOKEN"))
    else:
        try:
            resolver = FixtureResolver(a.fixtures)
        except (OSError, ValueError, KeyError) as e:
            print(f"error: cannot load fixtures: {e}", file=sys.stderr)
            return EXIT_ERROR
    try:
        body = run_check(data, resolver, a.extractor)
    except ResolverError as e:
        print(f"error: {e}", file=sys.stderr)
        print("No certificate was produced; the filing has NOT been checked.", file=sys.stderr)
        return EXIT_ERROR
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR

    env = envelope(body, input_path=a.brief)
    if a.out:
        Path(a.out).write_text(dumps(env), encoding="utf-8")
    if a.html:
        Path(a.html).write_text(render(env), encoding="utf-8")
    if not a.quiet:
        _summary(env)
        written = [p for p in (a.out, a.html) if p]
        if written:
            print("Wrote " + ", ".join(written))
    return EXIT[body["overall"]["verdict"]]


def cmd_verify(a) -> int:
    try:
        env = json.loads(Path(a.report).read_text(encoding="utf-8"))
        ok, recorded, recomputed = verify(env)
    except (OSError, ValueError, KeyError) as e:
        print(f"error: cannot read certificate: {e}", file=sys.stderr)
        return EXIT_ERROR
    if ok:
        print(f"OK  certificate_sha256 {recorded} matches the certificate body")
        return 0
    print(f"MISMATCH  recorded {recorded}\n          recomputed {recomputed}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="preflight", description="Deterministic pre-flight check of case citations in a draft filing.")
    ap.add_argument("--version", action="version", version=f"citation-preflight {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="check the citations in a UTF-8 text file")
    c.add_argument("brief")
    c.add_argument("--live", action="store_true", help="look up citations on CourtListener (needs COURTLISTENER_TOKEN)")
    c.add_argument("--fixtures", help="fixture JSON for offline mode (default: bundled synthetic fixtures)")
    c.add_argument("--extractor", choices=["auto", "eyecite", "regex"], default="auto")
    c.add_argument("--out", help="write certificate JSON here")
    c.add_argument("--html", help="write single-file HTML report here")
    c.add_argument("--quiet", action="store_true")
    c.set_defaults(func=cmd_check)

    v = sub.add_parser("verify", help="recompute a certificate's SHA-256")
    v.add_argument("report")
    v.set_defaults(func=cmd_verify)

    a = ap.parse_args(argv)
    if getattr(a, "live", False) and a.fixtures:
        ap.error("--live and --fixtures are mutually exclusive")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
