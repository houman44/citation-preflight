#!/usr/bin/env bash
# Offline demo: check an AI-drafted brief against SYNTHETIC fixtures, write the
# certificate and HTML report, then re-run and show the certificate hash is
# identical. Terminal output is also saved to demo/demo_output.txt.
set -u
cd "$(dirname "$0")/.."

if command -v preflight >/dev/null 2>&1; then
  PF=(preflight)
else
  PF=(python3 -m citation_preflight)
  export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
fi

{
echo "\$ preflight check demo/brief_ai_draft.txt --out demo/report.json --html demo/report.html"
echo
"${PF[@]}" check demo/brief_ai_draft.txt --out demo/report.json --html demo/report.html
code=$?
echo
echo "exit code: $code   (0 PASS, 1 BLOCK, 3 REVIEW, 4 error)"
echo
echo "--- Re-run on the same input and fixtures ---"
"${PF[@]}" check demo/brief_ai_draft.txt --out demo/report_rerun.json --quiet
h1=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["certificate_sha256"])' demo/report.json)
h2=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["certificate_sha256"])' demo/report_rerun.json)
echo "run 1 certificate sha256: $h1"
echo "run 2 certificate sha256: $h2"
if [ "$h1" = "$h2" ]; then echo "IDENTICAL: same input + same data => same certificate"; else echo "DIFFERENT (unexpected)"; fi
echo
echo "\$ preflight verify demo/report.json"
"${PF[@]}" verify demo/report.json
} 2>&1 | tee demo/demo_output.txt
