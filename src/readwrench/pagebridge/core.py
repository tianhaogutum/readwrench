"""Bounded PDF feature extraction, candidate assignment, and source verification."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import dhash
import numpy as np
import pymupdf as fitz
from PIL import Image
from rapidfuzz.fuzz import ratio
from scipy.optimize import linear_sum_assignment

from readwrench import __version__

from .mapping import (  # noqa: F401  (re-exported for callers and tests)
    MAX_PAGES,
    SCHEMA,
    BridgeError,
    approval_snapshot,
    read_plan,
    sha256,
    utc_now,
    validate_map,
)

THUMB_SIZE = (144, 192)
MIN_SCORE = 0.62
CONFIDENT_SCORE = 0.86
MIN_MARGIN = 0.08
MAX_PAGE_IMAGE_PIXELS = 40_000_000


@contextmanager
def open_pdf(path: Path):
    """Open only PDFs and enforce the MVP bound before rendering anything."""
    doc = None
    try:
        if not path.is_file():
            raise BridgeError(f"PDF does not exist: {path}")
        doc = fitz.open(path)
        if not doc.is_pdf:
            raise BridgeError(f"Input is not a PDF: {path}")
        if doc.needs_pass:
            raise BridgeError(f"Encrypted PDF requires decryption first: {path}")
        if not 1 <= len(doc) <= MAX_PAGES:
            raise BridgeError(f"Expected 1-{MAX_PAGES} pages, found {len(doc)}: {path}")
        if doc.get_ocgs():
            raise BridgeError(
                f"PDF contains optional-content layers; "
                f"flatten visible layers before generating a new plan: {path}"
            )
        for page in doc:
            rect = page.rect
            if not all(math.isfinite(v) and 0 < v <= 14400 for v in (rect.width, rect.height)):
                raise BridgeError(f"Unsupported page geometry on page {page.number + 1}: {path}")
            # A small output raster can still hide a huge decoded source image.
            # Read XObject metadata before asking the renderer to decode images.
            images = {image[0]: image for image in page.get_images(full=True)}
            pixels = sum(image[2] * image[3] for image in images.values())
            if pixels > MAX_PAGE_IMAGE_PIXELS:
                raise BridgeError(
                    f"Page {page.number + 1} exceeds the image budget of "
                    f"{MAX_PAGE_IMAGE_PIXELS:,} source pixels: {path}"
                )
        yield doc
    except BridgeError:
        raise
    except (RuntimeError, fitz.FileDataError) as exc:
        raise BridgeError(f"Cannot read PDF {path}: {exc}") from exc
    finally:
        if doc is not None:
            doc.close()


def render(page, *, max_edge=1400, dpi=120, scale=None) -> Image.Image:
    """Render one page; max_edge bounds decoded raster allocation."""
    if scale is None:
        scale = min(dpi / 72, max_edge / max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def thumbnail(page) -> Image.Image:
    image = render(page, max_edge=max(THUMB_SIZE), dpi=72)
    image.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", THUMB_SIZE, "white")
    canvas.paste(image, ((THUMB_SIZE[0] - image.width) // 2, (THUMB_SIZE[1] - image.height) // 2))
    return canvas


def normalize_text(text: str) -> str:
    # Digits are intentionally preserved: a changed total is real content.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


@dataclass
class Feature:
    page: int
    width: float
    height: float
    text: str
    image_hash: int
    image_digest: str
    ink: np.ndarray
    ink_amount: float

    def public(self):
        return {
            "page": self.page,
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "text_excerpt": self.text[:280],
            "text_chars": len(self.text),
            "thumbnail_sha256": self.image_digest,
            "blank_looking": self.ink_amount < 0.001,
        }


def extract(path: Path, assets: Path | None = None, prefix="page"):
    features = []
    if not path.is_file():
        raise BridgeError(f"PDF does not exist or is not a regular file: {path}")
    identity = {"path": str(path.resolve()), "name": path.name, "sha256": sha256(path)}
    with open_pdf(path) as doc:
        identity["pages"] = len(doc)
        for page in doc:
            image = thumbnail(page)
            if assets is not None:
                image.save(assets / f"{prefix}-{page.number + 1:03}.png")
            gray = image.convert("L").resize((72, 96), Image.Resampling.LANCZOS)
            ink = (255 - np.asarray(gray, dtype=np.float32)) / 255
            features.append(
                Feature(
                    page.number + 1,
                    page.rect.width,
                    page.rect.height,
                    normalize_text(page.get_text("text", sort=True))[:12000],
                    dhash.dhash_int(image, size=16),
                    hashlib.sha256(image.tobytes()).hexdigest(),
                    ink.reshape(-1),
                    float(ink.mean()),
                )
            )
            fitz.TOOLS.store_shrink(100)
    if sha256(path) != identity["sha256"]:
        raise BridgeError(f"PDF changed during analysis; retry with a stable file: {path}")
    return identity, features


def similarity(a: Feature, b: Feature):
    """Heuristic evidence for correspondence, never a probability of correctness."""
    denom = float(np.linalg.norm(a.ink) * np.linalg.norm(b.ink))
    ink_score = float(np.dot(a.ink, b.ink)) / denom if denom else 0.0
    # dhash returns both horizontal and vertical differences: 2 * size**2 bits.
    hash_score = 1 - (a.image_hash ^ b.image_hash).bit_count() / 512
    visual = 1.0 if a.image_digest == b.image_digest else 0.7 * ink_score + 0.3 * hash_score
    aspect_a, aspect_b = a.width / a.height, b.width / b.height
    visual *= min(aspect_a, aspect_b) / max(aspect_a, aspect_b)
    text = ratio(a.text, b.text) / 100 if a.text and b.text else None
    score = 0.75 * text + 0.25 * visual if text is not None else visual
    return round(score, 6), None if text is None else round(text, 6), round(visual, 6)


def page_relation(original, modified, pairs):
    """Separate structural correspondence from any detected content change."""
    if original is None:
        return "inserted"
    if modified is None:
        return "deleted"
    if any(
        a is not None and b is not None and (original - a) * (modified - b) < 0 for a, b in pairs
    ):
        return "reordered"
    return "same_number" if original == modified else "shifted"


def proposed_map(original: list[Feature], modified: list[Feature], min_score=MIN_SCORE):
    n, m = len(original), len(modified)
    evidence = [[similarity(a, b) for b in modified] for a in original]
    scores = np.asarray([[v[0] for v in row] for row in evidence])
    # One dummy destination per old page permits deletion instead of a weak match.
    utility = np.zeros((n, m + n))
    # A candidate on the inclusive floor must beat a dummy even when reordered.
    utility[:, :m] = np.where(scores >= min_score, scores - min_score + 1e-6, -1.0)
    # Stable tie preference only; duplicate pages still get zero ambiguity margin.
    utility[:, :m] -= np.abs(np.arange(n)[:, None] - np.arange(m)[None, :]) * 1e-12
    old_indices, new_indices = linear_sum_assignment(utility, maximize=True)
    matches = {
        int(i): int(j)
        for i, j in zip(old_indices, new_indices)
        if j < m and scores[i, j] >= min_score
    }
    used = set(matches.values())
    matched_pairs = [(i + 1, j + 1) for i, j in matches.items()]
    rows = []
    for i, a in enumerate(original):
        candidates = sorted(range(m), key=lambda j: (-scores[i, j], j))[:3]
        alternatives = [{"modified": j + 1, "score": float(scores[i, j])} for j in candidates]
        if i not in matches:
            rows.append(
                {
                    "original": i + 1,
                    "modified": None,
                    "score": None,
                    "status": "pending",
                    "reasons": ["unmatched_original"],
                    "alternatives": alternatives,
                    "confirmed": False,
                }
            )
            continue
        j = matches[i]
        score, text, visual = evidence[i][j]
        competitors = [scores[i, k] for k in range(m) if k != j]
        competitors += [scores[k, j] for k in range(n) if k != i]
        margin = score - max(competitors, default=0.0)
        reasons = []
        if score < CONFIDENT_SCORE:
            reasons.append("low_similarity")
        if margin < MIN_MARGIN:
            reasons.append("competing_candidate")
        if a.ink_amount < 0.001 or modified[j].ink_amount < 0.001:
            reasons.append("blank_looking")
        if text is None:
            reasons.append("visual_only")
        if page_relation(i + 1, j + 1, matched_pairs) == "reordered":
            reasons.append("reordered")
        rows.append(
            {
                "original": i + 1,
                "modified": j + 1,
                "score": score,
                "text_score": text,
                "visual_score": visual,
                "margin": round(margin, 6),
                "status": "pending" if reasons else "suggested",
                "reasons": reasons,
                "alternatives": alternatives,
                "confirmed": False,
            }
        )
    for j in range(m):
        if j not in used:
            rows.append(
                {
                    "original": None,
                    "modified": j + 1,
                    "score": None,
                    "status": "pending",
                    "reasons": ["unmatched_modified"],
                    "alternatives": [],
                    "confirmed": False,
                }
            )
    return rows


def _distribution_version(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not-installed"


def engine_versions():
    """Record the installed host engine and renderer versions in the plan."""
    return (
        f"py-pdf-compare=={_distribution_version('py-pdf-compare')}; "
        f"PyMuPDF=={_distribution_version('PyMuPDF')}"
    )


def make_plan(original, modified, assets, min_score=MIN_SCORE):
    a_id, a = extract(original, assets, "original")
    b_id, b = extract(modified, assets, "modified")
    return {
        "schema": SCHEMA,
        "version": __version__,
        "created_at": utc_now(),
        "engine": engine_versions(),
        "sources": {"original": a_id, "modified": b_id},
        "settings": {
            "minimum_match_score": min_score,
            "confident_score": CONFIDENT_SCORE,
            "minimum_margin": MIN_MARGIN,
            "scores_are_probabilities": False,
        },
        "pages": {"original": [v.public() for v in a], "modified": [v.public() for v in b]},
        "rows": proposed_map(a, b, min_score),
        "review": None,
    }


def verify_sources(data, original: Path | None = None, modified: Path | None = None):
    paths = {}
    for side, override in (("original", original), ("modified", modified)):
        source = data["sources"][side]
        path = (override or Path(source["path"])).resolve()
        if not path.is_file():
            raise BridgeError(f"Missing {side} PDF: {path}; use --{side} to relocate it")
        if sha256(path) != source["sha256"]:
            raise BridgeError(
                f"{side.capitalize()} PDF SHA-256 changed; generate and review a new plan"
            )
        with open_pdf(path) as doc:
            if len(doc) != source["pages"]:
                raise BridgeError(f"{side} page count does not match the approved mapping")
        paths[side] = path
    return paths
