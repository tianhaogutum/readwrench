"""``readwrench lineage``: offline paragraph timelines for saved web-page captures.

Reads several local HTML, MHTML or WARC captures of one web page, extracts the article
text of each (a port of Mozilla Readability's content selection), aligns the paragraphs
between consecutive captures and writes a self-contained HTML report plus optional JSON.
Standard library only. Formerly the standalone ``page-lineage`` tool (Node.js).
"""
