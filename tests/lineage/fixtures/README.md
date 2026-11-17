# Fixture provenance

These files are real upstream HTML/MHTML/WARC captures, not hand-written substitutes
for a browser archive format. Tests that change their content describe the result as a
controlled derivative, not a genuine historical capture. They are excluded from the
license of `readwrench lineage` itself.

- `nytimes.html`: [Mozilla Readability test/test-pages/nytimes-1/source.html](https://github.com/mozilla/readability/blob/ab4027a8b37669745016869a37a504727992b2ba/test/test-pages/nytimes-1/source.html),
  fetched 2026-11-11. Source is an archived 2017 NYT article, “United States to
  Lift Sudan Sanctions”. The Readability repository is Apache-2.0
  (`licenses/Readability-Apache-2.0.txt`); the article retains its original
  publisher copyright. Used only as a regression input. Its 2017
  article-modification metadata is **not** a capture timestamp.
- `portfolio.mhtml`: [msindwan/mhtml2html tests/templates/portfolio.mhtml](https://github.com/msindwan/mhtml2html/blob/a4eeea54cbb53d455f409428bcad65e5b6c8415d/tests/templates/portfolio.mhtml).
  Actual Chromium/Blink saved archive of `http://msindwan.bitbucket.org/`, MIME Date
  `Tue, 5 Sep 2016 22:30:04 -0000`. The weekday in that header is inconsistent with
  its calendar date; the MIME timestamp is a capture metadata **claim**, not
  independently verified evidence. Upstream license is MIT; see
  `mhtml2html-LICENSE.txt`.
- `portfolio.html`: a controlled **format conversion** of that same MHTML, generated
  with mhtml-to-html 2.2.0's Node CLI (scripts and fetching disabled) when the tool
  was still written in JavaScript. It is not a later historical capture; a regression
  test confirms both formats extract to identical article blocks.
- `example.warc.gz` and `iana-chunked.warc`: archive fixtures copied from
  [Webrecorder warcio.js test/data](https://github.com/webrecorder/warcio.js/tree/e0fec5a0b5832b07eb9248226c042ac8f4be04ad/test/data)
  on 2026-11-11 (the latter was named `example-iana.org-chunked.warc` upstream). They
  exercise archive gzip, HTTP gzip, HTTP chunking, revisits, record dates and server
  Last-Modified claims. The license text shipped with them is retained in
  `warcio-LICENSE`.

ArchiveBox JSON/JSONL and SingleFile manifest inputs in the test suite are generated
from their documented serializers/schemas inside each test; they were **not** produced
by running those capture tools.
