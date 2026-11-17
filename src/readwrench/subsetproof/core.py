"""Font I/O and outlines belong to fontTools; shaping belongs to HarfBuzz."""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
from io import BytesIO
import json
import math
from pathlib import Path
import struct
import stat
import unicodedata

from fontTools import subset
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
import regex
import uharfbuzz as hb

from .inputs import InputError, Run


MAX_FONT_BYTES = 50 * 1024 * 1024
MAX_GLYPHS_PER_SHAPE = 100_000
IGNORABLE = regex.compile(r"\p{Default_Ignorable_Code_Point}\Z")
UNSUPPORTED_TABLES = {"COLR", "CPAL", "CBDT", "CBLC", "sbix", "SVG ", "EBDT", "EBLC", "morx", "mort", "Silf", "DMAP"}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_state(path: Path) -> tuple:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise InputError(f"Expected a regular input file: {path}")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def is_selector(code: int) -> bool:
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


class FontAsset:
    def __init__(self, path: Path, index: int | None = None):
        self.path = path
        initial_state = file_state(path)
        size = initial_state[2]
        if not 0 < size <= MAX_FONT_BYTES:
            raise InputError(f"Font must contain 1–{MAX_FONT_BYTES} bytes: {path}")
        # One bounded snapshot drives both parsers and the provenance hash.
        # Uncompressed sfnt files can go directly to HarfBuzz; saving them
        # again solely for shaping needlessly compiles every font table.
        with path.open("rb") as stream:
            raw = stream.read(MAX_FONT_BYTES + 1)
        if len(raw) != size or file_state(path) != initial_state:
            raise InputError(f"Font changed while it was being read: {path}; retry with a stable file")
        header = raw[:20]
        if header[:4] in {b"wOFF", b"wOF2"}:
            if len(header) < 20 or struct.unpack(">I", header[16:20])[0] > MAX_FONT_BYTES:
                raise InputError("Compressed font advertises an excessive or invalid sfnt size")
        if header[:4] == b"ttcf" and index is None:
            raise InputError("Font collections require an explicit --font-index / --candidate-index")
        if index is not None and index < 0:
            raise InputError("Font index cannot be negative")
        if header[:4] != b"ttcf" and index not in {None, 0}:
            raise InputError("Standalone fonts have only face index 0")
        self.tt = TTFont(BytesIO(raw), fontNumber=index if index is not None else -1,
                         lazy=False, recalcTimestamp=False)
        unsupported = sorted(UNSUPPORTED_TABLES.intersection(self.tt.keys()))
        if unsupported:
            self.tt.close()
            raise InputError(f"Unsupported color/bitmap/AAT/Graphite/DMAP tables: {', '.join(unsupported)}; "
                             "use a dedicated rendering audit")
        if not ("glyf" in self.tt or "CFF " in self.tt or "CFF2" in self.tt):
            self.tt.close()
            raise InputError("Font has no supported TrueType/CFF outline table")
        self.upem = self.tt["head"].unitsPerEm
        if self.upem <= 0:
            raise InputError("Font has invalid unitsPerEm")
        self.cmap = self.tt.getBestCmap() or {}
        self.uvs = {}
        for table in self.tt["cmap"].tables:
            if table.format == 14:
                for selector, mappings in table.uvsDict.items():
                    self.uvs.setdefault(selector, {}).update(dict(mappings))
        self.order = self.tt.getGlyphOrder()
        self.axes = {axis.axisTag: {"min": axis.minValue, "default": axis.defaultValue,
                                  "max": axis.maxValue}
                     for axis in self.tt["fvar"].axes} if "fvar" in self.tt else {}
        self.feature_tags = set()
        for name in ("GSUB", "GPOS"):
            if name in self.tt and self.tt[name].table.FeatureList:
                self.feature_tags.update(record.FeatureTag for record in self.tt[name].table.FeatureList.FeatureRecord)
        if "kern" in self.tt:
            self.feature_tags.add("kern")
        self.flavor = self.tt.flavor or ("otf" if self.tt.sfntVersion == "OTTO" else "ttf")
        # HarfBuzz consumes sfnt data, including when the user supplied WOFF2/TTC.
        self.tt.flavor = None
        if self.flavor in {"ttf", "otf"} and header[:4] != b"ttcf":
            self.sfnt = raw
        else:
            stream = BytesIO()
            self.tt.save(stream)
            self.sfnt = stream.getvalue()
        if len(self.sfnt) > MAX_FONT_BYTES:
            raise InputError("Decoded font exceeds the size limit")
        self.face = hb.Face(self.sfnt)
        self.nominal_font = hb.Font(self.face)
        self._sets = {}
        self._glyphs = {}
        self.metadata = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": size,
                         "format": self.flavor, "face_index": index,
                         "units_per_em": self.upem, "glyph_count": len(self.order),
                         "axes": self.axes, "layout_features": sorted(self.feature_tags)}
        if file_state(path) != initial_state:
            self.tt.close()
            raise InputError(f"Font changed while it was being read: {path}; retry with a stable file")

    def close(self):
        self.tt.close()

    def validate_settings(self, run: Run, location: dict):
        for key, value in location.items():
            if key not in self.axes:
                raise InputError(f"Font {self.path.name} has no variation axis {key!r}")
            axis = self.axes[key]
            if not math.isfinite(value) or not axis["min"] <= value <= axis["max"]:
                raise InputError(f"Axis {key}={value} is outside [{axis['min']}, {axis['max']}]")
        absent = sorted(key for key, value in run.features.items() if value and key not in self.feature_tags)
        if absent:
            raise InputError(f"Font {self.path.name} does not provide requested features: {', '.join(absent)}")

    def glyph(self, gid: int, location: dict) -> dict:
        key = (gid, tuple(sorted(location.items())))
        if key in self._glyphs:
            return self._glyphs[key]
        location_key = key[1]
        if location_key not in self._sets:
            self._sets[location_key] = self.tt.getGlyphSet(location=location)
        glyph_set = self._sets[location_key]
        name = self.order[gid]
        glyph = glyph_set[name]
        recording = DecomposingRecordingPen(glyph_set, skipMissingComponents=False)
        glyph.draw(recording)

        def normalize(value):
            if isinstance(value, (int, float)):
                return round(value / self.upem, 9)
            if isinstance(value, (tuple, list)):
                return [normalize(item) for item in value]
            return value

        normalized = normalize(recording.value)
        fingerprint = hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()
        svg = SVGPathPen(glyph_set)
        recording.replay(svg)
        bounds = BoundsPen(glyph_set)
        recording.replay(bounds)
        result = {"name": name, "outline_sha256": fingerprint,
                  "path": svg.getCommands(), "bounds": bounds.bounds}
        self._glyphs[key] = result
        return result

    def shape(self, run: Run, location: dict) -> dict:
        font = hb.Font(self.face)
        font.scale = (self.upem, self.upem)
        font.set_variations(location)
        buffer = hb.Buffer()
        buffer.add_codepoints([ord(char) for char in run.text])
        buffer.language = run.language
        if run.script is not None:
            buffer.script = run.script
        if run.direction != "auto":
            buffer.direction = run.direction
        buffer.guess_segment_properties()
        hb.shape(font, buffer, run.features)
        if len(buffer.glyph_infos) > MAX_GLYPHS_PER_SHAPE:
            raise InputError(f"Shaping run exceeds {MAX_GLYPHS_PER_SHAPE:,} output glyphs")
        clusters = sorted({info.cluster for info in buffer.glyph_infos} | {len(run.text)})
        ends = dict(zip(clusters, clusters[1:]))
        glyphs = []
        for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions):
            drawing = self.glyph(info.codepoint, location)
            start = info.cluster
            end = ends.get(start, len(run.text))
            glyphs.append({"gid": info.codepoint, "name": drawing["name"],
                "outline_sha256": drawing["outline_sha256"], "cluster": start,
                "span": {"start": start, "end": end, "text": run.text[start:end],
                         **run.location(start)},
                "position": {key: round(value / self.upem, 9) for key, value in {
                    "x_advance": pos.x_advance, "y_advance": pos.y_advance,
                    "x_offset": pos.x_offset, "y_offset": pos.y_offset}.items()}})
        extents = font.get_font_extents(buffer.direction)
        return {"direction": buffer.direction, "script": buffer.script or "Zyyy",
                "language": buffer.language, "glyphs": glyphs,
                "font_extents_em": {key: round(getattr(extents, key) / self.upem, 9)
                                    for key in ("ascender", "descender", "line_gap")},
                "advance_em": {key: round(sum(glyph["position"][key + "_advance"] for glyph in glyphs), 9)
                               for key in ("x", "y")}}


