"""Shared configuration for all tool suites.

Temporary directories are created under the *real* temp path. On macOS ``/var``
and ``/tmp`` are symlinks to ``/private/...``; several tools refuse outputs reached
through a symlinked ancestor, and some tests compare paths for equality, so the
suites must not see the symlinked spelling. The environment variable makes
subprocesses (``python -m readwrench ...``) agree.

Each tool's suite skips itself (``pytest.importorskip``) when the optional
dependencies of that tool's extra are not installed.
"""
import os
import tempfile

_REAL_TMP = os.path.realpath(tempfile.gettempdir())
tempfile.tempdir = _REAL_TMP
os.environ["TMPDIR"] = _REAL_TMP
