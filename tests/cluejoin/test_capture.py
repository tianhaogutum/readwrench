import os
import shutil
import signal
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from readwrench.cluejoin.capture import capture_index
from readwrench.cluejoin.model import CluejoinError, Fragment
from readwrench.cluejoin.search import search
from readwrench.cluejoin.store import build_index, open_index

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Real extractor integration is opt-in: set READWRENCH_RGA to an rga executable
# (ripgrep-all 0.10.10 with Poppler was used to write these expectations). PDF cases
# also need Poppler's pdftotext, which rga calls for PDFs.
REAL_RGA = os.environ.get("READWRENCH_RGA") or os.environ.get("CLUEJOIN_RGA")
requires_rga = pytest.mark.skipif(
    not REAL_RGA, reason="set READWRENCH_RGA for real extractor integration"
)
requires_rga_pdf = pytest.mark.skipif(
    not REAL_RGA or shutil.which("pdftotext") is None,
    reason="set READWRENCH_RGA (and install pdftotext) for real PDF extraction",
)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "notes with spaces.txt"
    path.write_text("alpha\nbeta omega\n", encoding="utf-8")
    return path


def fake_rga(tmp_path, body):
    path = tmp_path / "fake rga"
    path.write_text("#!/usr/bin/env python3\nimport json, os, sys, time\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


GOOD_EVENT = """
event = {'type':'match', 'data': {'path':{'text':sys.argv[-1]},
         'lines':{'text':'alpha\\nbeta omega\\n'}, 'line_number':1}}
print(json.dumps(event), flush=True)
"""


def test_checked_capture_keeps_original_source_and_no_temporary_origin(tmp_path, source):
    executable = fake_rga(tmp_path, GOOD_EVENT)
    index = tmp_path / "captured.sqlite"
    stats = capture_index(index, [source], executable=executable)
    assert stats["upstream_exit_code"] == 0
    with closing(open_index(index)) as connection:
        doc = search(connection, [["alpha beta"], ["omega"]])["documents"][0]
    assert doc["source"] == str(source.resolve())
    assert doc["line_span"] == 1
    assert doc["evidence"][0]["origin"] == {
        "input": str(source.resolve()), "record": 1, "record_end": 2,
        "kind": "rga-extracted-line",
    }
    assert not list(tmp_path.glob(".cluejoin-rga-*"))


@pytest.mark.parametrize("body, message", [
    (GOOD_EVENT + "sys.stderr.write('conversion failed')\nsys.exit(2)\n", "exit 2"),
    ("print('malformed')\n", "invalid JSON"),
    ("sys.exit(1)\n", "no extracted lines"),
    (GOOD_EVENT.replace("sys.argv[-1]", "'unrequested.txt'"), "unrequested source"),
])
def test_failed_capture_preserves_old_index(tmp_path, source, body, message):
    index = tmp_path / "existing.sqlite"
    build_index(index, [Fragment("old", "old.pdf", "p1", "old material", page=1)])
    before = index.read_bytes()
    with pytest.raises(CluejoinError, match=message):
        capture_index(index, [source], executable=fake_rga(tmp_path, body), replace=True)
    assert index.read_bytes() == before
    assert not list(tmp_path.glob(".cluejoin-rga-*"))


def test_capture_bounds_stdout_and_drains_large_stderr(tmp_path, source):
    oversized = fake_rga(tmp_path, "sys.stdout.write('x' * 1100000)\n")
    with pytest.raises(CluejoinError, match="exceeds 1 MiB"):
        capture_index(tmp_path / "oversized.sqlite", [source], executable=oversized,
                      max_output_mib=1)
    noisy = fake_rga(tmp_path, "sys.stderr.write('x' * 200000)\n" + GOOD_EVENT)
    stats = capture_index(tmp_path / "noisy.sqlite", [source], executable=noisy)
    assert len(stats["upstream_stderr"]) == 65536
    assert not list(tmp_path.glob(".cluejoin-rga-*"))


def test_timeout_kills_upstream_descendants(tmp_path, source):
    pid_file = tmp_path / "child.pid"
    body = (
        "import subprocess\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    executable = fake_rga(tmp_path, body)
    try:
        with pytest.raises(CluejoinError, match="exceeded"):
            capture_index(tmp_path / "timeout.sqlite", [source], executable=executable, timeout=0.4)
        pid = int(pid_file.read_text())
        status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                capture_output=True, text=True, timeout=2)
        assert status.returncode != 0 or status.stdout.strip().startswith("Z")
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert not list(tmp_path.glob(".cluejoin-rga-*"))


def test_missing_rga_and_duplicate_inputs_fail_without_writes(tmp_path, source):
    index = tmp_path / "missing.sqlite"
    with pytest.raises(CluejoinError, match="not found"):
        capture_index(index, [source], executable=str(tmp_path / "absent"))
    with pytest.raises(CluejoinError, match="duplicate input"):
        capture_index(index, [source, source])
    assert not index.exists()
    assert not list(tmp_path.glob(".cluejoin-rga-*"))


def test_changed_source_during_extraction_cannot_replace_index(tmp_path, source):
    index = tmp_path / "existing.sqlite"
    build_index(index, [Fragment("old", "old.pdf", "p1", "old material", page=1)])
    before = index.read_bytes()
    body = GOOD_EVENT + "open(sys.argv[-1], 'w').write('changed source material')\n"
    with pytest.raises(CluejoinError, match="changed during"):
        capture_index(index, [source], executable=fake_rga(tmp_path, body), replace=True)
    assert index.read_bytes() == before
    assert not list(tmp_path.glob(".cluejoin-rga-*"))


@requires_rga_pdf
def test_real_rga_capture_retains_wrapped_phrase_and_original_pdf(tmp_path):
    index = tmp_path / "rfc.sqlite"
    source = FIXTURES / "rfc2616.pdf"
    stats = capture_index(index, [source], executable=REAL_RGA)
    assert stats["fragments"] == 114
    with closing(open_index(index)) as connection:
        doc = search(connection, [["use affects"], ["non-idempotent sequences"]])["documents"][0]
    assert doc["source"] == str(source.resolve())
    assert doc["page_span"] == 20
    assert doc["evidence"][0]["match"] == "use\naffects"


@requires_rga_pdf
def test_real_rga_uppercase_pdf_and_unicode_filename(tmp_path):
    source = tmp_path / "报告 with spaces.PDF"
    shutil.copyfile(FIXTURES / "rfc2616.pdf", source)
    index = tmp_path / "uppercase.sqlite"
    capture_index(index, [source], executable=REAL_RGA)
    with closing(open_index(index)) as connection:
        doc = search(connection, [["use affects"], ["non-idempotent sequences"]])["documents"][0]
    assert doc["source"] == str(source.resolve())
    assert doc["page_span"] == 20


@requires_rga
def test_real_rga_utf16_bom_and_crlf_text(tmp_path):
    source = tmp_path / "UTF16 空格.txt"
    source.write_bytes("needle one\r\nneedle two\r\n".encode("utf-16"))
    index = tmp_path / "utf16.sqlite"
    capture_index(index, [source], executable=REAL_RGA)
    with closing(open_index(index)) as connection:
        doc = search(connection, [["one needle"], ["two"]])["documents"][0]
    assert doc["source"] == str(source.resolve())
    assert doc["evidence"][0]["match"] == "one\r\nneedle"
    assert doc["line_span"] == 1
