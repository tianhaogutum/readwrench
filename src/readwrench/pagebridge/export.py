"""Version-pinned integration with the host engine and bounded raster evidence."""

import csv
import io
import json
from html import escape
from pathlib import Path

import pymupdf as fitz
from pdf_compare import PDFComparator
from PIL import Image, ImageChops
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .core import open_pdf, page_relation, render, utc_now, validate_map, verify_sources
from .mapping import RESULT_SCHEMA


def visual_difference(a, b, *, dpi=120, threshold=24, max_edge=1400):
    scale = min(dpi / 72, max_edge / max(a.rect.width, a.rect.height, b.rect.width, b.rect.height))
    image_a, image_b = render(a, scale=scale), render(b, scale=scale)
    size = max(image_a.width, image_b.width), max(image_a.height, image_b.height)
    canvas_a, canvas_b = Image.new("RGB", size, "white"), Image.new("RGB", size, "white")
    canvas_a.paste(image_a, (0, 0))
    canvas_b.paste(image_b, (0, 0))
    channels = ImageChops.difference(canvas_a, canvas_b).split()
    difference = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    mask = difference.point(lambda value: 255 if value > threshold else 0)
    changed_pixels = mask.histogram()[255]
    return (
        mask,
        image_a.size,
        image_b.size,
        {
            "changed_pixels": changed_pixels,
            "total_pixels": size[0] * size[1],
            "changed_ratio": round(changed_pixels / (size[0] * size[1]), 8),
            "effective_dpi": round(scale * 72, 2),
            "pixel_threshold": threshold,
            "geometry_changed": (a.rect.width, a.rect.height) != (b.rect.width, b.rect.height),
        },
    )


class ReviewedComparator(PDFComparator):
    """Delegate word diff/vector composition; supply an approved page ordering."""

    def __init__(self, original, modified, rows):
        super().__init__(str(original), str(modified))
        self.rows = rows

    def align_pages(self, text_a, text_b):
        events = []
        for row in self.rows:
            a, b = row["original"], row["modified"]
            if a is None:
                events.append(("insert", 0, 0, b - 1, b))
            elif b is None:
                events.append(("delete", a - 1, a, 0, 0))
            else:
                events.append(("replace", a - 1, a, b - 1, b))
        return events


def prepare_document(doc):
    # Work only in memory; generated reports include visible widgets/annotations.
    doc.bake(annots=True, widgets=True)
    for page in doc:
        if page.rotation:
            # In 1.26.7 show_pdf_page can offset a rotated cropped page. The
            # native remove_rotation API preserves vectors, but resets CropBox.
            # Normalize against the full media box, then restore the rotated
            # viewport in the new unrotated coordinate space.
            crop = fitz.Rect(page.cropbox)
            page.set_mediabox(fitz.Rect(page.mediabox))
            full = page.cropbox
            relative = crop - (full.x0, full.y0, full.x0, full.y0)
            viewport = relative * page.rotation_matrix
            page.remove_rotation()
            full = page.cropbox
            page.set_cropbox(viewport + (full.x0, full.y0, full.x0, full.y0))
        # Host show_pdf_page rejects truly empty pages. Give them a white paint operation.
        if not page.read_contents().strip():
            page.draw_rect(page.rect, color=None, fill=(1, 1, 1), overlay=False)


def add_mask(page, mask, size, rect):
    cropped = mask.crop((0, 0, size[0], size[1]))
    if not cropped.getbbox():
        return
    tint = Image.new("RGBA", cropped.size, (255, 144, 0, 0))
    tint.putalpha(cropped.point(lambda value: 80 if value else 0))
    stream = io.BytesIO()
    tint.save(stream, format="PNG")
    page.insert_image(rect, stream=stream.getvalue(), overlay=True)


def aligned_page(output, source, index, width, height):
    page = output.new_page(width=width, height=height)
    if index is not None:
        rect = source[index].rect
        page.show_pdf_page(fitz.Rect(0, 0, rect.width, rect.height), source, index)


