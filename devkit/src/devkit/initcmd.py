"""`devkit init`: opt a repo in with `.devkit.yaml`; optionally AGENTS.md, openspec rules and a CI workflow."""

from __future__ import annotations

import textwrap
from importlib import resources
from pathlib import Path

CI_WORKFLOW = ".github/workflows/devkit.yml"


class InitError(Exception):
    pass


def template(name: str) -> str:
    return resources.files("devkit").joinpath("templates", name).read_text(encoding="utf-8")


def _create(top: Path, rel: str, text: str, out: list[str]) -> None:
    """Write `rel` only if nothing is there, not even a dangling link."""
    path = top / rel
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # e.g. `.github` is a file
        raise InitError(f"cannot create {rel}: {e}") from None
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        out.append(f"{rel}: already exists, left unchanged")
        return
    out.append(f"{rel}: created")


def _read_or_empty(path: Path) -> str:
    """A dangling link or a directory reads as empty."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _claude_md(top: Path, out: list[str]) -> None:
    path = top / "CLAUDE.md"
    if not path.exists() and not path.is_symlink():
        _create(top, "CLAUDE.md", "@AGENTS.md\n", out)
    elif "@AGENTS.md" in _read_or_empty(path):
        out.append("CLAUDE.md: already exists and imports AGENTS.md")
    else:
        out.append("CLAUDE.md: already exists, left unchanged; add a line `@AGENTS.md` so Claude Code reads AGENTS.md")


def _openspec(top: Path, out: list[str]) -> None:
    """Append the generic `rules:` only when the config has none; otherwise print them."""
    import yaml

    rel, snippet = "openspec/config.yaml", template("openspec-rules.yaml")
    suggestion = "suggested rules:\n" + textwrap.indent(snippet, "  ")
    path = top / rel
    if not path.is_file():
        out.append(f"{rel}: not found (run `openspec init` first); {suggestion}")
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InitError(f"cannot read {path} ({e}); nothing was changed") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise InitError(f"{path} is not valid YAML ({e}); nothing was changed") from None
    if data is not None and not isinstance(data, dict):
        raise InitError(f"{path}: expected a mapping; nothing was changed")
    if data and "rules" in data:
        out.append(f"{rel}: already has rules, left unchanged; {suggestion}")
        return
    gap = "" if not text else "\n" if text.endswith("\n") else "\n\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(gap + snippet)
    out.append(f"{rel}: rules added")


def init(top: Path, *, agents_md: bool = False, openspec: bool = False, ci: bool = False) -> list[str]:
    """What was done, one line per file. Never overwrites a file."""
    out: list[str] = []
    _create(top, ".devkit.yaml", template("devkit.yaml"), out)
    if agents_md:
        _create(top, "AGENTS.md", template("AGENTS.template.md"), out)
        _claude_md(top, out)
    if openspec:
        _openspec(top, out)
    if ci:
        _create(top, CI_WORKFLOW, template("devkit-ci.yml"), out)
    return out
