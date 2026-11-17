"""Escaped, self-contained review and modeled path previews. Never load EPUB HTML."""

from __future__ import annotations

import json
from html import escape as h

STYLE = """
:root{color-scheme:light dark;font-family:system-ui,sans-serif;line-height:1.5}
body{max-width:70rem;margin:2rem auto;padding:0 1rem}h1{line-height:1.2}
article,section.path{border:1px solid #888;border-radius:.4rem;padding:1rem;margin:1.5rem 0}
code{overflow-wrap:anywhere}blockquote{border-left:.25rem solid #888;margin:1rem 0;padding-left:1rem}
label{display:block;margin:.75rem 0}input[type=text]{width:95%;max-width:55rem}
button,input,select{font:inherit;padding:.35rem}button{cursor:pointer}summary{cursor:pointer}
.status{font-weight:bold}.muted{opacity:.8}a:focus,button:focus,input:focus,select:focus{outline:3px solid #468bd5;outline-offset:3px}
.warning{border-left:.3rem solid #bf6600;padding-left:1rem}li{margin:.4rem 0}
"""


def page(title: str, body: str, script: str = "") -> str:
    csp = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; base-uri 'none'"
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{h(csp, quote=True)}">'
            f'<title>{h(title)}</title><style>{STYLE}</style></head><body>'
            f'<main><h1>{h(title)}</h1>{body}</main>{script}</body></html>')


def loc(value: dict) -> str:
    return f'{value["path"]}:{value.get("line") or "?"}' + (f'#{value["id"]}' if value.get("id") else "")


def lang(value: str | None) -> str:
    return f' lang="{h(value, quote=True)}"' if value else ""