def audit_pdf(data, summary):
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    # PyMuPDF already ships this cross-platform Unicode font. Embed a subset,
    # rather than silently replacing Chinese names/filenames with black squares.
    if "BridgeUnicode" not in pdfmetrics.getRegisteredFontNames():
        font_bytes = io.BytesIO(fitz.Font("cjk").buffer)
        pdfmetrics.registerFont(TTFont("BridgeUnicode", font_bytes))
    for style in styles.byName.values():
        if style.name != "Code":
            style.fontName = "BridgeUnicode"
    document = SimpleDocTemplate(
        stream,
        pagesize=(8.27 * inch, 11.69 * inch),
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    story = [
        Paragraph("readwrench pdf-pages", styles["Title"]),
        Paragraph("Approved correspondence and content comparison", styles["Heading2"]),
    ]
    for text in [
        f"Original: {data['sources']['original']['name']} ({data['sources']['original']['pages']} pages)",
        f"Revised: {data['sources']['modified']['name']} ({data['sources']['modified']['pages']} pages)",
        f"Reviewed by: {data['review']['reviewer']}",
        f"Generated: {summary['generated_at']}",
        "Matched pages preserve original and revised page labels. Red/green boxes show changed words "
        "using Py-PDF-Compare. Orange pixels show rendered differences on both panels. "
        "A shifted page number does not itself count as a content change.",
        "Thumbnail scores proposed correspondence only. A named reviewer approved the final map. "
        "Raster comparison is limited to the recorded resolution and threshold; it is not a proof "
        "of identical PDF internals, a signature check, or OCR.",
    ]:
        story.extend([Paragraph(escape(text), styles["BodyText"]), Spacer(1, 8)])
    c = summary["counts"]
    story.append(
        Paragraph(
            f"Matched: {c['matched']} | Inserted: {c['inserted']} | Deleted: {c['deleted']} | "
            f"Matched pairs with detected content/geometry changes: {c['changed']}",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 16))
    structure = summary.get("structure", {})
    story.append(
        Paragraph(
            f"Pairs crossing the original order: {structure.get('reordered_pairs', 0)} | "
            f"Other shifted page numbers: {structure.get('shifted_pairs', 0)}",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 10))
    if summary.get("report_mode") == "changed_only":
        story.append(
            Paragraph(
                "Unchanged matched panels are omitted; the complete page map is retained. "
                "Use PDF bookmarks or the report-page column to jump to detected changes.",
                styles["BodyText"],
            )
        )
        story.append(Spacer(1, 8))
    table_data = [
        ["Original", "Revised", "Page relation", "Content", "Raster pixels", "Report page"]
    ]
    for row in summary["rows"]:
        raster = row.get("visual")
        table_data.append(
            [
                str(row["original"] or "-"),
                str(row["modified"] or "-"),
                row.get("alignment", "-").replace("_", " "),
                row["kind"] if raster else "-",
                f"{raster['changed_ratio']:.2%}" if raster else "-",
                str(row.get("report_page") or "-"),
            ]
        )
    table = Table(table_data, repeatRows=1, colWidths=[50, 50, 95, 95, 100, 80])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173f59")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eff3f5")]),
                ("FONTNAME", (0, 0), (-1, -1), "BridgeUnicode"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 16))
    for side in ("original", "modified"):
        story.append(
            Paragraph(
                f"{side.capitalize()} SHA-256: {data['sources'][side]['sha256']}", styles["Code"]
            )
        )
    document.build(story)
    return stream.getvalue()


