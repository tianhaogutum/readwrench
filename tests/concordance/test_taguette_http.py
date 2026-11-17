"""Exercise upstream Taguette handlers, not a replacement annotation UI.

Uses Tornado's short-lived loopback test server, stopped at teardown. The
upstream message poll is disabled (as in Taguette's own test harness), and all
SQLite export temporary files stay in one temporary directory removed at teardown.
No browser is used.
"""

from http.cookies import SimpleCookie
import csv
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from .helpers import EXAMPLES, GUTENBERG, ALL_DEPENDENCIES, require

require(*ALL_DEPENDENCIES, "tornado")

from taguette import main, web
from tornado.testing import AsyncHTTPTestCase

from readwrench.concordance.exporting import export_workspace
from readwrench.concordance.workspace import collect, initialize


class TestUpstreamTaguetteWorkflow(AsyncHTTPTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="taguette-http-")
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        initialize([GUTENBERG / "prince.epub"], EXAMPLES / "concepts.json", self.workspace)
        self.hit = collect(self.workspace)[2][0]
        self.message_patch = patch.object(web.Application, "check_messages", lambda app: app.messages_event.set())
        self.temp_patch = patch.object(tempfile, "tempdir", str(self.root))
        self.message_patch.start()
        self.temp_patch.start()
        super().setUp()

    def get_app(self):
        config = dict(main.DEFAULT_CONFIG, MULTIUSER=False,
                      NAME="Local integration test", PORT=0, DOMAIN="localhost",
                      SECRET_KEY="local-test-cookie-key", DATABASE="sqlite:///" + str(self.workspace / "project.sqlite3"),
                      TOS_FILE=None, COOKIES_PROMPT=False, EMAIL="unused@example.invalid", MAIL_SERVER={})
        self.application = web.make_app(config, xsrf_cookies=False)
        return self.application

    def tearDown(self):
        try:
            super().tearDown()
            self.application.DBSession.kw["bind"].dispose()
        finally:
            self.temp_patch.stop()
            self.message_patch.stop()
            self.temporary.cleanup()

    def login_headers(self):
        response = self.fetch("/?token=" + self.application.single_user_token, follow_redirects=False)
        assert response.code == 302
        cookie = SimpleCookie()
        for value in response.headers.get_list("Set-Cookie"):
            cookie.load(value)
        headers = {"Cookie": "; ".join(f"{name}={morsel.coded_value}" for name, morsel in cookie.items())}
        return headers

    def test_native_login_tagging_and_sqlite_export_remain_resolvable(self):
        headers = self.login_headers()
        page = self.fetch("/project/1", headers=headers)
        assert page.code == 200
        assert b"query/power" in page.body
        document_id = self.hit["taguette_document_id"]
        contents = self.fetch(f"/api/project/1/document/{document_id}/contents", headers=headers)
        assert contents.code == 200
        assert "contents" in json.loads(contents.body)
        post_headers = dict(headers, **{"Content-Type": "application/json"})
        tag = self.fetch("/api/project/1/tag/new", method="POST", headers=post_headers,
                         body=json.dumps({"path": "sense/upstream-manual-code", "description": "Created through the actual Taguette API"}))
        assert tag.code == 200
        tag_id = json.loads(tag.body)["id"]
        with self.application.DBSession() as db:
            from taguette import database as d
            query_tag_id = db.query(d.Tag).filter_by(path="query/" + self.hit["concept_id"]).one().id
        update = self.fetch(f'/api/project/1/document/{document_id}/highlight/{self.hit["taguette_highlight_id"]}',
                            method="POST", headers=post_headers, body=json.dumps({"tags": [query_tag_id, tag_id]}))
        assert update.code == 200
        assert collect(self.workspace)[2][0]["senses"] == ["upstream-manual-code"]
        chapter_tag = self.fetch("/api/project/1/tag/new", method="POST", headers=post_headers,
                                 body=json.dumps({"path": "chapter/Researcher chapter", "description": "Explicit source range"}))
        assert chapter_tag.code == 200
        text = collect(self.workspace)[1][self.hit["chapter_id"]]
        chapter_highlight = self.fetch(f"/api/project/1/document/{document_id}/highlight/new", method="POST", headers=post_headers,
                                       body=json.dumps({"start_offset": 0, "end_offset": len(text.encode("utf-8")),
                                                        "tags": [json.loads(chapter_tag.body)["id"]]}))
        assert chapter_highlight.code == 200
        assert collect(self.workspace)[2][0]["chapter"] == "Researcher chapter"
        exported_db = self.fetch("/project/1/export/project.sqlite3", headers=headers)
        assert exported_db.code == 200
        exported_path = self.root / "native-export.sqlite3"
        exported_path.write_bytes(exported_db.body)
        hits = collect(self.workspace, database=exported_path)[2]
        assert hits[0]["id"] == self.hit["id"]
        assert hits[0]["senses"] == ["upstream-manual-code"]
        assert hits[0]["chapter_source"] == "manual_range"
        out = self.root / "matrix"
        summary = export_workspace(self.workspace, out, database=exported_path)
        assert summary["status_counts"]["classified"] == 1
        codebook = json.loads((out / "codebook.json").read_text())
        assert next(t for t in codebook if t["path"] == "sense/upstream-manual-code")["description"] == "Created through the actual Taguette API"

    def test_resized_quote_uses_source_offsets_despite_upstream_stale_snippet(self):
        headers = self.login_headers()
        assert self.hit["start"] > 0
        document_id = self.hit["taguette_document_id"]
        original_text = collect(self.workspace)[1][self.hit["chapter_id"]]
        expected = original_text[:self.hit["end"]]
        update = self.fetch(f'/api/project/1/document/{document_id}/highlight/{self.hit["taguette_highlight_id"]}',
                            method="POST", headers=dict(headers, **{"Content-Type": "application/json"}),
                            body=json.dumps({"start_offset": 0}))
        assert update.code == 200
        native = self.fetch("/project/1/export/highlights/.csv", headers=headers)
        assert native.code == 200
        rows = list(csv.DictReader(StringIO(native.body.decode("utf-8"))))
        row = next(row for row in rows if int(row["id"]) == self.hit["taguette_highlight_id"])
        if os.environ.get("READWRENCH_CONCORDANCE_EXPECT_TAGUETTE_RESIZE_FIX") == "1":
            assert row["content"].strip() == expected
        else:
            # Reproduced Taguette 1.5.2 defect: native exports use the old
            # cached snippet after the API changes the selected range.
            assert row["content"].strip() == self.hit["exact"]
            assert row["content"].strip() != expected
        reviewed = collect(self.workspace)[2][0]
        assert reviewed["exact"] == expected
        assert reviewed["start"] == 0
