from . import _deps  # noqa: F401,I001  (skips this module without the quote extra)
import socket
import urllib.request

from ._writers import write_epub, write_pdf

from readwrench.quotelocator import confirm, import_selector, load_document, locate, verify


def test_document_and_selector_workflows_run_with_network_access_disabled(tmp_path, monkeypatch):
    plain, html = tmp_path / "book.txt", tmp_path / "book.html"
    plain.write_text("A quotation.")
    html.write_text("<p>A quotation.</p>")
    pdf, epub = tmp_path / "paper.pdf", tmp_path / "book.epub"
    write_pdf(pdf, ["A quotation."])
    write_epub(epub)

    def deny(*args, **kwargs):
        raise AssertionError("Runtime attempted network access")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(urllib.request, "urlopen", deny)
    for path in [plain, html, pdf, epub]:
        quote = "A repeated quotation." if path.suffix == ".epub" else "A quotation."
        selector = import_selector(
            {
                "type": "Annotation",
                "target": {
                    "source": "https://invalid.example/must-not-be-fetched",
                    "selector": {"type": "TextQuoteSelector", "exact": quote},
                },
            }
        )
        doc = load_document(path)
        anchor = confirm(doc, locate(doc, selector), 1)
        assert verify(doc, anchor)["valid"]
