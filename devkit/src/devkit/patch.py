"""Codex `apply_patch` envelopes: parse them and apply their hunks in memory, the way Codex places them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BEGIN, END = "*** Begin Patch", "*** End Patch"
ADD, DELETE, UPDATE, MOVE = "*** Add File:", "*** Delete File:", "*** Update File:", "*** Move to:"
END_OF_FILE = "*** End of File"


class PatchError(ValueError):
    pass


@dataclass
class Hunk:
    context: str | None = None  # the `@@ <line>` anchor, sought before the hunk's old lines
    old: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    at_end: bool = False  # `*** End of File`: the old lines end the file


@dataclass
class FileOp:
    kind: Literal["add", "update", "delete"]
    path: str
    move_to: str | None = None
    content: str = ""  # add only
    hunks: list[Hunk] = field(default_factory=list)  # update only


def _header(line: str) -> bool:
    """A file header inside an update; like Codex, an indented one is a context line, not a header."""
    return line.rstrip().startswith((ADD, DELETE, UPDATE))


def _path(line: str, marker: str) -> str:
    path = line.strip()[len(marker):].strip()
    if not path:
        raise PatchError(f"missing path after {marker!r}")
    return path


def parse(text: str) -> list[FileOp]:
    """The file operations in the envelope; it may sit inside other text (a heredoc around it)."""
    lines = text.splitlines()
    marks = [line.strip() for line in lines]
    if BEGIN not in marks or END not in marks:
        raise PatchError("not an apply_patch envelope (*** Begin Patch ... *** End Patch)")
    start, end = marks.index(BEGIN), len(marks) - 1 - marks[::-1].index(END)
    if end < start:
        raise PatchError("*** End Patch comes before *** Begin Patch")
    body, ops, i = lines[start + 1:end], [], 0
    while i < len(body):
        line = body[i]
        head = line.strip()
        i += 1
        if not head:
            continue
        if head.startswith(ADD):
            added = []
            while i < len(body) and body[i].startswith("+"):
                added.append(body[i][1:])
                i += 1
            ops.append(FileOp("add", _path(line, ADD), content="".join(f"{a}\n" for a in added)))
        elif head.startswith(DELETE):
            ops.append(FileOp("delete", _path(line, DELETE)))
        elif head.startswith(UPDATE):
            op = FileOp("update", _path(line, UPDATE))
            if i < len(body) and body[i].rstrip().startswith(MOVE):
                op.move_to = _path(body[i], MOVE)
                i += 1
            while i < len(body) and not _header(body[i]):
                line = body[i]
                i += 1
                if line.rstrip() == END_OF_FILE:
                    if not op.hunks:
                        raise PatchError(f"{END_OF_FILE!r} before any hunk in {op.path}")
                    op.hunks[-1].at_end = True
                elif line.startswith("@@"):
                    op.hunks.append(Hunk(line[2:].strip().removesuffix("@@").strip() or None))
                else:
                    if not op.hunks:  # Codex lets the first hunk omit its `@@`
                        op.hunks.append(Hunk())
                    h, tag, rest = op.hunks[-1], line[:1], line[1:]
                    if tag in (" ", ""):
                        h.old.append(rest)
                        h.new.append(rest)
                    elif tag == "-":
                        h.old.append(rest)
                    elif tag == "+":
                        h.new.append(rest)
                    else:
                        raise PatchError(f"unexpected line in the update of {op.path}: {line!r}")
            if not any(h.old or h.new for h in op.hunks):
                raise PatchError(f"update of {op.path} has no hunks")
            ops.append(op)
        else:
            raise PatchError(f"unexpected line in patch: {line!r}")
    if not ops:
        raise PatchError("empty patch")
    return ops


def _seek(lines: list[str], pattern: list[str], start: int, at_end: bool) -> int | None:
    """Where `pattern` occurs from `start`: exact first, then ignoring trailing, then all, whitespace.

    `at_end` (`*** End of File`) only looks at the end of the file, as Codex does.
    """
    n = len(pattern)
    starts = range(max(len(lines) - n, start) if at_end else start, len(lines) - n + 1)
    for norm in (str, str.rstrip, str.strip):
        want = [norm(p) for p in pattern]
        for i in starts:
            if [norm(x) for x in lines[i:i + n]] == want:
                return i
    return None


def apply(before: str, hunks: list[Hunk]) -> str | None:
    """`before` with the hunks applied in order, or None when one can't be placed (stale context)."""
    lines = before.split("\n")
    if lines[-1] == "":
        lines.pop()
    pos, edits = 0, []
    for h in hunks:
        if h.context is not None:
            at = _seek(lines, [h.context], pos, False)
            if at is None:
                return None
            pos = at + 1
        if not h.old:  # a pure addition goes at the end, as in Codex
            edits.append((len(lines), 0, h.new))
            continue
        old, new = h.old, h.new
        at = _seek(lines, old, pos, h.at_end)
        if at is None and old[-1] == "":  # a trailing blank context line the file doesn't have
            old, new = old[:-1], new[:-1] if new and new[-1] == "" else new
            at = _seek(lines, old, pos, h.at_end) if old else pos  # nothing left to match: insert here
        if at is None:
            return None
        edits.append((at, len(old), new))
        pos = at + len(old)
    # Back to front so earlier positions stay valid; at a shared position the later hunk goes in first.
    for _, (at, n, new) in sorted(enumerate(edits), key=lambda e: (e[1][0], e[0]), reverse=True):
        lines[at:at + n] = new
    return "".join(f"{x}\n" for x in lines)


def fallback(hunks: list[Hunk]) -> tuple[str, str]:
    """The hunks' old and new sides on their own, for a patch that doesn't fit the file on disk."""
    return ("".join(f"{x}\n" for h in hunks for x in h.old), "".join(f"{x}\n" for h in hunks for x in h.new))
