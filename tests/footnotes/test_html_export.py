"""Exercise generated export JavaScript with Node's VM, without a browser."""

from __future__ import annotations

import pytest

pytest.importorskip("lxml")
pytest.importorskip("ebooklib")

import json
import os
import shutil
import subprocess

from lxml import etree

from readwrench.footnotes.audit import audit
from readwrench.footnotes.epub import Book
from readwrench.footnotes.html import preview_html, review_html
from readwrench.footnotes.patch import make_patch
from tests.footnotes.books import DEFAULT_CHAPTER, approved, make_book

NODE_HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let handler, blob, downloads = 0, filename;
const status = {textContent:''};
const rows = input.rows.map((row, index) => ({
  dataset:{index:String(index)}, values:row,
  querySelector(selector){
    if(selector === '.approved') return {checked:this.values.approved};
    if(selector === '.action') return {value:this.values.action};
    if(selector === '.label') return {value:this.values.label};
    throw new Error('Unexpected selector: '+selector);
  }
}));
const context = {Blob, marker:0,
  URL:{createObjectURL(value){blob=value;return 'blob:local-test';},revokeObjectURL(){}},
  setTimeout(callback){callback();return 0;},
  document:{
    getElementById(id){
      if(id === 'plan') return {textContent:input.plan};
      if(id === 'export') return {addEventListener(event, callback){if(event !== 'click') throw new Error('wrong event'); handler=callback;}};
      if(id === 'saved') return status;
      throw new Error('Unexpected ID: '+id);
    },
    querySelectorAll(){return rows;},
    createElement(tag){
      if(tag !== 'a') throw new Error('Unexpected tag: '+tag);
      return {click(){downloads++;filename=this.download;},remove(){}};
    },
    body:{append(){}}
  }
};
vm.runInNewContext(input.script, context, {timeout:2000});
handler();
blob.text().then(text => process.stdout.write(JSON.stringify({
  result:JSON.parse(text),status:status.textContent,filename,downloads,type:blob.type,marker:context.marker
})));
"""


def test_exported_decisions_preserve_source_binding_and_individual_choices(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable; no browser engine is installed for this test")
    chapter = DEFAULT_CHAPTER * 3
    source = make_book(tmp_path / "source.epub", {"Text/chapter.xhtml": chapter})
    with Book(source) as book:
        plan = audit(book).plan
    plan["references"][0]["context"] = '</script><script>globalThis.marker=1</script>'
    tree = etree.HTML(review_html(plan))
    scripts = tree.xpath("//script")
    payload = {"plan": scripts[0].text, "script": scripts[1].text,
               "rows": [{"approved": True, "action": "add_backlink", "label": '返回 1 <& "literal"'},
                        {"approved": True, "action": "skip", "label": "Skip label"},
                        {"approved": False, "action": "keep", "label": "Unapproved label"}]}
    environment = {**os.environ, "NODE_OPTIONS": "--max-old-space-size=1024"}
    result = subprocess.run([node, "-e", NODE_HARNESS], input=json.dumps(payload), capture_output=True,
                            text=True, env=environment, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    exported = actual["result"]
    assert exported["book_sha256"] == plan["book_sha256"]
    assert exported["scan_options"] == plan["scan_options"]
    assert [r["approved"] for r in exported["references"]] == [True, True, False]
    assert exported["references"][0]["backlink_label"] == '返回 1 <& "literal"'
    assert exported["references"][0]["context"] == plan["references"][0]["context"]
    assert actual["marker"] == 0
    assert actual["filename"] == "decisions.json"
    assert actual["downloads"] == 1
    assert actual["type"] == "application/json"
    assert "Exported 1 approved references" in actual["status"]


def test_every_preview_and_review_fragment_link_has_a_unique_target(tmp_path):
    source = make_book(tmp_path / "source.epub", {"Text/chapter.xhtml": DEFAULT_CHAPTER * 2})
    with Book(source) as book:
        fresh = audit(book)
        patch = make_patch(book, fresh, approved(fresh.plan))
    for document in (review_html(fresh.plan), preview_html(patch.manifest, fresh.plan)):
        tree = etree.HTML(document)
        ids = tree.xpath('//@id')
        assert len(ids) == len(set(ids))
        for link in tree.xpath('//a[starts-with(@href,"#")]'):
            assert link.get("href")[1:] in ids
        for label in tree.xpath('//label[@for]'):
            assert label.get("for") in ids
        assert not tree.xpath('//script[@src] | //iframe | //object | //embed | //img')
