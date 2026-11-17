"""The self-contained HTML timeline report (no network resources, all captured text escaped)."""
from __future__ import annotations

import json
import math
import os
from urllib.parse import quote

from .jsutil import js_slice

_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
# Characters the WHATWG path percent-encode set (and Node's pathToFileURL) leave alone.
_PATH_SAFE = "/!$&'()*+,;=:@[]|~"


def escape_html(value) -> str:
    text = "" if value is None else str(value)
    return "".join(_ESCAPES.get(char, char) for char in text)


h = escape_html


def file_url(path: str) -> str:
    """``pathToFileURL(path).href`` for an absolute path; a relative path stays a relative URL."""
    encoded = quote(path, safe=_PATH_SAFE, errors="surrogatepass")
    return "file://" + encoded if os.path.isabs(path) else encoded


def _original_link(capture) -> str:
    return (f'<a href="{h(file_url(capture["file"]))}" target="_blank" rel="noopener noreferrer">'
            "Open original capture ↗</a>")


def _time_label(capture) -> str:
    return capture["captureTime"]["at"] or "Capture time unknown"


def _warnings_html(warnings) -> str:
    if not warnings:
        return ""
    return '<ul class="warnings">' + "".join(f"<li>{h(w)}</li>" for w in warnings) + "</ul>"


def _inline_html(parts) -> str:
    return "".join(f"<ins>{h(p['text'])}</ins>" if p["added"] else f"<del>{h(p['text'])}</del>" if p["removed"]
                   else h(p["text"]) for p in parts)


def _search_data(before, after) -> str:
    return h(json.dumps([before or "", after or ""], ensure_ascii=False, separators=(",", ":")))


def _change_html(change) -> str:
    body = _inline_html(change["inline"]) if change["inline"] else h(
        change["after"] if change["after"] is not None else change["before"])
    if change["type"] == "moved":
        location = f"block {change['oldIndex'] + 1} → {change['newIndex'] + 1}"
    elif change["newIndex"] is None:
        location = f"previous block {change['oldIndex'] + 1}"
    else:
        location = f"block {change['newIndex'] + 1}"
    similarity = (f" · heuristic similarity {_js_round(change['confidence'] * 100)}%"
                  if change["type"] == "modified" else "")
    return (f'<article class="change {h(change["type"])}" data-search="{_search_data(change["before"], change["after"])}">'
            f'<header><span class="badge">{h(change["type"])}</span> <a href="#lineage-{h(change["lineageId"])}">'
            f'{h(change["lineageId"])}</a> · {h(location)}{similarity}</header>'
            f'<div class="block-text" dir="auto">{body}</div></article>')


def _js_round(value: float) -> int:
    """``Math.round`` (halves round up)."""
    return math.floor(value + 0.5)


