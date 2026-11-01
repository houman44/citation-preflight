# Citation Pre-flight: a deterministic gate for AI-drafted legal filings

A small command-line check that runs on a draft filing just before a lawyer approves or e-files it. It pulls every case citation out of the text, looks each one up, and checks the citation, case name and year against the record. It gives a verdict of **PASS**, **REVIEW** or **BLOCK**, plus a checksummed certificate and a one-page HTML report.

Models propose; deterministic checks decide. No LLM is involved at any step, so the same input and the same lookup data always give the same answer.

## The problem

- **Volume.** Damien Charlotin's AI Hallucination Cases database counted 1,240 reported instances of AI-hallucinated content in U.S. court filings as of July 27, 2026 ([Maryland State Bar Association, "Confronting the Hard Numbers Behind Lawyers, AI and Phantom Precedents"](https://www.msba.org/site/content/News-and-Publications/News/General-News/Confronting_the_Hard%20Numbers_Behind_Lawyers_AI_and_Phantom_Precedents.aspx)).
- **Verification features have not been enough.** Clio's own blog describes lawyers reporting "AI tools fabricating citations even with verification features turned on". It calls this "an architecture problem, not a safeguard gap" and sets the bar as "can I defend this output in front of a judge?" ([Clio blog, April 20, 2026](https://www.clio.com/enterprise/blog/verified-foundation-legal-ai/)).
- **There is now a natural place for a gate.** At ClioCon 2026 (October 26), Clio announced workflows that firms can configure with approval points. In the example given, a workflow builds a chronology and "routes it for attorney approval". Clio also announced eFiling from Clio Work, where "Lawyers review and submit every filing themselves" ([LawSites, ClioCon 2026 wrap-up](https://www.lawnext.com/2026/10/cliocon-2026-a-quick-wrap-up-of-everything-clio-announced-today-and-its-a-lot.html)).

This is not a fix for Clio's AI or anyone else's. It is an independent check that sits at the approval step, where a person is about to put their name to the filing.

## What it does

```
preflight check brief.txt [--live] [--out report.json] [--html report.html]
preflight verify report.json
```

1. **Extract** citations deterministically with [eyecite](https://github.com/freelawproject/eyecite) (Free Law Project; CourtListener uses the same parser). If eyecite is not installed, it falls back to a documented regex for 38 common reporters. It records volume, reporter, page, pin cite, the case name as written in the brief, and the year.
2. **Resolve** each case citation through a pluggable resolver:
   - `LiveResolver` (`--live`) posts the text to CourtListener's [Citation Lookup API v4](https://wiki.free.law/c/courtlistener/help/api/rest/v4/citation-lookup) with `Authorization: Token $COURTLISTENER_TOKEN` and reads the documented per-citation response (`status` 200/300/400/404/429, `clusters[].case_name`, `date_filed`, …).
   - `FixtureResolver` (the default) answers from a JSON file in the same format. **The bundled fixtures are synthetic**: they were written by hand in CourtListener's documented format and are not real API responses. Run `--live` with a token for real lookups.
3. **Check** each citation. Every check returns PASS, FAIL, WARN or NOT CHECKED, with evidence:

   | Rule | Check | Outcome |
   |---|---|---|
   | R1 | Citation resolves | 404 → **FAIL** "no such citation". 400 (unknown reporter) → WARN. 429 or no result → NOT CHECKED |
   | R2 | Unambiguous | More than one candidate case → WARN |
   | R3 | Case name matches | Mismatch → **FAIL** (rule below) |
   | R4 | Year matches `date_filed` | Same year → PASS. Off by 1 → WARN. Off by more → **FAIL** |
   | R0 | Supported form | Id., supra, short forms, statutes and journals → NOT CHECKED, never silently passed |

   **Name rule.** Both names are lower-cased, stripped of punctuation and accents, and have stop words removed (v, of, the, inc, co, corp, see, and so on). If both names have two parties ("X v. Y"), they match only when each party in the brief shares at least one word with the matching party in the record, or with the opposite party if the names were reversed on appeal. So "United States v. Smith" does **not** match "United States v. Jones". For names without "v." (such as *In re Gault*), they match when overlap ÷ size of the smaller name ≥ 0.5.

   **Year rule.** The year comes only from a parenthetical that immediately follows the citation, after an optional pin cite. eyecite 2.7.8 sometimes takes the *next* citation's year; when its year disagrees with this rule, the report adds a note.
4. **Certificate** (`--out`). The JSON records the tool and rules versions, the extractor version, the resolver (fixture file hash, or live response hash), the SHA-256 of the input bytes, the evidence and verdict for each citation, and the overall verdict:
   - **BLOCK** if any citation FAILs
   - **REVIEW** if any WARN or NOT CHECKED, or if no citations were found
   - **PASS** otherwise

   `certificate_sha256` is the SHA-256 of the canonical JSON of the certificate body (sorted keys, compact, UTF-8). The timestamp and input path sit outside the hashed body, so re-running on the same input with the same data gives a byte-identical body and the same hash. The certificate is checksummed and reproducible. It is **not** cryptographically signed. `preflight verify` recomputes the hash.
5. **HTML report** (`--html`): a single file with inline CSS and no scripts or external assets. It shows a verdict banner, the citation table with evidence, and both hashes.

Exit codes: `0` PASS, `1` BLOCK, `3` REVIEW, `4` tool or resolver error (for example, a missing token), `2` usage error.

## Two-minute demo

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[eyecite,dev]"      # or: pip install -e .   (regex fallback, no dependencies)
bash demo/run_demo.sh                # offline, synthetic fixtures; writes demo/report.json + demo/report.html
pytest -q
```

`demo/brief_ai_draft.txt` is a short opposition-brief paragraph seeded with:

- **two genuine citations:** *Brown v. Board of Education*, 347 U.S. 483 (1954), and *Miranda v. Arizona*, 384 U.S. 436 (1966)
- **the fabricated citation from *Mata v. Avianca*:** *Varghese v. China Southern Airlines Co.*, 925 F.3d 1339 (11th Cir. 2019)
- **a genuine citation with the wrong case attached:** the *Twombly* citation and quotation (550 U.S. 544, 570) labelled "*Ashcroft v. Iqbal* … (2009)", a common mix-up
- **an `Id.` reference and a statute**

Sample output (from `demo/demo_output.txt`, trimmed):

```
C1  [FAIL]        550 U.S. 544  “Ashcroft v. Iqbal” (2009)
    resolves       PASS        Found (status 200, 1 case).
    case name      FAIL        Brief says “Ashcroft v. Iqbal” but 550 U.S. 544 is “Bell Atlantic
                               Corp. v. Twombly” (first party shares nothing, second party shares nothing).
    year           FAIL        Brief says 2009; date_filed 2007-05-21 (off by 2 years).
C2  [NOT CHECKED] Id. at 556
C3  [FAIL]        925 F.3d 1339  “Varghese v. China Southern Airlines Co.” (2019)
    resolves       FAIL        No such citation: ... (status 404: Citation not found: '925 F.3d 1339').
C4  [PASS]        347 U.S. 483  “Brown v. Board of Education” (1954)
C5  [PASS]        384 U.S. 436  “Miranda v. Arizona” (1966)
C6  [NOT CHECKED] 42 U.S.C. § 1983

OVERALL: BLOCK  (2 FAIL, 0 WARN, 2 NOT CHECKED, 2 PASS)
Certificate sha256: 73a9171b80eb63976bc53651119bce76a6e67cc0d1dbb2b5501ff37f82a53549
run 1 certificate sha256: 73a9171b…53549
run 2 certificate sha256: 73a9171b…53549
IDENTICAL: same input + same data => same certificate
```

The hash depends on the extractor and its version: with the regex fallback you get a different hash, which is still identical from run to run.

For real lookups: `export COURTLISTENER_TOKEN=...; preflight check brief.txt --live --html report.html`. Without a token, the tool exits with code 4 and writes no certificate.

## Where it could sit in Clio's workflow

- **At the approval point before eFiling.** It would run when an agentic workflow routes a draft for attorney approval, or when Clio Work prepares an e-filing. A **BLOCK** stops the submit button and lists the failing citations with evidence. A **REVIEW** points the lawyer to exactly the items no machine verified (Id., statutes, ambiguous citations). The certificate hash can be stored on the matter as a record of what was checked, against which data, and when.
- **As a regression gate for AI drafting pipelines.** The same check can run in CI over a corpus of generated drafts. The pass condition becomes "zero FAIL citations", and the certificates can be compared from build to build. Because the check is deterministic, a change in results means the drafts or the reference data changed, not the checker.

## Honest limits

- **Case law only.** Statutes, regulations, journals, Id., supra and short forms are listed as NOT CHECKED.
- **It depends on CourtListener's coverage.** A real case missing from CourtListener will FAIL as "no such citation", so a BLOCK means "a person must look", not proof of fabrication. CourtListener's case names can be longer or in a different style from the brief's, which is why the name rule works on party words rather than exact strings.
- **It does not judge whether a case supports the proposition it is cited for.** A real case cited for something it never said will pass. That needs reading, not lookup.
- **The demo uses synthetic fixtures.** Their case names and dates are the decisions' public names and decision dates. The `absolute_url` values are the paths courtlistener.com redirected to on 2026-10-31. On that date, courtlistener.com's public page for 925 F.3d 1339 returned 404. Real API responses contain many more cluster fields.
- **Live mode is tested only against mocked HTTP.** It has not been run against the real API, because no token was available. The client-side throttle (60 valid citations per minute) is per process. The server's own throttle reply is reported, not retried. Text over 64,000 characters is refused rather than split into chunks. Citations past the 250-per-request cap come back as NOT CHECKED.
- **The extraction rules are heuristics.** The look-back case-name rule and the adjacent-parenthetical year rule are deterministic but can miss unusual formatting. When they find nothing, the check WARNs rather than passing.
- **This is a prototype** with about 1,300 lines of Python and 96 tests. It is not a product.

## Layout

```
src/citation_preflight/   extract.py  names.py  resolvers.py  rules.py  certificate.py  report_html.py  cli.py
src/citation_preflight/fixtures/synthetic_courtlistener_lookups.json   (SYNTHETIC)
demo/                     brief_ai_draft.txt  run_demo.sh  demo_output.txt  report.json  report.html
tests/                    extraction, rules, verdicts, reproducibility, live resolver (mocked HTTP), CLI
```

---

Independent prototype by Houman Azimi (Barg Labs). Not affiliated with or endorsed by Clio. Uses only public information.