def export_comparison(
    data,
    output: Path,
    *,
    original=None,
    modified=None,
    dpi=120,
    threshold=24,
    aligned=False,
    changed_only=False,
):
    validate_map(data, approved=True)
    paths = verify_sources(data, original, modified)
    comparator = ReviewedComparator(paths["original"], paths["modified"], data["rows"])
    summary = {
        "schema": RESULT_SCHEMA,
        "generated_at": utc_now(),
        "review": data["review"],
        "sources": data["sources"],
        "rows": [],
        "counts": {"matched": 0, "inserted": 0, "deleted": 0, "changed": 0},
        "structure": {"reordered_pairs": 0, "shifted_pairs": 0},
        "report_mode": "changed_only" if changed_only else "all_pairs",
    }
    pairs = [(row["original"], row["modified"]) for row in data["rows"]]
    with (
        open_pdf(paths["original"]) as a,
        open_pdf(paths["modified"]) as b,
        fitz.open() as diff,
        fitz.open() as aligned_a,
        fitz.open() as aligned_b,
    ):
        prepare_document(a)
        prepare_document(b)
        for row in data["rows"]:
            old, new = row["original"], row["modified"]
            alignment = page_relation(old, new, pairs)
            result = {"original": old, "modified": new, "alignment": alignment}
            if alignment == "reordered":
                summary["structure"]["reordered_pairs"] += 1
            elif alignment == "shifted":
                summary["structure"]["shifted_pairs"] += 1
            if old is not None and new is not None:
                summary["counts"]["matched"] += 1
                changed = comparator._add_comparison_page(diff, a, b, old - 1, new - 1)
                mask, size_a, size_b, visual = visual_difference(
                    a[old - 1], b[new - 1], dpi=dpi, threshold=threshold
                )
                page = diff[-1]
                ra, rb = a[old - 1].rect, b[new - 1].rect
                add_mask(page, mask, size_a, fitz.Rect(20, 60, 20 + ra.width, 60 + ra.height))
                add_mask(
                    page,
                    mask,
                    size_b,
                    fitz.Rect(30 + ra.width, 60, 30 + ra.width + rb.width, 60 + rb.height),
                )
                result.update(
                    {
                        "kind": "changed"
                        if changed or visual["changed_pixels"] or visual["geometry_changed"]
                        else "unchanged",
                        "words_added": changed["added"],
                        "words_removed": changed["removed"],
                        "visual": visual,
                    }
                )
                if result["kind"] == "changed":
                    summary["counts"]["changed"] += 1
            elif old is not None:
                comparator._add_single_page(diff, a, old - 1, "left", "Missing")
                result["kind"] = "deleted"
                summary["counts"]["deleted"] += 1
            else:
                comparator._add_single_page(diff, b, new - 1, "right", "Added")
                result["kind"] = "inserted"
                summary["counts"]["inserted"] += 1
            include_panel = not changed_only or result["kind"] != "unchanged"
            if not include_panel:
                diff.delete_page(-1)
            result["report_page"] = len(diff) if include_panel else None
            summary["rows"].append(result)
            if aligned:
                rects = ([a[old - 1].rect] if old is not None else []) + (
                    [b[new - 1].rect] if new is not None else []
                )
                width, height = max(r.width for r in rects), max(r.height for r in rects)
                aligned_page(aligned_a, a, old - 1 if old else None, width, height)
                aligned_page(aligned_b, b, new - 1 if new else None, width, height)
            fitz.TOOLS.store_shrink(100)
        # Count the audit pages before assigning absolute panel destinations.
        with fitz.open(stream=audit_pdf(data, summary), filetype="pdf") as preliminary:
            summary["audit_pages"] = len(preliminary)
        for row in summary["rows"]:
            if row["report_page"] is not None:
                row["report_page"] += summary["audit_pages"]
        with fitz.open(stream=audit_pdf(data, summary), filetype="pdf") as audit:
            if len(audit) != summary["audit_pages"]:
                raise RuntimeError("Audit pagination changed while assigning report destinations")
            diff.insert_pdf(audit, start_at=0)
        bookmarks = [[1, "Audit and approved page map", 1]]
        for row in summary["rows"]:
            if row["kind"] != "unchanged" and row["report_page"] is not None:
                label = f"{row['kind'].capitalize()}: original {row['original'] or '-'} / revised {row['modified'] or '-'}"
                bookmarks.append([1, label, row["report_page"]])
        diff.set_toc(bookmarks)
        diff.set_metadata(
            {
                "title": "Approved PDF page comparison",
                "producer": "readwrench pdf-pages / Py-PDF-Compare",
            }
        )
        diff.save(output / "diff.pdf", deflate=True, garbage=3)
        if aligned:
            aligned_a.save(output / "aligned-original.pdf", deflate=True, garbage=3)
            aligned_b.save(output / "aligned-modified.pdf", deflate=True, garbage=3)
    # Catch files changed while their open document handles were being rendered.
    # The CLI's staging directory is discarded if this final check fails.
    verify_sources(data, original, modified)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output / "mapping.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (output / "mapping.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "original",
                "modified",
                "alignment",
                "kind",
                "words_added",
                "words_removed",
                "changed_ratio",
                "report_page",
            ],
        )
        writer.writeheader()
        for row in summary["rows"]:
            writer.writerow(
                {key: row.get(key, "") for key in writer.fieldnames if key != "changed_ratio"}
                | {"changed_ratio": row.get("visual", {}).get("changed_ratio", "")}
            )
    return summary
