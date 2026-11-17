"""Small generated PDFs for the ``readwrench pdf-pages`` suite (needs ReportLab)."""


def write_pdf(path, texts, *, size=(400, 550)):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=size, invariant=1)
    for text in texts:
        c.setFont("Helvetica", 14)
        c.drawString(30, size[1] - 50, text)
        c.showPage()
    c.save()
    return path
