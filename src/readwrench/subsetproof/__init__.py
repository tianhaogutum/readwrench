"""``readwrench subset-proof``: strict manuscript proof for fontTools font subsets.

Font I/O, outlines and subsetting come from fontTools; shaping comes from HarfBuzz
(uharfbuzz). Formerly the standalone ``subset-proof`` tool. This module stays light:
the third-party dependencies are imported by ``proof``, ``core``, ``inputs`` and
``specimen`` only.
"""
