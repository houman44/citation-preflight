"""Single-file HTML report (inline CSS, no external assets, no scripts)."""

from __future__ import annotations

from html import escape

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;color:#1c2330;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 24px 40px}
h1{font-size:18px;margin:0 0 2px;font-weight:650;letter-spacing:-.01em}
.sub{color:#5b6475;font-size:13px;margin:0 0 18px}
.banner{border-radius:10px;padding:18px 22px;display:flex;align-items:center;margin-bottom:14px;color:#fff}
.banner .v{margin-right:20px;font-size:30px;font-weight:800;letter-spacing:.04em}
.banner .r{font-size:15px;opacity:.95}
.banner .c{margin-left:auto;padding-left:20px;font-size:13px;text-align:right;opacity:.95;white-space:nowrap}
.BLOCK{background:#b42318}.REVIEW{background:#b54708}.PASS{background:#067647}
.notice{background:#fff7e6;border:1px solid #f5c26b;color:#6b4300;border-radius:8px;padding:9px 14px;margin-bottom:14px;font-size:13px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid #dfe3ea;border-radius:10px;overflow:hidden}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#5b6475;background:#f8f9fb;padding:9px 12px;border-bottom:1px solid #dfe3ea}
td{vertical-align:top;padding:11px 12px;border-bottom:1px solid #edf0f4}
tr:last-child>td{border-bottom:0}
.cite{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;white-space:nowrap}
.muted{color:#7a8394}
.pill{display:inline-block;border-radius:999px;padding:2px 9px;font-size:11px;font-weight:700;letter-spacing:.03em;white-space:nowrap}
.p-PASS{background:#dcfae6;color:#085d3a}.p-FAIL{background:#fee4e2;color:#912018}
.p-WARN{background:#fef0c7;color:#93370d}.p-NC{background:#eaecf0;color:#344054}
table.checks{border:0;border-radius:0;background:transparent;width:100%}
table.checks td{padding:0 0 6px;border:0;vertical-align:baseline}
table.checks td.k{width:100px;color:#5b6475;font-size:12px;padding-right:8px;white-space:nowrap}
table.checks td.r{width:104px;padding-right:10px}
table.checks .pill{display:block;text-align:center}
table.checks td.e{font-size:12.5px}
tr.row-FAIL>td:first-child{box-shadow:inset 4px 0 0 #d92d20}
tr.row-WARN>td:first-child{box-shadow:inset 4px 0 0 #f79009}
tr.row-NC>td:first-child{box-shadow:inset 4px 0 0 #98a2b3}
tr.row-PASS>td:first-child{box-shadow:inset 4px 0 0 #17b26a}
table.meta{margin-top:16px;font-size:12.5px}
table.meta td{padding:6px 14px;border-bottom:1px solid #edf0f4}
table.meta td:first-child{width:190px;color:#5b6475;white-space:nowrap}
.h{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
.foot{margin-top:14px;color:#5b6475;font-size:12px}
"""


def _pill(result: str) -> str:
    cls = "NC" if result == "NOT CHECKED" else result
    return f'<span class="pill p-{cls}">{escape(result)}</span>'


def render(env: dict) -> str:
    b = env["certificate"]
    ov = b["overall"]
    counts = ov["counts"]
    res = b["resolver"]
    ext = b["extractor"]

    rows = []
    for c in b["citations"]:
        p = c["parsed"]
        brief = []
        if p.get("case_name_in_brief"):
            brief.append(escape(p["case_name_in_brief"]))
        if p.get("year_in_brief"):
            brief.append(f'<span class="muted">({p["year_in_brief"]})</span>')
        brief_html = " ".join(brief) or '<span class="muted">—</span>'

        db_html = '<span class="muted">—</span>'
        lk = c.get("lookup")
        if lk and lk.get("candidates"):
            db_html = "<br>".join(
                f'{escape(x["case_name"] or "?")} <span class="muted">({escape(x["date_filed"] or "?")})</span>'
                for x in lk["candidates"])
        elif lk:
            db_html = f'<span class="muted">status {escape(str(lk.get("status")))}: no case</span>'

        checks = "".join(
            f'<tr><td class="k">{escape(ch["check"])}</td><td class="r">{_pill(ch["result"])}</td>'
            f'<td class="e">{escape(ch["evidence"])}</td></tr>'
            for ch in c["checks"])
        notes = "".join(f'<tr><td class="k">note</td><td class="r"></td><td class="e muted">{escape(n)}</td></tr>'
                        for n in c.get("extraction_notes", []))
        v = c["verdict"]
        rowcls = "NC" if v == "NOT CHECKED" else v
        rows.append(
            f'<tr class="row-{rowcls}"><td class="muted">{escape(c["id"])}</td>'
            f'<td class="cite">{escape(c["text"])}</td>'
            f'<td>{brief_html}</td><td>{db_html}</td>'
            f'<td><table class="checks">{checks}{notes}</table></td>'
            f'<td>{_pill(v)}</td></tr>')

    if res.get("synthetic"):
        notice = ('<div class="notice"><b>Offline demo:</b> lookups come from SYNTHETIC fixtures written in '
                  "CourtListener's documented response format, not from a live database. "
                  "Run with <code>--live</code> and a CourtListener token for real lookups.</div>")
        res_desc = f'fixture file {escape(res.get("source", ""))} (synthetic), sha256 {escape(res.get("source_sha256", ""))}'
    elif res.get("name") == "fixture":
        notice = ""
        res_desc = f'fixture file {escape(res.get("source", ""))}, sha256 {escape(res.get("source_sha256", ""))}'
    else:
        notice = ""
        res_desc = f'{escape(res.get("name", ""))} {escape(res.get("endpoint", ""))}; response sha256 {escape(str(res.get("response_sha256")))}'

    count_txt = " · ".join(f"{counts[k]} {k}" for k in ("FAIL", "WARN", "NOT CHECKED", "PASS"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Citation Pre-flight Report</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>Citation Pre-flight</h1>
<p class="sub">Deterministic pre-filing check of case citations · {escape(env.get("input_path", ""))}</p>
<div class="banner {escape(ov["verdict"])}"><div class="v">{escape(ov["verdict"])}</div>
<div class="r">{escape(ov["reason"])}</div><div class="c">{escape(count_txt)}</div></div>
{notice}
<table><thead><tr><th>#</th><th>Citation</th><th>Brief says</th><th>Database says</th><th>Checks and evidence</th><th>Verdict</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<table class="meta">
<tr><td>Input SHA-256</td><td class="h">{escape(b["input"]["sha256"])}</td></tr>
<tr><td>Certificate SHA-256</td><td class="h">{escape(env["certificate_sha256"])}</td></tr>
<tr><td>Tool / rules</td><td>{escape(b["tool"]["name"])} {escape(b["tool"]["version"])} \u00b7 rules {escape(b["rules"]["version"])}</td></tr>
<tr><td>Extractor</td><td>{escape(str(ext.get("name")))} {escape(str(ext.get("version")))}</td></tr>
<tr><td>Resolver</td><td>{res_desc}</td></tr>
<tr><td>Generated (not hashed)</td><td>{escape(env.get("generated_at", ""))}</td></tr>
</table>
<p class="foot">Checks whether each case citation exists and whether the name and year in the brief match the record.
It does not judge whether a case supports the proposition it is cited for. Checksummed and reproducible; not cryptographically signed.
Independent prototype by Houman Azimi (Barg Labs). Not affiliated with or endorsed by Clio.</p>
</div></body></html>
"""
