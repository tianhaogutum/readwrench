"""Read explicit shaping runs; never pretend HarfBuzz is a paragraph engine."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
import unicodedata

from fontTools.unicodedata import Scripts, script as unicode_script


class InputError(ValueError):
    """An input is invalid or outside the supported proof scope."""


MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_RUN_CHARS = 100_000
MAX_RUNS = 20_000
MAX_WORK_CHARS = 50_000
TAG = re.compile(r"[A-Za-z0-9 ]{4}\Z", re.ASCII)
SCRIPT_CODES = frozenset(Scripts.VALUES)
BIDI_CONTROLS = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x200E, 0x200F, 0x061C}


def tag(value: str) -> str:
    if not isinstance(value, str) or not TAG.fullmatch(value):
        raise InputError(f"Expected a four-character OpenType tag, got {value!r}")
    return value


def assignments(value: str, *, floating: bool = False) -> dict:
    result = {}
    if not value:
        return result
    for item in value.split(","):
        key, sep, raw = item.partition("=")
        if not sep:
            raise InputError(f"Expected TAG=value, got {item!r}")
        key = tag(key)
        if key in result:
            raise InputError(f"Duplicate tag {key!r}")
        try:
            number = float(raw) if floating else int(raw)
        except ValueError as exc:
            raise InputError(f"Invalid value for {key}: {raw!r}") from exc
        if floating and not math.isfinite(number):
            raise InputError(f"Axis {key} must be finite")
        if not floating and not 0 <= number <= 65535:
            raise InputError(f"Feature {key} must be an integer from 0 to 65535")
        result[key] = number
    return result


@dataclass(frozen=True)
class Run:
    id: str
    text: str
    line: int
    offset: int = 0
    direction: str = "auto"
    script: str | None = None
    language: str = "und"
    features: dict[str, int] = field(default_factory=dict)
    variations: dict[str, float] = field(default_factory=dict)

    def location(self, index: int) -> dict:
        return {"run": self.id, "line": self.line, "column": index + 1,
                "offset": self.offset + index}


def validate_run(run: Run) -> Run:
    if not run.text or len(run.text) > MAX_RUN_CHARS:
        raise InputError(f"Run {run.id!r} must contain 1–{MAX_RUN_CHARS} characters")
    if not isinstance(run.direction, str) or run.direction not in {"auto", "ltr", "rtl", "ttb", "btt"}:
        raise InputError(f"Run {run.id!r}: invalid direction {run.direction!r}")
    if run.script is not None:
        tag(run.script)
        if run.script not in SCRIPT_CODES:
            raise InputError(f"Run {run.id!r}: unknown Unicode script code {run.script!r}")
    if not isinstance(run.language, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,63}", run.language):
        raise InputError(f"Run {run.id!r}: expected a BCP47 language tag")
    strong_scripts = set()
    bidi_classes = set()
    has_numbers = False
    for i, char in enumerate(run.text):
        code = ord(char)
        if unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} or code in BIDI_CONTROLS:
            raise InputError(f"Run {run.id!r}, column {i + 1}: unsupported control U+{code:04X}; "
                             "supply explicit logical runs without tabs or bidi controls")
        script = unicode_script(char)
        if script not in {"Zyyy", "Zinh", "Zzzz"}:
            strong_scripts.add(script)
        bidi = unicodedata.bidirectional(char)
        has_numbers |= bidi in {"EN", "AN"}
        if bidi in {"L", "R", "AL"}:
            bidi_classes.add("R" if bidi in {"R", "AL"} else "L")
    # HarfBuzz does not perform bidi itemization. A run must already be itemized.
    if len(bidi_classes) > 1:
        raise InputError(f"Run {run.id!r} mixes LTR and RTL text; split it into explicit runs")
    if "R" in bidi_classes and has_numbers:
        raise InputError(f"Run {run.id!r} mixes RTL text and numbers; split number sequences into separate LTR runs")
    if len(strong_scripts) > 1:
        raise InputError(f"Run {run.id!r} mixes scripts {', '.join(sorted(strong_scripts))}; "
                         "split it into explicit runs")
    return run


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _mapping(value: object, *, floating: bool, name: str) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{name} must be an object")
    result = {}
    for key, number in value.items():
        tag(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise InputError(f"{name}.{key} must be a number")
        if floating:
            try:
                converted = float(number)
            except OverflowError as exc:
                raise InputError(f"{name}.{key} must be finite") from exc
            if not math.isfinite(converted):
                raise InputError(f"{name}.{key} must be finite")
            result[key] = converted
        elif not isinstance(number, int) or not 0 <= number <= 65535:
            raise InputError(f"{name}.{key} must be an integer from 0 to 65535")
        else:
            result[key] = number
    return result


def read_runs(path: Path, *, manifest: bool = False, direction: str = "auto",
              script: str | None = None, language: str = "und",
              features: dict | None = None) -> list[Run]:
    if path.stat().st_size > MAX_TEXT_BYTES:
        raise InputError(f"Text input exceeds {MAX_TEXT_BYTES} bytes")
    with path.open("rb") as stream:
        raw = stream.read(MAX_TEXT_BYTES + 1)
    if len(raw) > MAX_TEXT_BYTES:
        raise InputError(f"Text input exceeds {MAX_TEXT_BYTES} bytes")
    # A JSON file's optional encoding BOM is outside its text fields. Plain
    # manuscript BOMs remain literal codepoints with unchanged source offsets.
    text = raw.decode("utf-8-sig" if manifest else "utf-8")
    features = dict(features or {})
    runs = []
    total_chars = 0

    def add(run):
        nonlocal total_chars
        total_chars += len(run.text)
        if total_chars > MAX_WORK_CHARS:
            raise InputError(f"Manuscript exceeds the {MAX_WORK_CHARS:,}-character work budget; split it into batches")
        if len(runs) >= MAX_RUNS:
            raise InputError(f"Input exceeds {MAX_RUNS:,} nonempty shaping runs")
        runs.append(validate_run(run))

    if manifest:
        data = json.loads(text, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) != {"runs"} or not isinstance(data["runs"], list):
            raise InputError('Manifest must be an object with one "runs" array')
        if len(data["runs"]) > MAX_RUNS:
            raise InputError(f"Manifest exceeds {MAX_RUNS:,} runs")
        for n, value in enumerate(data["runs"], 1):
            allowed = {"id", "text", "direction", "script", "language", "features", "variations"}
            if not isinstance(value, dict) or set(value) - allowed:
                raise InputError(f"Run {n}: unknown fields or non-object run")
            if not isinstance(value.get("text"), str):
                raise InputError(f"Run {n}: text must be a string")
            run_id = value.get("id", str(n))
            if not isinstance(run_id, str) or not run_id or len(run_id) > 200:
                raise InputError(f"Run {n}: id must be a nonempty string of at most 200 characters")
            if any(unicodedata.category(char).startswith("C") for char in run_id):
                raise InputError(f"Run {n}: id cannot contain control or format characters")
            run_features = features | _mapping(value.get("features", {}), floating=False, name="features")
            run_variations = _mapping(value.get("variations", {}), floating=True, name="variations")
            add(Run(run_id, value["text"], n,
                direction=value.get("direction", direction), script=value.get("script", script),
                language=value.get("language", language), features=run_features, variations=run_variations))
    else:
        # Preserve CRLF offsets and do not normalize or compose manuscript text.
        offset = 0
        for n, line in enumerate(re.split(r"(\r\n|\r|\n|\u2028|\u2029)", text)):
            if n % 2:
                offset += len(line)
                continue
            if line:
                add(Run(str(n // 2 + 1), line, n // 2 + 1, offset,
                        direction, script, language, features))
            offset += len(line)
    if not runs or len(runs) > MAX_RUNS:
        raise InputError(f"Input must contain 1–{MAX_RUNS} nonempty shaping runs")
    if len({run.id for run in runs}) != len(runs):
        raise InputError("Run IDs must be unique")
    return runs
