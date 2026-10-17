"""Layered settings: built-in defaults, then the user config, then the repo's `.devkit.yaml`."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import Level
from .repo import REPO_CONFIG
from .rules import REGISTRY, Rule, get_rule

SCOPES = ("opted-in", "all")


class ConfigError(Exception):
    def __init__(self, path: Path, key: str, message: str):
        super().__init__(f"{path}: {key}: {message}")
        self.path, self.key = path, key


@dataclass
class RuleConfig:
    level: Level
    settings: dict[str, Any]
    sources: dict[str, str]  # "level" and each setting -> the layer that last set it


@dataclass
class Config:
    rules: dict[str, RuleConfig]
    repos: str = "opted-in"
    repos_source: str = "default"
    top: Path | None = None
    user_path: Path | None = None
    repo_path: Path | None = None
    ignored: list[str] = field(default_factory=list)
    errors: list[ConfigError] = field(default_factory=list)  # layers skipped in lenient mode

    @property
    def opted_in(self) -> bool:
        return self.repo_path is not None

    @property
    def in_scope(self) -> bool:
        return self.repos == "all" or self.opted_in


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "devkit" / "config.yaml"


def _check_type(path: Path, key: str, value: Any, default: Any) -> Any:
    if isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise ConfigError(path, key, "expected a list of non-empty strings")
        return list(value)
    if type(value) is not type(default):  # exact, so a bool never passes as an int
        raise ConfigError(path, key, f"expected {type(default).__name__}")
    return value


def parse_layer(path: Path, raw: Any) -> dict[str, Any]:
    """Validate one file's contents into {"repos"?: str, "rules": {name: {"level"?: Level, setting: value}}}."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(path, "(top)", "expected a mapping")
    out: dict[str, Any] = {"rules": {}}
    for key, value in raw.items():
        if key == "repos":
            if value not in SCOPES:
                raise ConfigError(path, "repos", f"expected one of {', '.join(SCOPES)}")
            out["repos"] = value
        elif key == "rules":
            if not isinstance(value, dict):
                raise ConfigError(path, "rules", "expected a mapping of rule names")
            for name, spec in value.items():
                out["rules"][name] = _parse_rule(path, name, spec)
        else:
            raise ConfigError(path, str(key), "unknown key")
    return out


def _parse_rule(path: Path, name: Any, spec: Any) -> dict[str, Any]:
    if name not in REGISTRY:
        raise ConfigError(path, f"rules.{name}", f"unknown rule (known: {', '.join(REGISTRY)})")
    rule = get_rule(name)
    if not isinstance(spec, dict):
        spec = {"level": spec}
    out: dict[str, Any] = {}
    for key, value in spec.items():
        where = f"rules.{name}.{key}"
        if key == "level":
            try:
                out["level"] = Level.parse(value)
            except ValueError as e:
                raise ConfigError(path, where, str(e)) from None
        elif key in rule.settings:
            out[key] = _check_type(path, where, value, rule.settings[key])
        else:
            raise ConfigError(path, where, "unknown setting")
    return out


def _read(path: Path) -> Any:
    import yaml  # imported only when a config file exists

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as e:  # deep nesting: hostile repo
        raise ConfigError(path, "(file)", f"cannot read: {e}") from None


def _set(rc: RuleConfig, rule: Rule, key: str, value: Any, label: str) -> None:
    if key in rule.extend:
        rc.settings[key] = rc.settings[key] + [v for v in value if v not in rc.settings[key]]
    else:
        rc.settings[key] = value
    rc.sources[key] = label


def _apply(cfg: Config, layer: dict[str, Any], label: str, *, repo: bool) -> None:
    if "repos" in layer:
        if repo:
            cfg.ignored.append(f"repos: {layer['repos']} ({label}: only the user config sets the scope)")
        else:
            cfg.repos, cfg.repos_source = layer["repos"], label
    for name, spec in layer["rules"].items():
        rule, rc = get_rule(name), cfg.rules[name]
        for key, value in spec.items():
            if key == "level":
                if repo and rule.guard and value < rc.level:
                    cfg.ignored.append(f"rules.{name}.level: {value} ({label}: a repo cannot loosen a guard rule)")
                elif value != rc.level or not repo:
                    rc.level, rc.sources["level"] = value, label
            elif repo and rule.guard and key not in (rule.repo_settings or ()):
                cfg.ignored.append(f"rules.{name}.{key} ({label}: a repo cannot loosen a guard rule)")
            else:
                _set(rc, rule, key, value, label)


def load(top: Path | None, *, strict: bool, user_path: Path | None = None) -> Config:
    """Resolve the effective config for a repo top level (or None outside any repo).

    strict: raise on the first invalid layer. Otherwise record it in `errors` and skip that layer.
    """
    rules = {}
    for name in REGISTRY:
        rule = get_rule(name)
        rules[name] = RuleConfig(rule.default_level, copy.deepcopy(dict(rule.settings)),
                                 {"level": "default", **{k: "default" for k in rule.settings}})
    user_path = user_path or user_config_path()
    repo_path = top / REPO_CONFIG if top and (top / REPO_CONFIG).is_file() else None
    cfg = Config(rules=rules, top=top, user_path=user_path, repo_path=repo_path)
    for path, label, repo in ((user_path, f"user: {user_path}", False), (repo_path, f"repo: {REPO_CONFIG}", True)):
        if path is None or not path.is_file():
            continue
        try:
            layer = parse_layer(path, _read(path))
        except ConfigError as e:
            if strict:
                raise
            cfg.errors.append(e)
            continue
        _apply(cfg, layer, label, repo=repo)
    return cfg
