"""Derive a small, renamed CID CJK fixture from the documented upstream OTF.

Usage: python tests/subsetproof/fixtures/make_cid_fixture.py NotoSansCJKsc-Regular.otf
Needs fontTools. Download URL and original hash are recorded in sources.json next to
this script. Not a test module; pytest does not collect it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("--out", type=Path, default=HERE / "fonts/SubsetProofCJKFixture.otf")
args = parser.parse_args()
font = TTFont(args.source, recalcTimestamp=False)
upstream_version = font["name"].getDebugName(5)
options = subset.Options()
options.layout_features = ["*"]
options.glyph_names = True
options.ignore_missing_unicodes = False
options.name_IDs = ["*"]
options.name_languages = ["*"]
worker = subset.Subsetter(options)
copy = (ROOT / "examples/subset-proof/chinese.txt").read_text().replace("\n", "") + "「」（）！？、。"
worker.populate(text=copy)
worker.subset(font)
family, postscript = "Subset Proof CJK Fixture", "SubsetProofCJKFixture-Regular"
names = {1: family, 2: "Regular", 3: postscript, 4: family + " Regular", 6: postscript, 16: family, 17: "Regular"}
for record in list(font["name"].names):
    if record.nameID in names:
        font["name"].setName(names[record.nameID], record.nameID, record.platformID, record.platEncID, record.langID)
cff = font["CFF "].cff
cff.fontNames[0] = postscript
top = cff.topDictIndex[0]
top.FullName, top.FamilyName = family + " Regular", family
for index, dictionary in enumerate(top.FDArray):
    dictionary.FontName = f"{postscript}-{index}"
font.save(args.out)
font.close()
record = {"file": str(args.out.resolve().relative_to(HERE)),
          "url": "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
          "version": upstream_version, "derived": True,
          "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
          "source_bytes": args.source.stat().st_size,
          "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(), "bytes": args.out.stat().st_size,
          "transform": "fontTools 4.68.0 subset on examples/subset-proof/chinese.txt and vertical punctuation; all features; renamed by tests/subsetproof/fixtures/make_cid_fixture.py"}
print(json.dumps(record, indent=2))
