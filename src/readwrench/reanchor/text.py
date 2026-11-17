"""Map MuPDF's real glyph geometry to normalized quote ranges.

No PDF syntax is parsed here. PyMuPDF supplies layout, characters and quad recovery.
Normalization retains an offset map so a multi-line quote remains one annotation.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

import pymupdf
import regex

_PUNCT = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize_with_offsets(raw: str) -> tuple[str, list[tuple[int, int]]]:
    # Dehyphenation applies only across an actual line break, not ordinary hyphens.
    skip: set[int] = set()
    for match in re.finditer(r"(?<=\w)-[ \t]*\n\s*(?=\w)", raw):
        skip.update(range(match.start(), match.end()))
    out: list[str] = []
    offsets: list[tuple[int, int]] = []
    for match in regex.finditer(r"\X", raw):
        start, end = match.span()
        cluster = "".join(
            c
            for i, c in enumerate(match.group(), start)
            if i not in skip and c not in "\u00ad\u200b\ufeff"
        )
        if not cluster:
            continue
        for part in unicodedata.normalize("NFKC", cluster).translate(_PUNCT).casefold():
            if part.isspace():
                if out and out[-1] != " ":
                    out.append(" ")
                    offsets.append((start, end))
            else:
                out.append(part)
                offsets.append((start, end))
    if out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def normalize(text: str) -> str:
    return normalize_with_offsets(text)[0]


@dataclass
class Glyph:
    text: str
    line: int
    block: int
    direction: tuple[float, float]
    quad: pymupdf.Quad


class TextIndex:
    def __init__(self, page: pymupdf.Page):
        self.glyphs: list[Glyph] = []
        raw: list[str] = []
        raw_to_glyph: list[int] = []
        line_no = 0
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        for bi, block in enumerate(page.get_text("rawdict", flags=flags)["blocks"]):
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                direction = tuple(line["dir"])
                for span in line["spans"]:
                    for char in span["chars"]:
                        gi = len(self.glyphs)
                        quad = pymupdf.recover_char_quad(direction, span, char)
                        self.glyphs.append(Glyph(char["c"], line_no, bi, direction, quad))
                        raw.extend(char["c"])
                        raw_to_glyph.extend([gi] * len(char["c"]))
                raw.append("\n")
                raw_to_glyph.append(-1)
                line_no += 1
            raw.append("\n")
            raw_to_glyph.append(-1)
        self.raw = "".join(raw)
        self.text, offsets = normalize_with_offsets(self.raw)
        self.positions = [raw_to_glyph[start] for start, _ in offsets]
        self.cluster_glyphs: dict[int, tuple[int, ...]] = {}
        for start, end in offsets:
            glyphs = tuple(dict.fromkeys(gi for gi in raw_to_glyph[start:end] if gi >= 0))
            if glyphs and glyphs != (raw_to_glyph[start],):
                self.cluster_glyphs[raw_to_glyph[start]] = glyphs
        # Block intervals enable several changed-text candidates per page.
        self.blocks: list[tuple[int, int]] = []
        start = 0
        previous = None
        for pos, gi in enumerate(self.positions):
            if gi < 0:
                continue
            block = self.glyphs[gi].block
            if previous is not None and block != previous:
                self.blocks.append((start, pos))
                start = pos
            previous = block
        if self.text:
            self.blocks.append((start, len(self.text)))
        line_ranges: dict[int, list[int]] = {}
        for pos, gi in enumerate(self.positions):
            if gi >= 0:
                line = self.glyphs[gi].line
                if line not in line_ranges:
                    line_ranges[line] = [pos, pos + 1]
                else:
                    line_ranges[line][1] = pos + 1
        self.lines = [tuple(span) for span in line_ranges.values()]

    def nearest_line(self, point: pymupdf.Point) -> tuple[int, int] | None:
        choices = []
        for start, end in self.lines:
            quads = self.quads(start, end)
            if not quads:
                continue
            quad = quad_from_list(quads[0])
            gi = next(gi for gi in self.positions[start:end] if gi >= 0)
            dx, dy = self.glyphs[gi].direction
            px, py = -dy, dx
            pts = [quad.ul, quad.ur, quad.ll, quad.lr]
            along = [p.x * dx + p.y * dy for p in pts]
            across = [p.x * px + p.y * py for p in pts]
            u, v = point.x * dx + point.y * dy, point.x * px + point.y * py
            da = max(min(along) - u, 0, u - max(along))
            dp = max(min(across) - v, 0, v - max(across))
            # Margin notes often sit beyond the end of a short line. Give
            # baseline alignment more weight than horizontal margin distance.
            choices.append((da * da + 4 * dp * dp, start, end))
        if not choices:
            return None
        _, start, end = min(choices)
        return start, end

    def context(self, start: int, end: int) -> tuple[str, str]:
        return self.text[max(0, start - 300) : start], self.text[end : end + 300]

    def closed_span(self, start: int, end: int) -> bool:
        """A normalized substring cannot select only half of a ligature glyph."""
        return not (
            (
                start > 0
                and self.positions[start] >= 0
                and self.positions[start] == self.positions[start - 1]
            )
            or (
                end < len(self.positions)
                and self.positions[end - 1] >= 0
                and self.positions[end - 1] == self.positions[end]
            )
        )

    def selected_range(self, quads: list[list[float]]) -> tuple[int, int] | None:
        regions = [quad_from_list(q) for q in quads]
        selected = set()
        for i, glyph in enumerate(self.glyphs):
            center = glyph.quad.rect.tl + (glyph.quad.rect.br - glyph.quad.rect.tl) * 0.5
            if any(region.is_convex and center in region for region in regions):
                selected.add(i)

        def complete(gi):
            return gi >= 0 and all(g in selected for g in self.cluster_glyphs.get(gi, (gi,)))

        offsets = [i for i, gi in enumerate(self.positions) if complete(gi)]
        if not offsets:
            return None
        start, end = offsets[0], offsets[-1] + 1
        # Do not manufacture a quote across disjoint, noncontiguous highlights.
        if any(
            gi >= 0 and not complete(gi) and not self.text[i].isspace()
            for i, gi in enumerate(self.positions[start:end], start)
        ):
            return None
        return start, end

    def quads(self, start: int, end: int) -> list[list[float]]:
        by_line: dict[int, list[Glyph]] = {}
        seen = set()
        for primary in self.positions[start:end]:
            for gi in self.cluster_glyphs.get(primary, (primary,)):
                if gi < 0 or gi in seen:
                    continue
                seen.add(gi)
                glyph = self.glyphs[gi]
                if not glyph.text.isspace():
                    by_line.setdefault(glyph.line, []).append(glyph)
        result = []
        for glyphs in by_line.values():
            dx, dy = glyphs[0].direction
            px, py = -dy, dx
            pts = [p for g in glyphs for p in (g.quad.ul, g.quad.ur, g.quad.ll, g.quad.lr)]
            along = [p.x * dx + p.y * dy for p in pts]
            across = [p.x * px + p.y * py for p in pts]
            lo_a, hi_a = min(along), max(along)
            lo_p, hi_p = min(across), max(across)
            q = [
                lo_a * dx + lo_p * px,
                lo_a * dy + lo_p * py,
                hi_a * dx + lo_p * px,
                hi_a * dy + lo_p * py,
                lo_a * dx + hi_p * px,
                lo_a * dy + hi_p * py,
                hi_a * dx + hi_p * px,
                hi_a * dy + hi_p * py,
            ]
            if all(math.isfinite(v) for v in q):
                result.append(q)
        return result


def quad_from_list(q: list[float]) -> pymupdf.Quad:
    return pymupdf.Quad([pymupdf.Point(q[i], q[i + 1]) for i in range(0, 8, 2)])


def bounds(page: pymupdf.Page) -> pymupdf.Rect:
    """All annotation/text coordinates are in the unrotated page space."""
    return page.rect * page.derotation_matrix