def review_html(plan: dict) -> str:
    notes = {n["key"]: n for n in plan["notes"]}
    note_indexes = {key: index for index, key in enumerate(notes)}
    body = (f'<p>Book: <strong>{h(plan["book_title"])}</strong>. '
            f'{len(plan["references"])} possible note references, in package spine order.</p>'
            '<p>Read the reference context, the actual forward destination, and the return paths. '
            'Confirm each association you understand. Leave uncertain items unchecked. '
            'This file edits only a decision list; it does not change your EPUB.</p>'
            '<p class="warning">This is a navigation review, with escaped excerpts rather than book rendering. '
            'A successful EPUBCheck result is a format check. Test actual reading and screen-reader behavior separately.</p>'
            '<button type="button" id="export">Download decisions.json</button>'
            '<p role="status" id="saved">No references are approved by default.</p>')
    if plan["warnings"]:
        body += f'<details><summary>{len(plan["warnings"])} scan warnings</summary><ul>'
        body += "".join(f'<li><code>{h(json.dumps(w, ensure_ascii=False))}</code></li>' for w in plan["warnings"])
        body += '</ul></details>'
    for index, record in enumerate(plan["references"]):
        note = notes.get(record["note_key"])
        existing = "".join(f'<li>{h(r["kind"])}: <code>{h(r["href"])}</code> {h(r.get("label", ""))}</li>' for r in record["returns"])
        status = h(record["status"])
        choices = ''.join(f'<option value="{action}"{" selected" if action == record["action"] else ""}>{label}</option>' for action, label in (
            ("add_backlink", "Add a precise return if missing"), ("keep", "Keep this path unchanged"), ("skip", "Skip this reference")))
        body += (f'<article id="reference-{index}" tabindex="-1" data-index="{index}"><h2>Reference {record["number"]}: {h(record["label"] or "(unlabeled link)")}</h2>'
                 f'<p class="status">{status}</p><ol><li>Read from <code>{h(loc(record["location"]))}</code>'
                 f'<blockquote{lang(record.get("context_language"))}>{h(record["context"])}</blockquote></li>'
                 f'<li>Follow <code>{h(record["href"])}</code>. '
                 + (f'Note at <code>{h(loc(note["location"]))}</code><blockquote{lang(note.get("language"))}>{h(note["excerpt"])}</blockquote><a href="#catalog-{note_indexes[note["key"]]}">Inspect every existing link in this note</a>' if note else 'No unambiguous note was resolved. Repair the forward link in an editor and rescan.')
                 + ('<p>This link opens a whole XHTML file. Confirm that this note/file is the intended destination.</p>' if record.get("association_basis", "").startswith("whole-document") else '')
                 + f'</li><li>Observed return paths: <ul>{existing or "<li>No exact return found.</li>"}</ul></li></ol>'
                 f'<p class="muted">Detection: {h(", ".join(record["reason"]))}. '
                 f'{"Main reading spine" if record["in_linear_spine"] else "Nonlinear or non-spine resource"}.</p>'
                 f'<label for="action-{index}">Action</label><select id="action-{index}" class="action">{choices}</select>'
                 f'<label for="label-{index}">Descriptive return link text</label>'
                 f'<input id="label-{index}" class="label" type="text" maxlength="256"{lang(plan["scan_options"].get("label_lang") or (note or {}).get("language"))} value="{h(record["backlink_label"], quote=True)}">'
                 '<p class="muted">This label is used only for a newly added backlink. Existing link labels are preserved.</p>'
                 f'<label><input class="approved" type="checkbox"{" checked" if record["approved"] else ""}> '
                 f'I confirmed reference {record["number"]}, its note association, and this action.</label></article>')
    if not plan["references"]:
        body += '<p>No matching references were found. Unlinked markers need an editor. For legacy linked books, try --all-links or a reference XPath.</p>'
    if notes:
        body += '<h2>Existing note link catalog</h2><p>Every outgoing link is listed here once, including links that return elsewhere or do not resolve. These links are preserved.</p>'
        for key, note in notes.items():
            links = []
            for link in note.get("links", []):
                target = link["target"]
                destination = loc(link["destination"]) if link.get("destination") else target.get("error") or "unresolved"
                links.append(f'<li>From <code>{h(loc(link["location"]))}</code>, <span{lang(link.get("language"))}>{h(link["label"] or "(unlabeled link)")}</span>: <code>{h(link["href"])}</code> → <code>{h(destination)}</code></li>')
            incoming = ''.join(f'<li><a href="#reference-{i}">Return to review of reference {r["number"]}</a></li>'
                               for i, r in enumerate(plan["references"]) if r["note_key"] == key)
            body += (f'<section id="catalog-{note_indexes[key]}" tabindex="-1"><h3>{h(loc(note["location"]))}</h3>'
                     f'<ul>{"".join(links) or "<li>No existing outgoing links.</li>"}</ul>'
                     f'<p>References associated with this note:</p><ul>{incoming or "<li>None detected.</li>"}</ul></section>')
    body += '<p><a href="#export">Return to the decisions download button</a></p>'
    serialized = json.dumps(plan, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    script = f'<script type="application/json" id="plan">{serialized}</script>' + """
<script>
'use strict';
const plan = JSON.parse(document.getElementById('plan').textContent);
document.getElementById('export').addEventListener('click', () => {
  for (const row of document.querySelectorAll('article[data-index]')) {
    const record = plan.references[Number(row.dataset.index)];
    record.approved = row.querySelector('.approved').checked;
    record.action = row.querySelector('.action').value;
    record.backlink_label = row.querySelector('.label').value;
  }
  const blob = new Blob([JSON.stringify(plan, null, 2) + '\\n'], {type:'application/json'});
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = 'decisions.json';
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  document.getElementById('saved').textContent = 'Exported ' + plan.references.filter(r => r.approved && r.action !== 'skip').length + ' approved references. Build a separate candidate with readwrench footnotes candidate.';
});
</script>"""
    return page("EPUB footnote return review", body, script)


def preview_html(manifest: dict, plan: dict) -> str:
    notes = {n["key"]: n for n in plan["notes"]}
    validation = manifest.get("format_validation", {"status": "not_run"})
    body = (f'<p>Book: <strong>{h(manifest["book_title"])}</strong>. '
            f'{manifest["approved_references"]} approved of {manifest["total_references"]} possible references.</p>'
            f'<p>EPUBCheck candidate result: <strong>{h(validation["status"])}</strong>. '
            'Accessibility status: not assessed.</p>'
            '<p class="warning">Links below demonstrate each reviewed route using escaped excerpts. '
            'This is not an EPUB reading system. Open candidate.epub in your reader and test with your assistive technology.</p>')
    for index, route in enumerate(manifest["paths"]):
        note = notes.get(route["note_key"])
        has_return = route["result"] in {"added-exact-return", "kept-existing", "kept-ancestor-return"}
        body += (f'<section class="path"><h2>Reference {route["number"]}: {h(route["result"])}</h2>'
                 f'<section id="source-{index}" tabindex="-1"><h3>Reading position</h3>'
                 f'<p><code>{h(loc(route["source"]))}</code></p><blockquote{lang(route.get("context_language"))}>{h(route["context"])}</blockquote>'
                 f'<p><a href="#note-{index}">Follow note for reference {route["number"]}</a> '
                 f'(<code>{h(route["forward_href"])}</code>)</p></section>'
                 f'<section id="note-{index}" tabindex="-1"><h3>Note destination</h3>'
                 + (f'<p><code>{h(loc(note["location"]))}</code></p><blockquote{lang(note.get("language"))}>{h(note["excerpt"])}</blockquote>' if note else '<p>No resolved note.</p>')
                 + (f'<p><a href="#source-{index}"{lang(route.get("return_language"))}>{h(route["label"] or "(unlabeled existing return)")}</a></p>' if has_return else '<p>No exact return was added. This path was deliberately kept unchanged.</p>')
                 + (f'<p>Candidate return: <code>{h(route["return_href"])}</code></p>' if route.get("return_href") else '')
                 + (f'<p>The kept return lands at surrounding element <code>{h(loc(route["return_location"]))}</code>.</p>' if route["result"] == "kept-ancestor-return" else '')
                 + '</section></section>')
    return page("Candidate footnote paths", body)