def coverage(font: FontAsset, runs: list[Run]) -> dict:
    missing = []
    exemptions = []
    requested = set()
    for run in runs:
        for i, char in enumerate(run.text):
            code = ord(char)
            record = {"codepoint": f"U+{code:04X}", "character": char,
                      "name": unicodedata.name(char, "UNNAMED"), **run.location(i),
                      "context": run.text[max(0, i - 12): i + 13]}
            if is_selector(code):
                base = ord(run.text[i - 1]) if i else None
                mappings = font.uvs.get(code, {})
                variation_gid = font.nominal_font.get_variation_glyph(base, code) if base is not None else None
                if base in mappings and mappings[base] != ".notdef" and variation_gid not in {None, 0}:
                    requested.add(code)
                else:
                    missing.append(record | {"reason": "unsupported_variation_sequence",
                                              "base": f"U+{base:04X}" if base is not None else None})
            elif (code in font.cmap and font.tt.getGlyphID(font.cmap[code]) != 0
                  and font.nominal_font.get_nominal_glyph(code) not in {None, 0}):
                requested.add(code)
            elif IGNORABLE.fullmatch(char):
                exemptions.append(record | {"reason": "default_ignorable_without_cmap"})
            else:
                missing.append(record | {"reason": "missing_cmap_mapping"})
    return {"missing": missing, "exemptions": exemptions, "requested_unicodes": sorted(requested)}


