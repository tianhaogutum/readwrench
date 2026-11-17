"""Optional rga composition with checked exit status and process-group cleanup."""

import os
import selectors
import signal
import subprocess
import tempfile
import time
from dataclasses import replace as replace_fragment
from pathlib import Path

from .importers import RGA_SUFFIXES, from_rga
from .model import CluejoinError, line_break_ends
from .store import build_index, source_unchanged_check

MAX_STDERR = 65_536


def _stop(process: subprocess.Popen) -> None:
    # rga starts rg and extractors. Killing only the parent can leave those alive.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def capture_index(
    index: Path, inputs: list[Path], *, executable: str = "rga", timeout: float = 30,
    max_output_mib: int = 128, replace: bool = False,
) -> dict:
    if os.name != "posix":
        raise CluejoinError(
            "capture currently requires POSIX process groups; import rga JSON instead"
        )
    if not 0 < timeout <= 300 or not 1 <= max_output_mib <= 512:
        raise CluejoinError("timeout must be >0 and <=300 seconds; max-output-mib must be 1..512")
    if index.exists() and not replace:
        raise CluejoinError(f"index already exists: {index}; use --replace to rebuild it")
    if not inputs:
        raise CluejoinError("provide at least one PDF or plain-text input file")
    for path in inputs:
        if not path.is_file() or path.suffix.lower() not in RGA_SUFFIXES:
            raise CluejoinError(f"capture accepts explicit PDF/plain-text files only: {path}")
        if path.resolve() == index.resolve():
            raise CluejoinError("the output index must not replace an input document")
    resolved = [path.resolve() for path in inputs]
    sources = {str(path) for path in resolved}
    if len(sources) != len(inputs):
        raise CluejoinError("duplicate input files (including aliases) are not allowed")
    unchanged = source_unchanged_check(resolved)
    with tempfile.TemporaryDirectory(prefix=".cluejoin-rga-", dir=index.parent) as directory:
        work = Path(directory).resolve()
        config = work / "config.json"
        config.write_text("{}\n", encoding="utf-8")
        log = work / "rga.jsonl"
        command = [
            executable, f"--rga-config-file={config}", f"--rga-cache-path={work / 'cache'}",
            "--no-config", "--threads", "1", "--json", "--line-number", "--color=never",
            "^", "--", *(str(path) for path in resolved),
        ]
        environment = dict(os.environ, TOKIO_WORKER_THREADS="1")
        environment.pop("RGA_CONFIG", None)
        environment.update(TMPDIR=str(work), XDG_CACHE_HOME=str(work / "xdg-cache"))
        deadline = time.monotonic() + timeout
        stderr = bytearray()
        total = 0
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, env=environment,
            )
        except FileNotFoundError as exc:
            raise CluejoinError(
                f"rga executable not found: {executable}; install ripgrep-all"
            ) from exc
        try:
            with log.open("wb") as output, selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CluejoinError(f"rga extraction exceeded {timeout:g} seconds")
                    for key, _ in selector.select(min(remaining, 0.5)):
                        chunk = os.read(key.fileobj.fileno(), 65_536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "stdout":
                            total += len(chunk)
                            if total > max_output_mib * 1024 * 1024:
                                raise CluejoinError(
                                    f"rga output exceeds {max_output_mib} MiB; "
                                    "use a narrower extraction or raise --max-output-mib"
                                )
                            output.write(chunk)
                        elif len(stderr) < MAX_STDERR:
                            stderr.extend(chunk[:MAX_STDERR - len(stderr)])
                try:
                    code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired as exc:
                    raise CluejoinError(f"rga extraction exceeded {timeout:g} seconds") from exc
            if code not in {0, 1}:
                detail = stderr.decode("utf-8", errors="replace").strip()
                raise CluejoinError(f"rga failed with exit {code}: {detail or 'no error detail'}")
            if code == 1:
                raise CluejoinError(
                    "rga found no extracted lines; check text extraction/OCR upstream"
                )

            def captured_fragments():
                for fragment in from_rga(log):
                    if fragment.source not in sources:
                        raise CluejoinError(
                            f"rga returned an unrequested source: {fragment.source!r}"
                        )
                    # The temporary log is removed after import. Locate retained
                    # evidence in the original source and its extracted stream.
                    line = fragment.line or 0
                    yield replace_fragment(
                        fragment, origin=fragment.source, record=line,
                        record_end=line + len(line_break_ends(fragment.text, fragment.origin_kind)),
                        origin_kind="rga-extracted-line",
                    )

            stats = build_index(
                index, captured_fragments(), replace=replace, before_publish=unchanged
            )
            return {
                **stats, "adapter": "rga", "upstream_exit_code": code, "captured_bytes": total,
                "upstream_stderr": stderr.decode("utf-8", errors="replace"),
            }
        except BaseException:
            _stop(process)
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