STYLE = """:root{font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1f2937;background:#f8fafc;line-height:1.65;color-scheme:light}*{box-sizing:border-box}body{margin:0}main{max-width:1100px;padding:3rem 1.4rem 5rem;margin:auto}h1,summary,#capture-results{overflow-wrap:anywhere}a{color:#1d4ed8;text-underline-offset:.18em}a:focus-visible,summary:focus-visible,input:focus-visible,button:focus-visible{outline:3px solid #f59e0b;outline-offset:3px}h1{font-size:clamp(2rem,5vw,3rem);line-height:1.2;letter-spacing:-.04em;margin:.4rem 0 1rem}h2{margin-top:3rem}h3{font-size:1rem}h3 span{padding:0 .4rem}nav{display:flex;gap:1.5rem;flex-wrap:wrap;margin:1.5rem 0}.eyebrow{font-weight:700;color:#475569;letter-spacing:.08em;text-transform:uppercase;font-size:.75rem}.note,.quiet,small{color:#475569}.note{font-size:.9rem}.hero-url{overflow-wrap:anywhere}.stats{display:flex;gap:2rem;flex-wrap:wrap}.stats strong{font-size:1.8rem;display:block}.callout{background:#e0e7ff;border-left:4px solid #4338ca;padding:.75rem 1rem;margin:1.5rem 0}.warnings{background:#fff7ed;border-left:4px solid #c2410c;padding:.8rem 1rem .8rem 2.3rem;font-size:.9rem}.toolbar{display:flex;gap:.7rem;align-items:center;flex-wrap:wrap;background:#fff;border:1px solid #cbd5e1;padding:.8rem;border-radius:8px}input,button{font:inherit;padding:.45rem .65rem;border:1px solid #94a3b8;border-radius:4px;background:white}input{min-width:12rem;flex:1}button,summary{cursor:pointer}details{background:#fff;border:1px solid #cbd5e1;border-radius:8px;margin:1rem 0}summary{padding:1rem}.number{display:inline-block;background:#e2e8f0;border-radius:50%;width:1.8rem;text-align:center;margin-right:.5rem}.detail{padding:0 1rem 1rem}summary small{margin-left:.6rem}dl{display:grid;grid-template-columns:10rem 1fr;gap:.2rem 1rem;font-size:.85rem}dt{font-weight:600}dd{margin:0}.break{overflow-wrap:anywhere}.transition{margin:2rem 0}.counts{display:flex;gap:1rem;flex-wrap:wrap;font-size:.85rem}.change{background:white;border:1px solid #cbd5e1;border-left:4px solid #64748b;margin:.7rem 0;border-radius:5px;padding:1rem}.change header{font-size:.75rem;color:#475569;margin-bottom:.4rem}.added{border-left-color:#15803d}.removed{border-left-color:#b91c1c}.modified{border-left-color:#b45309}.moved{border-left-color:#4338ca}.badge{text-transform:uppercase;font-size:.65rem;letter-spacing:.04em;background:#e2e8f0;padding:.15rem .35rem;border-radius:3px}ins{background:#dcfce7;text-decoration:underline;text-decoration-color:#15803d}del{background:#fee2e2;text-decoration:line-through;text-decoration-color:#b91c1c}.block-text{white-space:pre-wrap;overflow-wrap:anywhere}.captured-block{border-top:1px solid #e2e8f0;padding:1rem 0}.block-number{font-size:.7rem}.detail li{margin:1rem 0}footer{border-top:1px solid #cbd5e1;margin-top:3rem;padding-top:1rem;font-size:.8rem;color:#475569}[hidden]{display:none!important}@media(max-width:600px){main{padding-top:1.5rem}dl{grid-template-columns:1fr}.stats{gap:1rem}}@media print{.toolbar,nav{display:none}details{break-inside:avoid}main{max-width:none;padding:0}.change{break-inside:avoid}}"""

SCRIPT = """const query=document.getElementById('query');const items=[...document.querySelectorAll('.change')];const paragraphs=[...document.querySelectorAll('.captured-block')];const folded=text=>text.normalize('NFC').toLocaleLowerCase();query.addEventListener('input',()=>{const term=folded(query.value.trim());let shown=0;for(const item of items){item.hidden=!JSON.parse(item.dataset.search).some(text=>folded(text).includes(term));if(!item.hidden)shown++}const results=document.getElementById('capture-results');results.replaceChildren();let found=0;if(term){const list=document.createElement('ol');for(const paragraph of paragraphs){const text=paragraph.querySelector('.block-text').textContent;const at=folded(text).indexOf(term);if(at<0)continue;found++;if(found>100)continue;const entry=document.createElement('li');const link=document.createElement('a');link.href='#'+paragraph.id;const capture=paragraph.closest('details');link.textContent=capture.querySelector('summary').textContent.trim()+' · '+text.slice(Math.max(0,at-35),at+term.length+80);entry.append(link);list.append(entry)}const message=document.createElement('p');message.textContent=found+' matching captured paragraphs'+(found>100?' (showing the first 100; refine your quote)':'');results.append(message,list)}document.getElementById('search-count').textContent=shown+' of '+items.length+' changes'+(term?' · '+found+' captured paragraphs':'')});document.getElementById('expand').addEventListener('click',()=>{const nodes=[...document.querySelectorAll('details')];const open=nodes.some(node=>!node.open);nodes.forEach(node=>node.open=open);document.getElementById('expand').textContent=open?'Collapse all histories':'Expand all histories'});function reveal(){let target;try{target=document.getElementById(decodeURIComponent(location.hash.slice(1)))}catch{return}if(!target)return;for(let node=target;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;target.scrollIntoView()}window.addEventListener('hashchange',reveal);reveal();"""

CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; "
       "form-action 'none'; object-src 'none'; connect-src 'none'; img-src 'none'")


def _capture_html(index: int, capture) -> str:
    extraction = capture["extraction"]
    selector = f": {h(extraction['selector'])}" if extraction.get("selector") else ""
    record = ""
    if capture.get("containerRecord"):
        record = (f'<dt>WARC record</dt><dd class="break">{h(capture["containerRecord"]["id"])}<br>Decoded HTML SHA-256: '
                  f'{h(capture["containerRecord"]["payloadSHA256"])}</dd>')
    claims = ""
    if capture["claimedModified"]:
        claims = ('<p class="note">Page-provided modification claims (not verified): '
                  + "; ".join(f"{h(c['value'])} ({h(c['source'])})" for c in capture["claimedModified"]) + "</p>")
    blocks = "".join(
        f'<div id="block-{h(capture["id"])}-{position}" class="captured-block"><a class="block-number" '
        f'href="#lineage-{h(block["lineageId"])}">{position + 1} · {h(block["lineageId"])}</a>'
        f'<div class="block-text" dir="auto">{h(block["text"])}</div></div>'
        for position, block in enumerate(capture["blocks"]))
    return (f'<details id="capture-{h(capture["id"])}"><summary><span class="number">{index + 1}</span> '
            f'{h(_time_label(capture))} · {h(capture["label"])} <small>{len(capture["blocks"])} blocks</small></summary>'
            f'<div class="detail"><p>{_original_link(capture)}</p><dl><dt>Extracted title</dt><dd>{h(capture["title"])}'
            f'</dd><dt>Capture time source</dt><dd>{h(capture["captureTime"]["source"])}</dd><dt>Raw capture time</dt>'
            f'<dd>{h(capture["captureTime"]["raw"] or "unknown")}</dd><dt>Extraction</dt><dd>{h(extraction["method"])}'
            f'{selector}</dd><dt>Encoding</dt><dd>{h(extraction["encoding"])} · {h(extraction["encodingSource"])}</dd>'
            f'{record}<dt>Original</dt><dd class="break">{h(capture["file"])}</dd><dt>SHA-256</dt><dd class="break">'
            f'<code>{h(capture["sha256"])}</code></dd></dl>{claims}{_warnings_html(capture["warnings"])}{blocks}'
            "</div></details>")


def _transition_html(transition, by_id) -> str:
    old, current = by_id[transition["from"]], by_id[transition["to"]]
    title = ""
    if transition["titleChange"]:
        change = transition["titleChange"]
        title = (f'<article class="change modified" data-search="{_search_data(change["before"], change["after"])}">'
                 '<header><span class="badge">Extracted title changed</span></header>'
                 f'<div class="block-text" dir="auto">{_inline_html(change["inline"])}</div></article>')
    counts = "".join(f"<span>{count} {h(key)}</span>" for key, count in transition["counts"].items())
    changes = ("".join(_change_html(change) for change in transition["changes"]) if transition["changes"]
               else '<p class="quiet">No extracted paragraph text changes observed.</p>')
    return (f'<section class="transition" id="transition-{h(current["id"])}"><h3><a href="#capture-{h(old["id"])}">'
            f'{h(_time_label(old))}</a> <span aria-label="to">→</span> <a href="#capture-{h(current["id"])}">'
            f'{h(_time_label(current))}</a></h3><p class="note">{h(old["label"])} → {h(current["label"])}. Difference '
            'observed between captures; actual edit time unknown.</p>'
            f'<p class="counts">{counts}</p>{_warnings_html(transition["warnings"])}{title}{changes}</section>')


