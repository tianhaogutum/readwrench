"""Bounded input reading and staged, atomic output writing."""
from __future__ import annotations

import json
import os
import stat
import uuid

from .jsutil import to_utf8


class InputError(Exception):
    """Invalid input, extraction failure or write failure (exit status 1)."""


LIMITS = {
    "captureBytes": 10 * 1024 * 1024,
    "manifestBytes": 2 * 1024 * 1024,
    "captures": 100,
    "elements": 100_000,
    "depth": 256,
    "blocks": 2000,
    "blockChars": 50_000,
    "totalChars": 1_000_000,
    "collectionBlocks": 20_000,
}


def _describe(error: OSError) -> str:
    return error.strerror or str(error)


def read_bounded(filename, limit: int = LIMITS["captureBytes"]) -> bytes:
    filename = os.fspath(filename)
    try:
        # Opening a FIFO without O_NONBLOCK can hang before fstat could reject it.
        # O_NONBLOCK is ignored for regular files and lets special files be inspected.
        fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        raise InputError(f"Cannot read {filename}: {_describe(error)}") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise InputError(f"Not a regular file: {filename}")
        if info.st_size > limit:
            raise InputError(f"File exceeds {limit} byte limit: {filename}")
        # Bound the read itself too, in case the file grows after fstat().
        chunks = []
        size = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise InputError(f"File exceeds {limit} byte limit: {filename}")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        raise InputError(f"Cannot read {filename}: {_describe(error)}") from error
    finally:
        os.close(fd)


def _reject_constant(name: str):
    raise ValueError(f"Unexpected token {name}")


def parse_json(data: bytes, filename) -> object:
    try:
        text = data.decode("utf-8", "replace")
        if text.startswith("﻿"):
            text = text[1:]
        return json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise InputError(f"Invalid JSON in {filename}: {error}") from None


def _identity(filename: str) -> str:
    try:
        return os.path.realpath(filename, strict=True)
    except FileNotFoundError:
        # Also resolve a symlinked parent for a not-yet-existing output.
        try:
            return os.path.join(os.path.realpath(os.path.dirname(filename), strict=True),
                                os.path.basename(filename))
        except OSError:
            return os.path.abspath(filename)


def write_outputs(outputs: list[dict], *, force: bool = False, protected_files=()) -> None:
    """Write every ``{"filename", "data"}`` output, or none of them.

    Each output is staged in a private temporary file beside its destination and then
    moved into place (replaced only with ``force``; otherwise hard-linked, which fails
    atomically if another process created the output meanwhile).
    """
    staged: list[str] = []
    try:
        protected = {_identity(os.path.abspath(os.fspath(name))) for name in protected_files}
        destinations: set[str] = set()
        # Validate every output before staging any one of them.
        for output in outputs:
            output["filename"] = os.path.abspath(os.fspath(output["filename"]))
            identity = _identity(output["filename"])
            if identity in protected:
                raise InputError(f"Refusing to overwrite an input: {output['filename']}")
            if identity in destinations:
                raise InputError("Output paths must be different")
            destinations.add(identity)
            if not force:
                try:
                    os.lstat(output["filename"])
                except FileNotFoundError:
                    pass
                else:
                    raise InputError(f"Output exists; use --force to replace it: {output['filename']}")
        pairs = []
        for output in outputs:
            os.makedirs(os.path.dirname(output["filename"]), exist_ok=True)
            temp = f"{output['filename']}.{uuid.uuid4()}.tmp"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            staged.append(temp)
            try:
                data = output["data"]
                view = memoryview(to_utf8(data) if isinstance(data, str) else data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            pairs.append((temp, output["filename"]))
        for temp, filename in pairs:
            if force:
                os.replace(temp, filename)
            else:
                os.link(temp, filename)
                os.unlink(temp)
    except InputError:
        raise
    except OSError as error:
        raise InputError(f"Cannot write report: {_describe(error)}") from error
    finally:
        for temp in staged:
            try:
                os.unlink(temp)
            except OSError:
                pass
