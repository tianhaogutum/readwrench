"""Run one proof: admit the manuscript, build or load the candidate, shape both, write the report.

Imports fontTools, uharfbuzz, regex and brotli at module level; :mod:`.cli` imports this
module only after parsing the arguments.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

from fontTools import subset
from fontTools.ttLib import TTLibError
import uharfbuzz as hb
import brotli  # installed by fonttools[woff]

from readwrench import __version__
from .core import FontAsset, compare_shapes, coverage, file_hash, file_state, is_selector, make_subset
from .inputs import InputError, MAX_WORK_CHARS, assignments, read_runs, tag
from .specimen import render

MAX_EFFECTIVE_LOCATIONS = 32
MAX_TOTAL_GLYPHS = 100_000

# Expected input, scope and I/O failures: reported as one line on stderr, exit status 2.
EXPECTED_ERRORS = (InputError, OSError, UnicodeError, json.JSONDecodeError, TTLibError, ValueError,
                   KeyError, IndexError, RecursionError, struct.error, zlib.error, brotli.error,
                   subset.Subsetter.SubsettingError)

def execute(args: argparse.Namespace) -> dict:
    if args.out.exists() or args.out.is_symlink():
        raise InputError(f"Output already exists: {args.out}; choose a new output directory")
    locations = [assignments(raw, floating=True) for raw in args.at] or [{}]
    if len(locations) > 32:
        raise InputError("At most 32 variation locations can be checked in one invocation")
    text_state = file_state(args.text)
    runs = read_runs(args.text, manifest=args.manifest, direction=args.direction, script=args.script,
                     language=args.language, features=assignments(args.features))
    text_sha256 = file_hash(args.text)
    if file_state(args.text) != text_state:
        raise InputError(f"Manuscript changed while it was being read: {args.text}; retry with a stable file")
    work_chars = sum(len(run.text) for run in runs) * len(locations)
    if work_chars > MAX_WORK_CHARS:
        raise InputError(f"Work budget exceeds {MAX_WORK_CHARS:,} characters × locations; split the manuscript or location matrix")
    effective_locations = {tuple(sorted((base | run.variations).items())) for run in runs for base in locations}
    if len(effective_locations) > MAX_EFFECTIVE_LOCATIONS:
        raise InputError(f"At most {MAX_EFFECTIVE_LOCATIONS} distinct effective variation locations are supported")
    original = FontAsset(args.font, args.font_index)
    candidate = None
    try:
        for run in runs:
            for location in locations:
                original.validate_settings(run, location | run.variations)
        covered = coverage(original, runs)
        report = {"schema_version": 1, "tool": "subset-proof", "version": __version__,
                  "mode": args.command, "accepted": False,
                  "versions": {"fonttools": version("fonttools"), "uharfbuzz": version("uharfbuzz"),
                               "harfbuzz": hb.version_string(), "regex": version("regex")},
                  "original": original.metadata, "candidate": None,
                  "text": {"path": str(args.text), "sha256": text_sha256,
                           "bytes": text_state[2], "format": "run_manifest" if args.manifest else "utf8_lines",
                           "run_count": len(runs), "index_units": "Unicode codepoints; line/column one-based; offset zero-based",
                           "manifest_locations": "line is one-based run number; offset is relative to run" if args.manifest else None},
                  "settings": {"locations": locations, "keep_features": None},
                  "scope": {"level": "explicit shaping runs, no bidi paragraph layout or line wrapping",
                            "unicode": "strict cmap coverage; supported UVS required; absent default-ignorables recorded as exemptions",
                            "comparison": "glyph identity, decomposed outlines, clusters, advances, offsets, directional font extents; 1e-9 em rounding",
                            "excludes": ["rasterization", "hinting", "color fonts", "font fallback", "paragraph layout"]},
                  "coverage": {"original": covered}, "issues": [], "results": []}
        # Stage every artifact together. A rejected document still gets diagnostics;
        # invalid inputs or exceptions leave no partial output directory.
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".subset-proof-", dir=args.out.parent) as stage:
            staging = Path(stage)
            mapping = None
            if covered["missing"]:
                report["issues"].append("original_missing_unicode")
            else:
                baseline = []
                original_glyph_count = 0
                for run in runs:
                    for base in locations:
                        location = base | run.variations
                        shape = original.shape(run, location)
                        original_glyph_count += len(shape["glyphs"])
                        if original_glyph_count > MAX_TOTAL_GLYPHS:
                            raise InputError(f"Original shaping exceeds {MAX_TOTAL_GLYPHS:,} total output glyphs; split the input")
                        baseline.append((run, location, shape))
                if args.command == "build":
                    keep = [tag(item) for item in args.keep_features.split(",") if item] if args.keep_features != "*" else ["*"]
                    absent = set(keep) - original.feature_tags - {"*"}
                    if absent:
                        raise InputError(f"Original font has no requested retained features: {', '.join(sorted(absent))}")
                    report["settings"]["keep_features"] = keep
                    report["settings"]["subsetter_ignore_missing_unicodes"] = any(is_selector(code) for code in covered["requested_unicodes"])
                    flavor = None if args.flavor == "sfnt" else args.flavor
                    extension = flavor or ("otf" if original.tt.sfntVersion == "OTTO" else "ttf")
                    candidate_path = staging / f"subset.{extension}"
                    shaped_gids = {glyph["gid"] for _, _, shape in baseline for glyph in shape["glyphs"]}
                    report["settings"]["retained_shaped_glyphs"] = len(shaped_gids)
                    mapping = make_subset(original, candidate_path, covered, keep, flavor, shaped_gids)
                    candidate = FontAsset(candidate_path)
                    candidate.metadata["path"] = candidate_path.name
                else:
                    candidate = FontAsset(args.candidate, args.candidate_index)
                candidate_coverage = coverage(candidate, runs)
                report["coverage"]["candidate"] = candidate_coverage
                report["candidate"] = candidate.metadata
                if candidate_coverage["missing"]:
                    report["issues"].append("candidate_missing_unicode")
                candidate_glyph_count = 0
                for run, location, left in baseline:
                    # Missing candidate feature tables are a rejection to detect,
                    # not an invalid original manuscript setting.
                    for axis in location:
                        if axis not in candidate.axes:
                            raise InputError(f"Candidate has no requested variation axis {axis}")
                    right = candidate.shape(run, location)
                    candidate_glyph_count += len(right["glyphs"])
                    if candidate_glyph_count > MAX_TOTAL_GLYPHS:
                        raise InputError(f"Candidate shaping exceeds {MAX_TOTAL_GLYPHS:,} total output glyphs; split the input")
                    compared = compare_shapes(left, right, mapping)
                    if not compared["equal"] and "shaping_difference" not in report["issues"]:
                        report["issues"].append("shaping_difference")
                    report["results"].append({"run": asdict(run), "location": location,
                                              "original": left, "candidate": right, "comparison": compared})
            report["accepted"] = not report["issues"]
            with (staging / "report.json").open("w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            with (staging / "specimen.html").open("w", encoding="utf-8") as stream:
                for chunk in render(report, original, candidate):
                    stream.write(chunk)
            # The destination did not exist at entry; re-check before publishing.
            if args.out.exists():
                raise InputError(f"Output appeared during the run: {args.out}")
            staging.rename(args.out)
        return report
    finally:
        original.close()
        if candidate is not None:
            candidate.close()


def rejection_diagnostics(report: dict) -> None:
    for side, covered in report["coverage"].items():
        for item in covered["missing"][:20]:
            print(f"{side}: run {item['run']!r}, line {item['line']}, column {item['column']}: "
                  f"{item['codepoint']} {item['name']} ({item['reason']})", file=sys.stderr)
        if len(covered["missing"]) > 20:
            print(f"{side}: {len(covered['missing']) - 20} more missing occurrences; see report.json", file=sys.stderr)
    different = [result for result in report["results"] if not result["comparison"]["equal"]]
    for result in different[:5]:
        comparison = result["comparison"]
        print(f"shaping: run {result['run']['id']!r}, axes {result['location']}: "
              f"{len(comparison['changes'])} glyph differences, {len(comparison['notdef'])} .notdef outputs, "
              f"{len(comparison['metric_changes'])} metric differences", file=sys.stderr)
    if len(different) > 5:
        print(f"shaping: {len(different) - 5} more differing run/location pairs; see report.json", file=sys.stderr)
