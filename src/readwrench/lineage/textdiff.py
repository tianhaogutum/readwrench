# Ported from jsdiff (https://github.com/kpdecker/jsdiff), src/diff/base and array,
# Copyright (c) 2009-2015, Kevin Decker <kpdecker@gmail.com>; BSD-3-Clause
# (licenses/jsdiff-BSD-3-Clause.txt). Changes: translated to Python, array mode only.
"""Myers sequence diff with jsdiff's path selection, timeout and edit-length budget.

Ported so that change lists and inline word differences match the original tool's
output token for token (Python's :mod:`difflib` picks different, non-minimal matches).
"""
from __future__ import annotations

import time


class _Component:
    __slots__ = ("count", "added", "removed", "previous")

    def __init__(self, count, added, removed, previous):
        self.count = count
        self.added = added
        self.removed = removed
        self.previous = previous


class _Path:
    __slots__ = ("old_pos", "last")

    def __init__(self, old_pos, last):
        self.old_pos = old_pos
        self.last = last


def _add_to_path(path: _Path, added: bool, removed: bool, old_inc: int) -> _Path:
    last = path.last
    if last is not None and last.added == added and last.removed == removed:
        return _Path(path.old_pos + old_inc, _Component(last.count + 1, added, removed, last.previous))
    return _Path(path.old_pos + old_inc, _Component(1, added, removed, last))


def _extract_common(path: _Path, new: list, old: list, diagonal: int) -> int:
    new_len, old_len = len(new), len(old)
    old_pos = path.old_pos
    new_pos = old_pos - diagonal
    common = 0
    while new_pos + 1 < new_len and old_pos + 1 < old_len and old[old_pos + 1] == new[new_pos + 1]:
        new_pos += 1
        old_pos += 1
        common += 1
    if common:
        path.last = _Component(common, False, False, path.last)
    path.old_pos = old_pos
    return new_pos


def _build(last: _Component, new: list, old: list) -> list[dict]:
    components = []
    while last is not None:
        components.append(last)
        last = last.previous
    components.reverse()
    result = []
    new_pos = old_pos = 0
    for component in components:
        if not component.removed:
            value = new[new_pos:new_pos + component.count]
            new_pos += component.count
            if not component.added:
                old_pos += component.count
        else:
            value = old[old_pos:old_pos + component.count]
            old_pos += component.count
        result.append({"count": component.count, "added": component.added, "removed": component.removed,
                       "value": value})
    return result


def diff_arrays(old: list, new: list, *, timeout_ms: float | None = None,
                max_edit_length: int | None = None) -> list[dict] | None:
    """jsdiff ``diffArrays``: ``[{count, added, removed, value}]`` or None when over budget."""
    new_len, old_len = len(new), len(old)
    edit_length = 1
    max_edit = new_len + old_len
    if max_edit_length is not None:
        max_edit = min(max_edit, max_edit_length)
    deadline = time.monotonic() + timeout_ms / 1000 if timeout_ms is not None else None

    best: dict[int, _Path | None] = {0: _Path(-1, None)}
    new_pos = _extract_common(best[0], new, old, 0)
    if best[0].old_pos + 1 >= old_len and new_pos + 1 >= new_len:
        return _build(best[0].last, new, old)

    min_diagonal = -float("inf")
    max_diagonal = float("inf")
    while edit_length <= max_edit and (deadline is None or time.monotonic() <= deadline):
        diagonal = int(max(min_diagonal, -edit_length))
        # The upper bound is re-read every step: reaching the end of the old sequence
        # narrows it while this edit length is still being explored.
        while diagonal <= min(max_diagonal, edit_length):
            remove_path = best.get(diagonal - 1)
            add_path = best.get(diagonal + 1)
            if remove_path is not None:
                best[diagonal - 1] = None
            can_add = False
            if add_path is not None:
                add_new_pos = add_path.old_pos - diagonal
                can_add = 0 <= add_new_pos < new_len
            can_remove = remove_path is not None and remove_path.old_pos + 1 < old_len
            if not can_add and not can_remove:
                best[diagonal] = None
                diagonal += 2
                continue
            if not can_remove or (can_add and remove_path.old_pos < add_path.old_pos):
                base = _add_to_path(add_path, True, False, 0)
            else:
                base = _add_to_path(remove_path, False, True, 1)
            new_pos = _extract_common(base, new, old, diagonal)
            if base.old_pos + 1 >= old_len and new_pos + 1 >= new_len:
                return _build(base.last, new, old)
            best[diagonal] = base
            if base.old_pos + 1 >= old_len:
                max_diagonal = min(max_diagonal, diagonal - 1)
            if new_pos + 1 >= new_len:
                min_diagonal = max(min_diagonal, diagonal + 1)
            diagonal += 2
        edit_length += 1
    return None
