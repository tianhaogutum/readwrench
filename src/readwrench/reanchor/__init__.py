"""``readwrench reanchor``: explicitly reviewed migration of embedded PDF annotations.

An extension of pdfanno 0.2.2 (formerly the standalone ``pdf-reanchor`` tool). This
module stays light: the proposal/apply workflow lives in
:mod:`readwrench.reanchor.workflow` and imports PyMuPDF, pdfanno, pydantic, RapidFuzz
and regex only when used.
"""