def _lineage_html(lineage, by_id) -> str:
    last = lineage["observations"][-1]
    summary_text = by_id[last["captureId"]]["blocks"][last["index"]]["text"]
    items = []
    for observation in lineage["observations"]:
        capture = by_id[observation["captureId"]]
        block = capture["blocks"][observation["index"]]
        items.append(f'<li><a href="#block-{h(capture["id"])}-{observation["index"]}">{h(_time_label(capture))} · '
                     f'{h(capture["label"])}</a> <span class="badge">{h(observation["state"])}</span>'
                     f'<div class="block-text" dir="auto">{h(block["text"])}</div></li>')
    for removal in lineage["removals"]:
        items.append(f'<li>Absent in <a href="#capture-{h(removal["captureId"])}">'
                     f'{h(_time_label(by_id[removal["captureId"]]))}</a>.</li>')
    return (f'<details id="lineage-{h(lineage["id"])}"><summary>{h(lineage["id"])} · '
            f'{len(lineage["observations"])} observations · {h(js_slice(summary_text, 100))}</summary>'
            f'<ol class="detail">{"".join(items)}</ol></details>')


def render_html(timeline) -> str:
    by_id = {capture["id"]: capture for capture in timeline["captures"]}
    total_changes = sum(len(t["changes"]) + (1 if t["titleChange"] else 0) for t in timeline["transitions"])
    captures = "".join(_capture_html(index, capture) for index, capture in enumerate(timeline["captures"]))
    transitions = "".join(_transition_html(transition, by_id) for transition in timeline["transitions"])
    lineages = "".join(_lineage_html(lineage, by_id) for lineage in timeline["lineages"])
    ordering = "recorded capture time" if timeline["ordering"] == "capture-time" else "user-supplied manifest order"
    baseline = ('<p class="quiet">One capture establishes a baseline. Add a second capture to observe changes.</p>')
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}"><title>{h(timeline["title"])} — Page Lineage</title>'
        f"<style>\n{STYLE}\n</style></head><body><main>"
        '<div class="eyebrow">Page Lineage · offline archive companion</div>'
        f'<h1>{h(timeline["title"])}</h1><p class="hero-url"><a href="{h(timeline["sourceURL"])}" target="_blank" '
        f'rel="noopener noreferrer">{h(timeline["sourceURL"])}</a></p><div class="stats"><div><strong>'
        f'{len(timeline["captures"])}</strong>captures</div><div><strong>{len(timeline["lineages"])}</strong>paragraph '
        f'histories</div><div><strong>{total_changes}</strong>observed changes</div></div><p class="callout">These are '
        "capture observations. They do not establish when the webpage was edited. Ordering: "
        f'{ordering}.</p>{_warnings_html(timeline["warnings"])}<nav aria-label="Report sections"><a href="#changes">'
        'Changes</a><a href="#captures">Full captures</a><a href="#lineages">Paragraph histories</a></nav>'
        '<h2 id="changes">Observed changes</h2><div class="toolbar"><label for="query">Find text across captures</label>'
        '<input id="query" type="search" placeholder="Find a quote you remember reading"><span id="search-count" '
        'role="status"></span><button type="button" id="expand">Expand all histories</button></div>'
        '<div id="capture-results" aria-live="polite"></div><noscript><p>All content is available without JavaScript. '
        "Search and expand controls require JavaScript.</p></noscript>"
        f"{transitions or baseline}"
        '<h2 id="captures">Full extracted captures</h2><p class="note">Review extraction quality here. Original files '
        "remain available through each capture entry. Opening an original may execute that file’s own scripts in your "
        f"browser.</p>{captures}"
        '<h2 id="lineages">Paragraph histories</h2><p class="note">Edited-text links are conservative heuristics. '
        "Repeated or ambiguous paragraphs may appear as separate additions and removals. IDs are local to this "
        f"report.</p>{lineages}"
        "<footer>Built with readwrench lineage (Python ports of Mozilla Readability and jsdiff). Report text is "
        "escaped, capture HTML is never embedded, and the report loads no external resources.</footer></main>"
        f"<script>\n{SCRIPT}\n</script></body></html>"
    )
