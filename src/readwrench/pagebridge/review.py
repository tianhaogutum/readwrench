"""A self-contained local review document. No browser PDF parser or server."""

import json
import re
from html import escape
from pathlib import Path

STATIC = Path(__file__).parent / "static"


def write_review(plan: dict, path: Path):
    # Escape script delimiters as well as HTML. PDF text and file names are untrusted.
    payload = json.dumps(plan, ensure_ascii=True).replace("<", "\\u003c").replace("&", "\\u0026")
    title = escape(f"{plan['sources']['original']['name']} → {plan['sources']['modified']['name']}")
    script = STATIC.joinpath("review.js").read_text(encoding="utf-8")
    values = {"__TITLE__": title, "__DATA__": payload, "__SCRIPT__": script}
    html = re.sub(r"__TITLE__|__DATA__|__SCRIPT__", lambda match: values[match[0]], TEMPLATE)
    path.write_text(html, encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' file: data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>Review PDF page correspondence</title>
<style>
:root{font:15px/1.5 system-ui,sans-serif;color:#172a3a;background:#eef2f5}*{box-sizing:border-box}
body{max-width:1380px;margin:auto;padding:28px}h1{font-size:30px;line-height:1.15;margin:8px 0 12px}
.eyebrow{color:#385876;text-transform:uppercase;letter-spacing:.12em;font-size:12px;font-weight:750}
.intro{max-width:85ch}code{background:#e1e8ee;padding:2px 5px}button,input,select{font:inherit}
button{border:1px solid #244963;border-radius:6px;background:white;padding:8px 12px;cursor:pointer}
button.primary{background:#174e70;color:white}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #bb7300;outline-offset:2px}
.toolbar{display:flex;gap:12px;flex-wrap:wrap;align-items:center;background:white;padding:16px;border-radius:10px;margin:20px 0;position:sticky;top:0;z-index:2;box-shadow:0 3px 10px #172a3a18}
.toolbar input[type=text]{min-width:190px;padding:8px;border:1px solid #7b8f9f;border-radius:5px}
#status{font-weight:650}.pagination{display:inline-flex;gap:8px;align-items:center;font-size:13px}.pagination[hidden]{display:none}#error{color:#a02323;white-space:pre-wrap}.pair{border:1px solid #cad3db;background:white;border-radius:10px;margin:15px 0;overflow:hidden}
.pair.pending{border-left:5px solid #be7a06}.pair.suggested{border-left:5px solid #347b68}
.row-head{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:12px 18px;background:#f8fafb}
.badge{font-size:12px;padding:3px 8px;border-radius:20px;background:#f9e8bf}.suggested .badge{background:#dceee7}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px 18px}.panel{display:grid;grid-template-columns:144px 1fr;gap:14px;align-items:start;min-width:0}.panel img{width:144px;height:192px;border:1px solid #b9c4cf;object-fit:contain;background:white}
.excerpt{font-size:13px;color:#4d6478;overflow-wrap:anywhere}.details{padding:0 18px 14px;font-size:13px;color:#385876}.empty{height:192px;border:1px dashed #b7c2cc;display:grid;place-items:center;color:#597284}
select{max-width:100%;padding:5px}label{cursor:pointer}#insertions{padding:18px;background:#e1eae6;border-radius:8px}#insertions ul{display:flex;gap:18px;list-style:none;padding:0;flex-wrap:wrap}#insertions img{width:108px;border:1px solid #9aafa2}
footer{font-size:13px;color:#526a7c;margin-top:24px}@media(max-width:760px){body{padding:14px}.panels{grid-template-columns:1fr}.panel{grid-template-columns:108px 1fr}.panel img{width:108px;height:144px}.toolbar{position:static}}
</style>
<div class="eyebrow">readwrench pdf-pages · correspondence review</div>
<h1>Match the pages. Then compare the content.</h1><p class="intro">__TITLE__</p>
<p class="intro">Suggestions use text and small rendered fingerprints. Scores are review aids, not probabilities. Amber rows need attention. A page-number shift can follow an insertion; <strong>reordered</strong> means the pair crosses another pair. Check alternatives, choose each revised page once, and review pages without a counterpart.</p>
<div class="toolbar"><span id="status" role="status" aria-live="polite"></span><span id="pagination" class="pagination"><button id="previous" type="button" aria-label="Previous group of page pairs">Previous</button><span id="pagePosition" role="status" aria-live="polite"></span><button id="next" type="button" aria-label="Next group of page pairs">Next</button></span><label><input id="pendingOnly" type="checkbox"> Show pending only</label><button id="checkAll" type="button">Mark all displayed pairs reviewed</button><label>Reviewed by <input id="reviewer" type="text" maxlength="120" placeholder="Your name"></label><button id="saveDraft" type="button">Save draft</button><button id="resume" type="button">Resume from JSON</button><input id="importMapping" type="file" accept=".json,application/json" hidden><button id="download" class="primary" type="button">Download approved mapping</button></div>
<noscript><p>JavaScript is disabled. Review the editable mapping.json file and use the terminal confirm command after checking the source PDFs.</p></noscript>
<p id="error" role="alert"></p><main id="rows"></main>
<section id="insertions"><h2>Revised pages without a counterpart</h2><p>These pages will be exported as insertions. Pair them above if they correspond to an original page.</p><ul id="insertList"></ul><label><input id="insertionsReviewed" type="checkbox"> I reviewed the inserted pages, deleted pages, and page order.</label></section>
<footer>All work stays local. There are no network requests or external dependencies. Keep this HTML beside its assets folder. After download: <code>readwrench pdf-pages export approved-mapping.json --out comparison</code>. Mapping JSON records page numbers, source paths and hashes; keep it with appropriate document access controls.</footer>
<script id="plan" type="application/json">__DATA__</script>
<script>
__SCRIPT__
</script></html>"""
