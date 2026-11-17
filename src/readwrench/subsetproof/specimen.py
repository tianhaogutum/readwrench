"""Offline specimen from exactly the glyphs HarfBuzz returned, without fallback."""

from __future__ import annotations

from html import escape
import json
from collections.abc import Iterator

from .core import FontAsset


def shaped_svg(asset: FontAsset, shape: dict, location: dict, *, label: str, changed: set[int], prefix: str = "proof") -> str:
    x = y = 0.0
    bounds = [0.0, -1.0, 0.0, 0.3]
    paths = []
    definitions = {}
    for i, glyph in enumerate(shape["glyphs"]):
        drawing = asset.glyph(glyph["gid"], location)
        pos = glyph["position"]
        gx, gy = x + pos["x_offset"], -(y + pos["y_offset"])
        if drawing["bounds"] is not None:
            x0, y0, x1, y1 = (v / asset.upem for v in drawing["bounds"])
            bounds = [min(bounds[0], gx + x0), min(bounds[1], gy - y1),
                      max(bounds[2], gx + x1), max(bounds[3], gy - y0)]
        color = "#b42318" if i in changed or glyph["gid"] == 0 else "#182b3a"
        if glyph["gid"] not in definitions:
            definitions[glyph["gid"]] = (f'{prefix}-g{glyph["gid"]}', drawing["path"])
        outline_id = definitions[glyph["gid"]][0]
        paths.append(f'<use href="#{outline_id}" fill="{color}" '
                     f'transform="translate({gx:.9f} {gy:.9f}) scale({1/asset.upem:.12f} {-1/asset.upem:.12f})"/>' )
        x += pos["x_advance"]
        y += pos["y_advance"]
        bounds[0], bounds[2] = min(bounds[0], x), max(bounds[2], x)
        bounds[1], bounds[3] = min(bounds[1], -y), max(bounds[3], -y)
    margin = 0.12
    x0, y0, x1, y1 = bounds
    width, height = max(x1 - x0 + 2 * margin, .5), max(y1 - y0 + 2 * margin, 1.5)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{escape(label, quote=True)}" '
            f'viewBox="{x0-margin:.9f} {y0-margin:.9f} {width:.9f} {height:.9f}" '
            f'width="{width*32:.2f}" height="{height*32:.2f}">'
            f'<title>{escape(label)}</title><defs>' +
            ''.join(f'<path id="{outline_id}" d="{path}"/>' for outline_id, path in definitions.values()) +
            f'</defs>{"".join(paths)}</svg>')


def render(report: dict, original: FontAsset, candidate: FontAsset | None) -> Iterator[str]:
    accepted = report["accepted"]
    status = "ACCEPTED" if accepted else "REJECTED"
    yield f'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>Subset Proof — {status}</title><style>
