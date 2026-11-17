"""Shared constants and builders for the ``readwrench lineage`` suite."""
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://example.org/tutorial"
ARTICLE_TEXTS = [
    "A reliable backup starts with a complete inventory of the files you cannot recreate. Keep that inventory "
    "separate from your primary storage and include the reason each group matters.",
    "Store a second copy on a different device and test a full restore before relying on your plan. A backup you "
    "have never restored remains an untested claim about future recovery.",
    "Encrypt private archives before copying them to a remote location. Keep recovery keys somewhere that remains "
    "available when the original computer is lost or damaged.",
]


def page(texts=ARTICLE_TEXTS) -> str:
    paragraphs = "".join(f"<p>{text}</p>" for text in texts)
    return ('<!doctype html><html><head><meta charset="utf-8"><title>A changing tutorial</title></head><body>'
            f"<nav>Navigation</nav><article>{paragraphs}</article></body></html>")


def block(text: str) -> dict:
    return {"kind": "p", "text": text}
