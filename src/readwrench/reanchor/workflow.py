"""Local evidence bundles and fail-closed, explicitly confirmed PDF writeback."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

from readwrench import __version__
from .adapter import Matcher, source_annotations
from .models import Asset, Decisions, InputPDF, Proposal
from .text import TextIndex, bounds, normalize, quad_from_list


class ReviewError(ValueError):
    """An actionable input/confirmation error, without a Python traceback."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )


def read_json(path: Path) -> dict:
    return json.loads(read_contract_bytes(path))


def read_contract_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(64 * 1024 * 1024 + 1)
    if len(content) > 64 * 1024 * 1024:
        raise ReviewError("JSON file exceeds the 64 MiB review-contract limit")
    return content


@contextmanager
def open_pdf(path: Path):
    with pymupdf.open(path) as doc:
        if not doc.is_pdf or doc.page_count < 1:
            raise ReviewError(f"Expected a nonempty PDF: {path}")
        if doc.is_encrypted or doc.needs_pass or (doc.metadata or {}).get("encryption"):
            raise ReviewError(
                f"Encrypted PDFs are not supported; use a separately decrypted copy: {path}"
            )
        yield doc


@contextmanager
def staging_directory(output: Path):
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ReviewError(f"Output already exists; choose a new directory: {output}")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        yield temporary
        if output.exists() or output.is_symlink():
            raise ReviewError(f"Output appeared during processing: {output}")
        os.rename(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def render_asset(
    page: pymupdf.Page,
    root: Path,
    name: str,
    *,
    rect: pymupdf.Rect | None = None,
    quads: list[list[float]] | None = None,
) -> Asset:
    path = root / "assets" / name
    path.parent.mkdir(exist_ok=True)
    clip = None
    if rect is not None:
        rect = pymupdf.Rect(rect.x0 - 65, rect.y0 - 55, rect.x1 + 65, rect.y1 + 55)
        clip = (rect & bounds(page)) * page.rotation_matrix
        if clip.is_empty or clip.is_infinite:
            clip = None
    view = clip or page.rect
    scale = min(1.6, 1400 / max(view.width, view.height))
    annotation = None
    try:
        if quads:
            annotation = page.add_highlight_annot([quad_from_list(q) for q in quads])
            annotation.set_colors(stroke=(0, 0.75, 1))
            annotation.set_opacity(0.45)
            annotation.update()
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
        pixmap.save(path)
    finally:
        if annotation is not None:
            page.delete_annot(annotation)
    return Asset(path="assets/" + name, sha256=sha256(path))


def quads_rect(quads: list[list[float]]) -> pymupdf.Rect:
    rect = pymupdf.Rect()
    for q in quads:
        rect |= quad_from_list(q).rect
    return rect


def propose(
    old: Path, new: Path, output: Path, *, candidates: int = 5, minimum_text: float = 0.78
) -> Proposal:
    from .review import write_review

    if isinstance(candidates, bool) or not isinstance(candidates, int) or not 1 <= candidates <= 20:
        raise ReviewError("Candidate limit must be an integer from 1 to 20")
    if not math.isfinite(minimum_text) or not 0.50 <= minimum_text <= 1:
        raise ReviewError("Minimum text similarity must be finite and between 0.50 and 1.00")
    old, new = old.expanduser().resolve(), new.expanduser().resolve()
    old_hash, new_hash = sha256(old), sha256(new)
    with open_pdf(old) as source, open_pdf(new) as target, staging_directory(output) as stage:
        pairs = source_annotations(source, old_hash)
        matcher = Matcher(target, minimum_text=minimum_text)
        page_images = {}
        for item, anchor in pairs:
            page = source[item.page_index]
            if item.page_index not in page_images:
                page_images[item.page_index] = render_asset(
                    page, stage, f"old-page-{item.page_index + 1}.png"
                )
            item.page_image = page_images[item.page_index]
            item.image = render_asset(
                page, stage, f"{item.id}-old.png", rect=pymupdf.Rect(item.source_rect)
            )
            if not item.supported:
                continue
            item.candidates, item.candidate_count = matcher.candidates(anchor, limit=candidates)
            if item.candidate_count > candidates:
                item.warnings.append(
                    f"Showing the top {candidates} of {item.candidate_count} candidates; none is preselected."
                )
            if (
                item.candidates
                and item.candidates[0].evidence.method == "fuzzy"
                and any(c.evidence.method == "normalized-exact" for c in item.candidates)
            ):
                item.warnings.append(
                    "A fuzzy candidate ranks above an exact quote. Inspect both: changed context or pagination can lower an exact match's score."
                )
            if (
                len(item.candidates) > 1
                and item.candidates[0].score - item.candidates[1].score < 0.05
            ):
                item.warnings.append(
                    "Several candidates have similar scores. Compare context and images carefully."
                )
            for candidate in item.candidates:
                candidate.image = render_asset(
                    target[candidate.page_index],
                    stage,
                    f"{item.id}-{candidate.id}.png",
                    rect=quads_rect(candidate.quads),
                    quads=candidate.quads,
                )
        proposal = Proposal(
            tool_version=__version__,
            created_utc=datetime.now(UTC).isoformat(),
            old_pdf=InputPDF(path=str(old), sha256=old_hash, page_count=len(source)),
            new_pdf=InputPDF(path=str(new), sha256=new_hash, page_count=len(target)),
            matching={
                "minimum_text_similarity": minimum_text,
                "candidate_limit": candidates,
                "score_weights": "pdfanno 0.2.2: text .40, context .30, layout .15, page .10, length .05",
                "normalization": "NFKC, casefold, whitespace, punctuation, line-end dehyphenation",
                "geometry": "MuPDF glyph quads; multi-line spans kept together",
            },
            annotations=[item for item, _ in pairs],
            warnings=[
                "All candidates require an explicit human choice. Scores are ranking signals, not probabilities.",
                "Only embedded review annotations are read; links, popups and form widgets are structural objects.",
                "Unmatched output includes the full original PDF, which may contain other sensitive material.",
            ],
        )
        if not pairs:
            proposal.warnings.insert(
                0,
                "No embedded review annotations found. Export a PDF with annotations from your reader or annotation database.",
            )
        if not any(matcher.page_texts):
            proposal.warnings.insert(
                0,
                "The new PDF has no extractable text. This tool does not run OCR; preserve unmatched comments or supply a PDF with a text layer.",
            )
        proposal.proposal_id = proposal.fingerprint()
        write_json(stage / "proposal.json", proposal.model_dump())
        write_json(
            stage / "decisions.template.json",
            Decisions(proposal_id=proposal.proposal_id).model_dump(),
        )
        write_review(stage / "review.html", proposal)
        if sha256(old) != old_hash or sha256(new) != new_hash:
            raise ReviewError("An input PDF changed while generating the proposal; run again")
    return proposal


def load_proposal(bundle: Path) -> Proposal:
    proposal = Proposal.model_validate(read_json(bundle / "proposal.json"))
    if proposal.proposal_id != proposal.fingerprint():
        raise ReviewError("Proposal fingerprint mismatch; regenerate the bundle")
    ids = [item.id for item in proposal.annotations]
    if len(ids) != len(set(ids)):
        raise ReviewError("Duplicate source annotation ids in proposal")
    return proposal


def verify_asset(bundle: Path, asset: Asset | None) -> Path | None:
    if asset is None:
        return None
    if (bundle / "assets").is_symlink():
        raise ReviewError("Evidence assets directory must not be a symbolic link")
    path = (bundle / asset.path).resolve()
    if not path.is_relative_to((bundle / "assets").resolve()):
        raise ReviewError("Evidence asset escapes the bundle's assets directory")
    if sha256(path) != asset.sha256:
        raise ReviewError(f"Evidence image changed: {asset.path}")
    return path


def _validate_geometry(page: pymupdf.Page, candidate) -> None:
    page_bounds = bounds(page)
    for coords in candidate.quads:
        q = quad_from_list(coords)
        if (
            not q.is_convex
            or q.is_empty
            or not all(p in page_bounds + (-1, -1, 1, 1) for p in (q.ul, q.ur, q.ll, q.lr))
        ):
            raise ReviewError("Accepted candidate has invalid or out-of-page geometry")
    index = TextIndex(page)
    span = index.selected_range(candidate.quads)
    if span is None or normalize(index.text[slice(*span)]) != normalize(candidate.matched_text):
        raise ReviewError("Accepted candidate geometry does not select its recorded text")


def _migrate(source: pymupdf.Annot, page: pymupdf.Page, candidate, marker: str) -> pymupdf.Annot:
    source_doc = source.parent.parent
    if source_doc.xref_get_key(source.xref, "IRT")[0] != "null":
        raise ReviewError("Reply/group relationships must be preserved with the original PDF")
    rich_type, rich_value = source_doc.xref_get_key(source.xref, "RC")
    if rich_type != "null" and rich_value and not source.info.get("content", "").strip():
        raise ReviewError("Rich-text-only comment cannot be migrated as an empty plain comment")
    kind = source.type[1].lower()
    quads = [quad_from_list(q) for q in candidate.quads]
    adders = {
        "highlight": page.add_highlight_annot,
        "underline": page.add_underline_annot,
        "strikeout": page.add_strikeout_annot,
        "squiggly": page.add_squiggly_annot,
    }
    if kind in adders:
        annot = adders[kind](quads)
    elif kind == "text":
        rect = quads[0].rect
        limits = bounds(page)
        point = (
            max(limits.x0 + 1, min(rect.x1 + 5, limits.x1 - 20)),
            max(limits.y0 + 1, min(rect.y0, limits.y1 - 20)),
        )
        annot = page.add_text_annot(
            point, source.info.get("content", ""), icon=source.info.get("name") or "Note"
        )
    else:
        raise ReviewError(f"Unsupported annotation cannot be accepted: {kind}")
    info = source.info
    annot.set_info(
        **{
            k: info[k]
            for k in ["content", "title", "subject", "creationDate", "modDate"]
            if info.get(k)
        }
    )
    colors = {k: v for k, v in source.colors.items() if v}
    if colors:
        annot.set_colors(colors)
    if 0 <= source.opacity <= 1:
        annot.set_opacity(source.opacity)
    annot.set_flags(source.flags)
    page.parent.xref_set_key(annot.xref, "NM", pymupdf.get_pdf_str(marker))
    annot.update()
    return annot


def has_signature_fields(doc: pymupdf.Document) -> bool:
    # SigFlags is optional. Check actual widgets and certification entries too.
    if doc.get_sigflags() > 0:
        return True
    if doc.xref_get_key(doc.pdf_catalog(), "Perms")[0] != "null":
        return True
    return any(
        widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE
        for page in doc
        for widget in page.widgets() or []
    )


def apply(
    bundle: Path,
    decision_path: Path,
    output: Path,
    *,
    old: Path | None = None,
    new: Path | None = None,
) -> dict:
    bundle = bundle.expanduser().resolve()
    proposal = load_proposal(bundle)
    decision_bytes = read_contract_bytes(decision_path)
    decision_digest = hashlib.sha256(decision_bytes).hexdigest()
    decisions = Decisions.model_validate_json(decision_bytes)
    if not decisions.confirmed:
        raise ReviewError(
            "Decisions are not confirmed; review choices and explicitly confirm before exporting"
        )
    if decisions.proposal_id != proposal.proposal_id:
        raise ReviewError("Decisions belong to a different proposal")
    by_id = {item.id: item for item in proposal.annotations}
    choices = {choice.annotation_id: choice for choice in decisions.choices}
    if unknown := choices.keys() - by_id.keys():
        raise ReviewError(f"Unknown annotation decision: {sorted(unknown)[0]}")
    old = (old or Path(proposal.old_pdf.path)).expanduser().resolve()
    new = (new or Path(proposal.new_pdf.path)).expanduser().resolve()
    for path, expected in [(old, proposal.old_pdf), (new, proposal.new_pdf)]:
        if sha256(path) != expected.sha256:
            raise ReviewError(f"Input PDF bytes changed since review: {path}")
    migrated, unmatched = [], []
    with open_pdf(old) as source, open_pdf(new) as target, staging_directory(output) as stage:
        if has_signature_fields(target):
            raise ReviewError(
                "The new PDF has signature fields, flags or certification entries; use a separate unsigned revision"
            )
        for item in proposal.annotations:
            choice = choices.get(item.id)
            verify_asset(bundle, item.image)
            verify_asset(bundle, item.page_image)
            if choice is None or choice.action != "accept":
                reason = (
                    "unsupported"
                    if not item.supported
                    else "no_candidates"
                    if not item.candidates
                    else "rejected"
                    if choice and choice.action == "reject"
                    else "unresolved"
                )
                unmatched.append(
                    {
                        "annotation": item.model_dump(),
                        "reason": reason,
                        "review_note": choice.note if choice else "",
                    }
                )
                continue
            if not item.supported:
                raise ReviewError(f"Unsupported annotation cannot be accepted: {item.id}")
            matches = [c for c in item.candidates if c.id == choice.candidate_id]
            if len(matches) != 1:
                raise ReviewError(f"Unknown or duplicate candidate for annotation: {item.id}")
            candidate = matches[0]
            verify_asset(bundle, candidate.image)
            if item.page_index >= len(source) or candidate.page_index >= len(target):
                raise ReviewError("Annotation page is out of range")
            source_page = source[item.page_index]
            source_annot = source_page.load_annot(item.source_xref)
            if source_annot.type[1].lower() != item.kind:
                raise ReviewError("Source annotation kind no longer matches proposal")
            page = target[candidate.page_index]
            _validate_geometry(page, candidate)
            marker = (
                "pdf-reanchor:"
                + hashlib.sha256(
                    f"{proposal.proposal_id}:{item.id}:{candidate.id}".encode()
                ).hexdigest()[:32]
            )
            annotation = _migrate(source_annot, page, candidate, marker)
            migrated.append(
                {
                    "annotation_id": item.id,
                    "candidate_id": candidate.id,
                    "page_index": candidate.page_index,
                    "new_name": marker,
                    "new_xref": annotation.xref,
                    "old_page_index": item.page_index,
                    "old_xref": item.source_xref,
                    "old_name": source_annot.info.get("id", ""),
                    "source_info": source_annot.info,
                    "matched_text": candidate.matched_text,
                    "evidence": candidate.evidence.model_dump(),
                    "review_note": choice.note,
                }
            )
        target.save(stage / "migrated.pdf", garbage=3, deflate=True)
        # Reopen the actual saved file and require each accepted migration to exist.
        with pymupdf.open(stage / "migrated.pdf") as saved:
            names = {a.info.get("id"): a.xref for page in saved for a in page.annots() or []}
            if any(row["new_name"] not in names for row in migrated):
                raise ReviewError(
                    "Saved PDF verification failed: a confirmed annotation is missing"
                )
            for row in migrated:
                # Garbage collection can renumber PDF xrefs during save.
                row["new_xref"] = names[row["new_name"]]
        audit = {
            "schema_version": 1,
            "proposal_id": proposal.proposal_id,
            "created_utc": datetime.now(UTC).isoformat(),
            "old_sha256": proposal.old_pdf.sha256,
            "new_sha256": proposal.new_pdf.sha256,
            "decisions_sha256": decision_digest,
            "migrated_sha256": sha256(stage / "migrated.pdf"),
            "total": len(proposal.annotations),
            "migrated_count": len(migrated),
            "unmatched_count": len(unmatched),
            "migrated": migrated,
            "unmatched_ids": [r["annotation"]["id"] for r in unmatched],
            "unmatched_package_contains_full_original": bool(unmatched),
        }
        write_json(stage / "audit.json", audit)
        write_json(
            stage / "unmatched.json",
            {
                "schema_version": 1,
                "source_sha256": proposal.old_pdf.sha256,
                "annotations": unmatched,
            },
        )
        with zipfile.ZipFile(
            stage / "unmatched.zip", "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.write(stage / "unmatched.json", "unmatched.json")
            archive.writestr(
                "README.txt",
                "Unmatched comments were not written to the revised PDF.\nOriginal PDF bytes are included when any comments remain; inspect using a PDF reader.\nIt may include other sensitive content. Source page numbers in JSON are zero-based.\n",
            )
            if unmatched:
                archive.write(old, "original.pdf")
                copied = set()
                for row in unmatched:
                    item = by_id[row["annotation"]["id"]]
                    for asset in [item.image, item.page_image, *[c.image for c in item.candidates]]:
                        if asset and asset.path not in copied:
                            archive.write(verify_asset(bundle, asset), asset.path)
                            copied.add(asset.path)
        if sha256(old) != proposal.old_pdf.sha256 or sha256(new) != proposal.new_pdf.sha256:
            raise ReviewError("An input PDF changed during migration; no output committed")
        if sha256(decision_path) != decision_digest:
            raise ReviewError("Decisions changed during migration; no output committed")
    return audit
