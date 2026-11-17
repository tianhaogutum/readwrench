"""``readwrench check`` adapter for ``readwrench reanchor`` review bundles.

A ``readwrench reanchor propose`` bundle is bound to the exact bytes of the two PDFs
and of every evidence image by SHA-256, and ``proposal.json`` carries its own
fingerprint. ``readwrench reanchor apply`` refuses a bundle when any of these no longer
match. This check runs the same verification read-only, so a stale or edited bundle
is found before someone spends time reviewing it.

Only the standard library and :mod:`readwrench.check.model` are imported here; the
proposal schema (pydantic) is imported inside the checker after :func:`need`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from readwrench.check.model import (
    ERROR,
    INFO,
    PDF,
    WARNING,
    Check,
    Finding,
    Source,
    need,
    plural,
    short,
)

KIND = "reanchor-proposal"
CHECK = "reanchor"
# The same limit ``readwrench reanchor apply`` enforces for review-contract JSON.
CONTRACT_LIMIT = 64 * 1024 * 1024


def classify(path: Path, head: bytes) -> tuple[str, str] | None:
    """Recognise a ``proposal.json`` written by ``readwrench reanchor propose``."""
    try:
        if not head.lstrip()[:1] == b"{":
            return None
        if b'"upstream_version"' not in head or b'"proposal_id"' not in head:
            return None
        if b'"old_pdf"' not in head and b'"annotations"' not in head:
            return None
        return KIND, "PDF reanchor review proposal"
    except Exception:  # classify must never raise
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_proposal(source: Source):
    need("pydantic")
    from pydantic import ValidationError

    from .models import Proposal

    def finding(rule, severity, message, *, pointer=None, subject=None, hint=None):
        return Finding(CHECK, rule, severity, message, file=source.display, pointer=pointer,
                       subject=subject, hint=hint)

    bundle_path = source.path.parent
    bundle = str(Path(source.display).parent)
    regenerate = "readwrench reanchor propose OLD.pdf NEW.pdf --out NEW_BUNDLE"
    try:
        size = source.path.stat().st_size
        if size > CONTRACT_LIMIT:
            yield finding("invalid-proposal", ERROR,
                          "proposal.json exceeds the 64 MiB review-contract limit")
            return
        proposal = Proposal.model_validate(json.loads(source.path.read_bytes()))
    except (OSError, ValueError, ValidationError) as exc:
        # json.JSONDecodeError and UnicodeDecodeError are ValueErrors.
        yield finding("invalid-proposal", ERROR,
                      f"Not a valid reanchor proposal: {short(exc, 200)}", hint=regenerate)
        return

    clean = True
    if proposal.proposal_id != proposal.fingerprint():
        clean = False
        yield finding("fingerprint-mismatch", ERROR,
                      "proposal.json was edited after it was created; apply will refuse it",
                      pointer="/proposal_id", hint=regenerate)
    ids = [item.id for item in proposal.annotations]
    if len(ids) != len(set(ids)):
        clean = False
        yield finding("invalid-proposal", ERROR, "Duplicate source annotation ids",
                      pointer="/annotations", hint=regenerate)

    for key, label, flag in (("old_pdf", "original", "--old-pdf"),
                             ("new_pdf", "revision", "--new-pdf")):
        recorded = getattr(proposal, key)
        path = Path(recorded.path)
        name = path.name
        if not path.is_file():
            clean = False
            yield finding("pdf-missing", WARNING,
                          f"The {label} PDF is no longer at the path recorded in the proposal",
                          pointer=f"/{key}/path", subject=name,
                          hint=(f"readwrench reanchor apply {bundle} DECISIONS.json --out RESULT "
                                f"{flag} PATH (the file must be byte-identical)"))
            continue
        if _sha256(path) != recorded.sha256:
            clean = False
            yield finding("pdf-changed", ERROR,
                          f"The {label} PDF changed since the proposal was made; "
                          "apply will refuse this bundle",
                          pointer=f"/{key}/sha256", subject=name, hint=regenerate)

    assets_dir = bundle_path / "assets"
    if assets_dir.is_symlink():
        clean = False
        yield finding("evidence-changed", ERROR,
                      "The evidence assets directory is a symbolic link; apply will refuse it",
                      pointer="/annotations", hint=regenerate)
    else:
        root = assets_dir.resolve()
        seen: set[str] = set()
        for i, item in enumerate(proposal.annotations):
            assets = [(f"/annotations/{i}/image", item.image),
                      (f"/annotations/{i}/page_image", item.page_image)]
            assets += [(f"/annotations/{i}/candidates/{j}/image", c.image)
                       for j, c in enumerate(item.candidates)]
            for pointer, asset in assets:
                if asset is None or asset.path in seen:
                    continue
                seen.add(asset.path)
                path = (bundle_path / asset.path).resolve()
                if not path.is_relative_to(root):
                    problem = "points outside the bundle's assets directory"
                elif not path.is_file():
                    problem = "is missing"
                elif _sha256(path) != asset.sha256:
                    problem = "changed since the proposal was made"
                else:
                    continue
                clean = False
                yield finding("evidence-changed", ERROR,
                              f"Evidence image {problem}; apply will refuse this bundle",
                              pointer=pointer, subject=asset.path, hint=regenerate)

    total = len(proposal.annotations)
    if not clean:
        return
    if total:
        with_candidates = sum(bool(item.candidates) for item in proposal.annotations)
        unsupported = sum(not item.supported for item in proposal.annotations)
        message = (f"Review bundle with {plural(total, 'annotation')}: "
                   f"{with_candidates} with candidates, {unsupported} unsupported "
                   "(kept unmatched with the original PDF). Nothing is migrated until "
                   "choices are confirmed in review.html")
        yield finding("review-pending", INFO, message,
                      hint=f"open {bundle}/review.html, then readwrench reanchor apply "
                           f"{bundle} DECISIONS.json --out RESULT")
    else:
        yield finding("no-annotations", INFO,
                      "The original PDF had no embedded review annotations; "
                      "nothing to migrate")


CHECKS = (
    Check(
        name=CHECK,
        area=PDF,
        summary="Re-verify reanchor review bundles against their PDFs and evidence images",
        kinds=frozenset({KIND}),
        run=check_proposal,
        rules=("invalid-proposal", "fingerprint-mismatch", "pdf-missing", "pdf-changed",
               "evidence-changed", "review-pending", "no-annotations"),
        requires=("pydantic",),
        extra="reanchor",
        tool="reanchor",
        note="Checks the bundle's hashes and fingerprint only; it does not judge candidate "
             "quality or re-run matching, and decisions files are not inspected.",
    ),
)
