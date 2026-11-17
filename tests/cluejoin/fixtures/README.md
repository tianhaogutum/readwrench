# Fixture provenance (`readwrench clues` tests)

`rfc2616.pdf` is the RFC Editor's actual RFC 2616 PDF, downloaded on 2026-11-11 from
<https://www.rfc-editor.org/rfc/rfc2616.pdf> (550,558 bytes, 114 physical pages,
SHA-256 `4b992042e3a18f1bb8cebbd0916c66b89b4ab179d75d552a85f48f83a222e631`). It is kept
unmodified as a real retrieval-format fixture. It carries its own copyright statement
(section 20, "Full Copyright Statement"), which permits copying the unmodified document;
see `licenses/RFC2616-ISOC-copyright.txt`. The package license does not replace the
RFC's own terms. RFC 2616 is obsolete as HTTP guidance.

`rfc2616.txt` is its Poppler 26.09.0 text extraction with the default form-feed page
separators (`pdftotext rfc2616.pdf rfc2616.txt`; SHA-256
`ff15e0154854b9acc501a6604a6199093a6d5c13600eadbf87f1396d3d10205c`).
`rfc2616-layout-crlf.txt` is a second real Poppler variant
(`pdftotext -layout -eol dos`) with physical layout and CRLF line endings.

`rga-rfc2616.jsonl` is actual output of ripgrep-all 0.10.10 (ripgrep 15.2.0, Poppler
26.09.0), not handcrafted JSON:

```sh
rga --rga-config-file=rga-config.json --rga-cache-path=rga-cache --no-config \
  --threads 1 --json -i -e idempotent -e semantically fixtures/upstream/rfc2616.pdf
```

The config was `{}`. The recorded source path (`fixtures/upstream/rfc2616.pdf`) is the
relative path used when it was captured; tests assert it verbatim.
`rga-rfc2616-context.jsonl` came from the same release with
`--json -n -C 2 -i -e 'a cache behaves' -e 'non-idempotent sequences'`; it covers
`context` events and extracted-stream line numbers (which refer to the rga-adapted
stream, not to PDF layout lines or the separate Poppler output). Upstream timing fields
are incidental and not used as expectations.

The small examples (`examples/clues/`) and inline test data are synthetic.
