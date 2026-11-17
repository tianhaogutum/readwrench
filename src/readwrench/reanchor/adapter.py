"""Pinned pdfanno adapter: upstream anchors/scoring, reviewable full-span geometry.

The private upstream score helpers are deliberately isolated here. pdfanno is pinned
to 0.2.2 and the real upstream PDF corpus exercises this boundary. No upstream code
is monkeypatched and no pdfanno sidecar/database is touched.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict, deque

import pymupdf
from pdfanno.diff.anchors import TEXT_COVERAGE_KINDS, extract_anchors
from pdfanno.diff.context import context_similarity
from pdfanno.diff.match import _Candidate, _layout_score, _length_similarity, _proximity
from pdfanno.diff.sections import build_section_index, section_for
from pdfanno.diff.types import Anchor
from rapidfuzz.fuzz import partial_ratio_alignment, ratio

from .models import Annotation, Candidate, Evidence
from .text import TextIndex, bounds, normalize


def _id(prefix: str, payload: str) -> str:
    return prefix + hashlib.sha256(payload.encode()).hexdigest()[:20]


def source_annotations(doc: pymupdf.Document, digest: str) -> list[tuple[Annotation, Anchor]]:
    # Use the real upstream anchor extractor. Recover per-annotation if a malformed
    # object prevents the whole-document upstream operation from completing.
    extraction_error = ""
    try:
        upstream = iter(extract_anchors(doc, "sha256:" + digest))
    except Exception as exc:
        upstream = iter([])
        extraction_error = f"Upstream anchor extraction failed: {type(exc).__name__}"
    result = []
    for pi in range(doc.page_count):
        page = doc[pi]
        index = TextIndex(page)
        for annot in page.annots() or []:
            info = {k: str(v or "") for k, v in annot.info.items()}
            kind = annot.type[1].lower() or "unknown"
            aid = _id("ann-", f"{digest}:{pi}:{annot.xref}")
            anchor = next(upstream, None)
            if anchor is None:
                anchor = Anchor(
                    annotation_id=aid,
                    doc_id="sha256:" + digest,
                    kind=kind,
                    page_index=pi,
                    selected_text="",
                    page_width=bounds(page).width,
                    page_height=bounds(page).height,
                )
                vertices = annot.vertices
                if vertices and len(vertices) % 4 == 0:
                    anchor.quads = [
                        [float(v) for pt in vertices[i : i + 4] for v in pt]
                        for i in range(0, len(vertices), 4)
                    ]
            anchor.annotation_id = aid
            anchor.page_width = bounds(page).width
            anchor.page_height = bounds(page).height
            warnings = [extraction_error] if extraction_error else []
            supported = kind in TEXT_COVERAGE_KINDS or kind == "text"
            basis = "unavailable"
            span = None
            if kind in TEXT_COVERAGE_KINDS and anchor.quads:
                try:
                    span = index.selected_range(anchor.quads)
                except (ValueError, RuntimeError):
                    pass
                if span is None:
                    supported = False
                    warnings.append(
                        "No contiguous text span under the source quads; preserve unmatched."
                    )
                else:
                    basis = "selected text"
            elif kind == "text":
                # A sticky note has no selected quote. Find the closest actual
                # source text line, and label it as context rather than selection.
                center = (annot.rect.tl + annot.rect.br) * 0.5
                span = index.nearest_line(center)
                if span:
                    basis = "nearby text"
                    anchor.quads = index.quads(*span)
                    warnings.append(
                        "Sticky note anchor uses nearby text, not selected text; inspect the source image."
                    )
                else:
                    supported = False
                    warnings.append("No nearby extractable text for the sticky note.")
            if span is not None:
                start, end = span
                anchor.selected_text = index.text[start:end]
                anchor.context_before, anchor.context_after = index.context(start, end)
            if not supported:
                warnings.append(
                    f"{kind} is retained in the unmatched package, without coordinate copying."
                )
            reply_type, _ = doc.xref_get_key(annot.xref, "IRT")
            if reply_type != "null":
                supported = False
                warnings.append(
                    "Reply/group relationship retained in the original PDF; migrate discussion manually."
                )
            rich_type, rich_value = doc.xref_get_key(annot.xref, "RC")
            if rich_type != "null" and rich_value:
                if rich_type == "string":
                    info["rich_content"] = rich_value
                else:
                    info["rich_content_reference"] = rich_value
                if not info.get("content", "").strip():
                    supported = False
                    warnings.append(
                        "Rich-text-only comment has no plain Contents; retained with the original PDF instead of migrating an empty comment."
                    )
                elif supported:
                    warnings.append(
                        "Rich-text formatting is reduced to the plain comment Contents during migration; the source PDF stays unchanged."
                    )
            colors = {k: [float(v) for v in (value or [])] for k, value in annot.colors.items()}
            item = Annotation(
                id=aid,
                source_xref=annot.xref,
                page_index=pi,
                kind=kind,
                selected_text=anchor.selected_text,
                context_before=anchor.context_before,
                context_after=anchor.context_after,
                anchor_basis=basis,
                source_rect=list(annot.rect),
                info=info,
                colors=colors,
                opacity=annot.opacity,
                flags=annot.flags,
                supported=supported,
                warnings=warnings,
            )
            result.append((item, anchor))
    return result


class Matcher:
    def __init__(self, doc: pymupdf.Document, *, minimum_text: float = 0.78):
        self.doc = doc
        self.minimum_text = minimum_text
        self._index_cache: OrderedDict[int, TextIndex] = OrderedDict()
        self.sections = build_section_index(doc)
        self.page_texts: list[str] = []
        self.page_blocks: list[list[tuple[int, int]]] = []
        # Retain only strings/intervals, and at most two pages of glyph geometry.
        for pi in range(doc.page_count):
            index = self.index(pi)
            self.page_texts.append(index.text)
            self.page_blocks.append(index.blocks)

    def index(self, page_index: int) -> TextIndex:
        index = self._index_cache.pop(page_index, None)
        if index is None:
            index = TextIndex(self.doc[page_index])
        self._index_cache[page_index] = index
        if len(self._index_cache) > 2:
            self._index_cache.popitem(last=False)
        return index

    def candidates(self, anchor: Anchor, *, limit: int) -> tuple[list[Candidate], int]:
        query = normalize(anchor.selected_text)
        if not query:
            return [], 0
        spans: list[tuple[int, int, int, str]] = []
        for pi, text in enumerate(self.page_texts):
            start = 0
            while True:
                hit = text.find(query, start)
                if hit < 0:
                    break
                spans.append((pi, hit, hit + len(query), "normalized-exact"))
                start = hit + 1
            # A changed original and an unchanged decoy may coexist. Generate
            # fuzzy alternatives even if an exact match exists elsewhere.
            if len(query) < 8:
                continue
            # An exact decoy must not hide changed text in the same block.
            occupied = sorted((s, e) for p, s, e, _ in spans if p == pi)
            regions = deque()
            for lo, hi in self.page_blocks[pi]:
                cursor = lo
                for start, end in occupied:
                    if end <= cursor:
                        continue
                    if start >= hi:
                        break
                    if start > cursor:
                        regions.append((cursor, start))
                    cursor = max(cursor, end)
                if cursor < hi:
                    regions.append((cursor, hi))
            while regions:
                lo, hi = regions.popleft()
                if hi - lo < 0.6 * len(query):
                    continue
                alignment = partial_ratio_alignment(
                    query, text[lo:hi], score_cutoff=self.minimum_text * 100
                )
                if alignment is None:
                    continue
                a, b = lo + alignment.dest_start, lo + alignment.dest_end
                if a >= b:
                    continue
                regions.extend([(lo, a), (b, hi)])
                matched = text[a:b]
                similarity = ratio(query, matched) / 100
                if (
                    similarity < self.minimum_text
                    or min(len(query), len(matched)) / max(len(query), len(matched)) < 0.60
                ):
                    continue
                spans.append((pi, a, b, "fuzzy"))
        candidates = []
        exact_total = sum(method == "normalized-exact" for _, _, _, method in spans)
        exact_rank = 0
        for pi, start, end, method in spans:
            index = self.index(pi)
            if not index.closed_span(start, end):
                continue
            quads = index.quads(start, end)
            if not quads:
                continue
            before, after = index.context(start, end)
            matched = index.text[start:end]
            section = section_for(self.sections, pi, (quads[0][1] + quads[0][7]) / 2)
            layout = _layout_score(
                anchor,
                quads[0],
                bounds(self.doc[pi]),
                v2_rank=exact_rank if method == "normalized-exact" else None,
                v2_total=exact_total,
                candidate_section_path=section.path if section else None,
            )
            if method == "normalized-exact":
                exact_rank += 1
            upstream = _Candidate(
                page_index=pi,
                quads=quads,
                matched_text=matched,
                text_similarity=ratio(query, matched) / 100,
                context_similarity=context_similarity(
                    anchor.context_before, anchor.context_after, before, after, mode="mean"
                ),
                layout_score=layout,
                page_proximity=_proximity(pi - anchor.page_index, 3),
                length_similarity=_length_similarity(len(query), len(matched)),
                window_start=start,
            )
            candidates.append(
                Candidate(
                    id=_id("c-", f"{anchor.annotation_id}:{pi}:{start}:{end}"),
                    page_index=pi,
                    quads=quads,
                    matched_text=matched,
                    context_before=before,
                    context_after=after,
                    score=upstream.score,
                    evidence=Evidence(
                        text=upstream.text_similarity,
                        context=upstream.context_similarity,
                        layout=upstream.layout_score,
                        page_proximity=upstream.page_proximity,
                        length=upstream.length_similarity,
                        page_delta=pi - anchor.page_index,
                        method=method,
                    ),
                )
            )
        candidates.sort(key=lambda c: (-c.score, c.page_index, c.id))
        return candidates[:limit], len(candidates)
