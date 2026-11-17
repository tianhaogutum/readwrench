# Font fixtures for `readwrench subset-proof` tests

Downloaded 2026-11-11 from the upstream repositories below. Every entry except the
derived CID fixture is an unmodified upstream binary. `sources.json` records exact byte
sizes, internal version strings and SHA-256 hashes; the source URLs name download
branches rather than commit pins, and the recorded hashes pin the test inputs.

| File | Upstream source | License (next to the font) | Purpose |
| --- | --- | --- | --- |
| NotoSans.ttf | https://github.com/google/fonts/tree/main/ofl/notosans | OFL-1.1, `NotoSans-OFL.txt` | Variable TrueType, ligatures, kerning, accents, compressed-font round trips |
| NotoSansArabic.ttf | https://github.com/google/fonts/tree/main/ofl/notosansarabic | OFL-1.1, `NotoSansArabic-OFL.txt` | Joining, RTL shaping, mark positioning, collections |
| NotoSansDevanagari.ttf | https://github.com/google/fonts/tree/main/ofl/notosansdevanagari | OFL-1.1, `NotoSansDevanagari-OFL.txt` | Indic reordering and conjunct closure |
| SourceSerif4-Regular.otf | https://github.com/adobe-fonts/source-serif/tree/release/OTF | OFL-1.1 with Reserved Font Name "Source", `SourceSerif4-LICENSE.md` | CFF outlines, ligatures, publication features |
| SubsetProofCJKFixture.otf | https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese | OFL-1.1, `NotoCJK-LICENSE.txt` | **Derived and renamed** CID-keyed CFF fixture: Chinese copy, vertical alternates and metrics |

Generated subsets and deliberately damaged font variants are made at test time inside
temporary test directories. They are test mutations of the listed upstream binaries, not
upstream releases.

The CID fixture is the one modified font: it was derived from the 16,437,364-byte Noto
Sans CJK SC 2.004 OTF with fontTools (subset to the copy in
`examples/subset-proof/chinese.txt` plus vertical punctuation, all layout features
kept) and renamed "Subset Proof CJK Fixture". `make_cid_fixture.py` reproduces it from
a downloaded copy of the upstream OTF; the source hash and transformation are recorded
in `sources.json`. The retained fixture is 20,304 bytes and 110 glyphs.

Fonts used by the former standalone tool's tests but not shipped here (to keep the
repository small): ZCOOL XiaoWei (6.3 MB; its Chinese poster test now uses the CID
fixture), Noto Emoji (2 MB; ZWJ and skin-tone cluster test) and Source Serif 4 Variable
(1.9 MB; CFF2 variable-location test).
