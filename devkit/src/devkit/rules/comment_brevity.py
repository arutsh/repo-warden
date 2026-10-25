"""Style rule: refuse newly introduced comment blocks longer than `max_lines` (ported from GrandFlow)."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from ..events import Event, Finding, Level
from . import Rule

LINE_COMMENT_STYLES = {
    ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//",
    ".go": "//", ".java": "//", ".c": "//", ".cpp": "//",
    ".h": "//", ".hpp": "//", ".rs": "//",
    ".py": "#", ".sh": "#", ".yml": "#", ".yaml": "#", ".rb": "#",
}
BLOCK_COMMENT_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".rs", ".css",
}
_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_TRIPLE_QUOTED_RE = re.compile(r'("{3}|\'{3})(.*?)\1', re.DOTALL)
_ALEMBIC_DIR = os.path.join("migrations", "versions", "")

Violation = tuple[int, str]  # (first line, the comment's text)


def comment_runs(numbered: list[tuple[int, str]], marker: str, max_lines: int) -> list[Violation]:
    """Each run of more than `max_lines` consecutive line comments; `###`-style banners don't count."""
    found, run = [], []
    for lineno, line in [*numbered, (-1, "")]:
        stripped = line.strip()
        is_comment = (stripped.startswith(marker) and not stripped.startswith(marker * 3)
                      and not (lineno == 1 and stripped.startswith("#!")))  # a shebang isn't a comment
        if is_comment and (not run or run[-1][0] + 1 == lineno):
            run.append((lineno, stripped))
            continue
        if len(run) > max_lines:
            found.append((run[0][0], "\n".join(t for _, t in run)))
        run = [(lineno, stripped)] if is_comment else []
    return found


def pattern_violations(ext: str, text: str, max_lines: int) -> list[Violation]:
    found = []
    marker = LINE_COMMENT_STYLES.get(ext)
    if marker:
        found += comment_runs(list(enumerate(text.splitlines(), 1)), marker, max_lines)
    patterns = ([_BLOCK_RE] if ext in BLOCK_COMMENT_EXTS else []) + ([_TRIPLE_QUOTED_RE] if ext == ".py" else [])
    for pattern in patterns:
        for m in pattern.finditer(text):
            if m.group(0).count("\n") + 1 > max_lines:
                body = m.group(2) if pattern is _TRIPLE_QUOTED_RE else m.group(0)  # a docstring's text, as ast gives it
                found.append((text.count("\n", 0, m.start()) + 1, body))
    return found


def python_violations(text: str, path: str, max_lines: int) -> list[Violation]:
    """tokenize and ast, so string literals aren't mistaken for comments or docstrings."""
    import ast
    import io
    import tokenize

    tree = ast.parse(text)
    comments = [(tok.start[0], tok.string) for tok in tokenize.generate_tokens(io.StringIO(text).readline)
                if tok.type == tokenize.COMMENT and tok.line.strip().startswith("#")]
    found = comment_runs(comments, "#", max_lines)
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners) or not node.body:
            continue
        if isinstance(node, ast.Module) and _ALEMBIC_DIR in path:
            continue  # Alembic's generated Revision ID/Revises header
        first = node.body[0]
        if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            continue
        if first.end_lineno - first.lineno + 1 > max_lines:
            found.append((first.lineno, first.value.value.strip()))
    return found


def violations(ext: str, text: str, path: str, max_lines: int) -> list[Violation]:
    if ext == ".py":
        import tokenize

        try:
            return python_violations(text, path, max_lines)
        except (SyntaxError, ValueError, tokenize.TokenError, RecursionError, MemoryError):  # deep nesting too
            pass
    return pattern_violations(ext, text, max_lines)


def _key(text: str) -> str:
    """A comment's identity, ignoring indentation: moving or re-indenting it doesn't introduce it."""
    return "\n".join(line.strip() for line in text.strip().splitlines())


def introduced(ext: str, before: str, after: str, path: str, max_lines: int) -> list[Violation]:
    """Violations in `after` that weren't already in `before`, matched by their text."""
    existing = Counter(_key(text) for _, text in violations(ext, before, path, max_lines)) if before else Counter()
    out = []
    for lineno, text in violations(ext, after, path, max_lines):
        if existing[_key(text)] > 0:
            existing[_key(text)] -= 1
        else:
            out.append((lineno, text))
    return out


class CommentBrevity(Rule):
    name = "comment_brevity"
    default_level = Level.BLOCK
    kinds = frozenset({"write", "edit"})
    content = True
    settings = {"max_lines": 2}

    def validate(self, key: str, value: Any) -> str | None:
        if key == "max_lines" and value < 1:
            return "must be at least 1"
        return None

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        if not event.path or event.after is None:
            return []
        ext = os.path.splitext(event.path)[1].lower()
        if ext not in LINE_COMMENT_STYLES and ext not in BLOCK_COMMENT_EXTS:
            return []
        max_lines = settings["max_lines"]
        new = introduced(ext, event.before or "", event.after, event.path, max_lines)
        if not new:
            return []
        lineno, text = new[0]
        preview = (text.strip().splitlines() or [""])[0][:80]
        where = "" if event.fragment else f"at line {lineno} "  # a fragment's line numbers aren't the file's
        return [Finding(self.name, (
            f"this {event.kind} introduces a comment longer than {max_lines} line{'s' if max_lines != 1 else ''} "
            f"{where}({preview!r}). Trim it; longer rationale belongs in the commit message, "
            "PR description or docs."), path=event.path, line=None if event.fragment else lineno)]


RULE = CommentBrevity()