def make_subset(source: FontAsset, destination: Path, covered: dict,
                keep_features: list[str], flavor: str | None = None,
                shaped_gids: set[int] | None = None) -> dict[int, int]:
    options = subset.Options()
    # fontTools 4.68 excludes variation selectors from matched_unicodes even
    # when a valid format-14 pair exists. Admission above checked each pair.
    # Only that verified case needs the compatibility switch (docs/subset-proof.md, "fontTools and variation selectors").
    options.ignore_missing_unicodes = any(is_selector(code) for code in covered["requested_unicodes"])
    options.layout_features = keep_features
    if "*" not in keep_features and "kern" not in keep_features:
        # Legacy kern is outside GSUB/GPOS feature filtering. Translate the
        # user's explicit feature-retention set through fontTools' own table
        # option so "drop all features" really drops legacy kerning too.
        options.drop_tables = list(options.drop_tables) + ["kern"]
    options.glyph_names = True
    options.notdef_outline = True
    options.recalc_timestamp = False
    font = TTFont(BytesIO(source.sfnt), recalcTimestamp=False)
    try:
        subsetter = subset.Subsetter(options)
        # Reuse HarfBuzz's upstream recorder strategy: retain glyphs produced by
        # source shaping as well as the literal codepoints. This also retains
        # cmap entries HarfBuzz needs for canonical composition and invisible
        # default-ignorable substitution (for example Aacute and space).
        subsetter.populate(unicodes=covered["requested_unicodes"], gids=shaped_gids or set())
        subsetter.subset(font)
        source_ids = {name: gid for gid, name in enumerate(source.order)}
        mapping = {gid: source_ids[name] for gid, name in enumerate(font.getGlyphOrder())}
        font.flavor = flavor
        font.save(destination)
        return mapping
    finally:
        font.close()


def compare_shapes(original: dict, candidate: dict, mapping: dict[int, int] | None = None) -> dict:
    before, after = original["glyphs"], candidate["glyphs"]
    changes = []
    before_keys = [(glyph["gid"] if mapping is not None else glyph["outline_sha256"], glyph["cluster"]) for glyph in before]
    after_keys = [(mapping.get(glyph["gid"]) if mapping is not None else glyph["outline_sha256"], glyph["cluster"]) for glyph in after]
    # difflib aligns unchanged suffixes after a missing ligature/glyph. The
    # common successful case avoids the matcher entirely. autojunk bounds work
    # for long repetitive manuscripts; alignment affects presentation only.
    if before_keys == after_keys:
        pairs = ((i, i) for i in range(len(before)))
    else:
        aligned = []
        for _, a0, a1, b0, b1 in SequenceMatcher(None, before_keys, after_keys, autojunk=True).get_opcodes():
            aligned.extend((a0 + i if a0 + i < a1 else None,
                            b0 + i if b0 + i < b1 else None)
                           for i in range(max(a1 - a0, b1 - b0)))
        pairs = iter(aligned)
    for original_index, candidate_index in pairs:
        left = before[original_index] if original_index is not None else None
        right = after[candidate_index] if candidate_index is not None else None
        fields = []
        if left is None or right is None:
            fields.append("glyph_count")
        else:
            if mapping is not None and mapping.get(right["gid"]) != left["gid"]:
                fields.append("glyph_identity")
            if left["outline_sha256"] != right["outline_sha256"]:
                fields.append("outline")
            if left["cluster"] != right["cluster"]:
                fields.append("cluster")
            if left["position"] != right["position"]:
                fields.append("position")
        if fields:
            changes.append({"original_index": original_index, "candidate_index": candidate_index,
                            "fields": fields, "original": left, "candidate": right})
    notdef = [{"side": side, "index": i, "span": glyph["span"]}
              for side, glyphs in (("original", before), ("candidate", after))
              for i, glyph in enumerate(glyphs) if glyph["gid"] == 0]
    metric_changes = [{"field": key, "original": original["font_extents_em"][key],
                       "candidate": candidate["font_extents_em"][key]}
                      for key in original["font_extents_em"]
                      if original["font_extents_em"][key] != candidate["font_extents_em"][key]]
    return {"equal": not changes and not notdef and not metric_changes,
            "identity_method": "original_gid_mapping_and_outline" if mapping is not None else "outline_equivalence",
            "changes": changes, "metric_changes": metric_changes, "notdef": notdef}
