"""``readwrench pdf-pages``: a page-correspondence review adapter for Py-PDF-Compare.

Formerly the standalone ``pdf-page-bridge`` tool. Not a new PDF engine: it proposes a
page map, records a named human approval bound to both source files, and only then
exports a comparison. This module stays light; heavy dependencies load in the
submodules that need them.
"""