:root{{font:16px/1.55 system-ui,sans-serif;color:#182b3a;background:#f3f5f4}}
body{{max-width:1100px;margin:auto;padding:32px}}h1{{font-size:2.2rem;margin:.2em 0}}
.badge{{font-weight:750;color:{'#067647' if accepted else '#b42318'}}}
section,.summary{{background:white;border:1px solid #d4deda;border-radius:8px;padding:20px;margin:20px 0}}
.sample{{overflow:auto;border-left:3px solid #c5d4cb;padding:10px;background:#fafcfb}}
.sample svg{{display:block;max-width:none}}.text{{white-space:pre-wrap;overflow-wrap:anywhere}}
code,pre{{font-family:ui-monospace,monospace;font-size:.85rem}}pre{{overflow:auto;max-height:500px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:6px;text-align:left;border-bottom:1px solid #d4deda}}
.muted{{color:#53675f}}.issue{{color:#b42318}}summary{{cursor:pointer;font-weight:600}}
@media print{{body{{padding:0;background:white}}section{{break-inside:avoid}}.sample{{overflow:visible}}}}
</style><body><p class="badge">{status}</p><h1>Subset Proof</h1>
<p>Strict manuscript proof using fontTools and HarfBuzz. Red glyphs mark a difference or .notdef.</p>
<div class="summary"><p><b>Original:</b> {escape(original.path.name)} · {original.metadata['bytes']:,} bytes</p>'''
    if candidate is not None:
        yield f'<p><b>Candidate:</b> {escape(candidate.path.name)} · {candidate.metadata["bytes"]:,} bytes</p>'
    missing_count = sum(len(covered["missing"]) for covered in report["coverage"].values())
    failed_pairs = [(number, result) for number, result in enumerate(report["results"]) if not result["comparison"]["equal"]]
    yield (f'<p><b>Scope:</b> {report["text"]["run_count"]} runs · {len(report["results"])} shaping comparisons · '
           f'{missing_count} missing occurrences · {len(failed_pairs)} differing comparisons</p>')
    if failed_pairs:
        yield '<p>Jump to a difference: '
        for number, result in failed_pairs[:20]:
            yield f'<a href="#result-{number}">{escape(result["run"]["id"])}</a> '
        if len(failed_pairs) > 20:
            yield '(first 20 shown; complete results below)'
        yield '</p>'
    yield ('<p><a href="report.json">Complete glyph sequences and structured report</a></p>'
                  '<p class="muted">Outlined glyphs are drawn at 32 px/em from the actual shaping results. '
                  'No web fonts, fallback, JavaScript, or network requests. This is a run-level proof; '
                  'it does not test paragraph layout, rasterization, or hinting.</p></div>')
    for side, covered in report["coverage"].items():
        if covered["missing"]:
            yield (f'<section><h2 class="issue">{escape(side.title())}: missing Unicode</h2><table>'
                          '<tr><th>Location</th><th>Character</th><th>Reason</th></tr>')
            for item in covered["missing"][:200]:
                yield (f'<tr><td>Run {escape(item["run"])} · line {item["line"]}, column {item["column"]}</td>'
                              f'<td><code>{item["codepoint"]}</code> {escape(item["name"])}</td>'
                              f'<td>{escape(item["reason"])}<p class="text" dir="auto">{escape(item["context"])}</p></td></tr>')
            yield '</table></section>'
            if len(covered["missing"]) > 200:
                yield f'<p>{len(covered["missing"]) - 200} further occurrences are recorded in report.json.</p>'
    for number, result in enumerate(report["results"]):
        run = result["run"]
        changes = result["comparison"]["changes"]
        original_changes = {change["original_index"] for change in changes if change["original_index"] is not None}
        candidate_changes = {change["candidate_index"] for change in changes if change["candidate_index"] is not None}
        label = "Equal" if result["comparison"]["equal"] else "Different"
        yield (f'<section id="result-{number}"><h2>Run {escape(run["id"])} · {label}</h2>'
                      f'<p class="text" dir="auto">{escape(run["text"])}</p>'
                      f'<p class="muted">{escape(result["original"]["direction"])} · '
                      f'{escape(result["original"]["script"])} · {escape(run["language"])} · '
                      f'axes {escape(json.dumps(result["location"], sort_keys=True))} · '
                      f'features {escape(json.dumps(run["features"], sort_keys=True))}</p>')
        yield '<h3>Original</h3><div class="sample">'
        yield shaped_svg(original, result["original"], result["location"], label="Original", changed=original_changes, prefix=f'r{number}-o')
        yield '</div>'
        if candidate is not None:
            yield '<h3>Candidate</h3><div class="sample">'
            yield shaped_svg(candidate, result["candidate"], result["location"], label="Candidate", changed=candidate_changes, prefix=f'r{number}-c')
            yield '</div>'
        yield (f'<p>{len(result["original"]["glyphs"])} original glyphs · '
               f'{len(result["candidate"]["glyphs"])} candidate glyphs · '
               f'{len(changes)} changed events · '
               f'advance {result["original"]["advance_em"]} → {result["candidate"]["advance_em"]} em</p>')
        if changes or result["comparison"]["notdef"] or result["comparison"]["metric_changes"]:
            # The JSON remains complete; avoid duplicating a potentially huge
            # glyph sequence again inside the HTML document.
            yield ('<details open><summary>First 50 changed events (complete data in report.json)</summary><pre>' +
                   escape(json.dumps({"changes": changes[:50], "metric_changes": result["comparison"]["metric_changes"],
                                      "notdef": result["comparison"]["notdef"][:50]},
                                     ensure_ascii=False, indent=2)) + '</pre></details>')
        yield '</section>'
    provenance = {key: value for key, value in report.items() if key not in {"results", "coverage"}}
    yield ('<details><summary>Provenance and proof scope</summary><pre>' +
           escape(json.dumps(provenance, ensure_ascii=False, indent=2)) + '</pre></details></body></html>')
