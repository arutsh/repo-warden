"""`devkit hook | install | uninstall | status | check | init`."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
from typing import TextIO

from .adapters import AGENTS


def run_hook(agent: str, name: str, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> int:
    """Fail open: a broken devkit must never stall the agent. Enforcement lives in cplt, repo-warden and CI."""
    try:
        from .engine import evaluate, one_run

        adapter = importlib.import_module(AGENTS[agent])
        hook_event, phase, _ = adapter.HOOK_EVENTS[name]
        payload = json.load(stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        with one_run():
            response = adapter.response(evaluate(adapter.events(payload, phase)), hook_event)
        if response:
            stdout.write(json.dumps(response) + "\n")
    except Exception as e:  # noqa: BLE001
        stderr.write(f"devkit: hook error ({type(e).__name__}: {e}); allowing\n")
    return 0


def devkit_exe() -> str:
    argv0 = sys.argv[0]
    if os.path.basename(argv0) == "devkit" and os.sep in argv0:
        return os.path.abspath(argv0)
    found = shutil.which("devkit")
    if not found:
        raise SystemExit("devkit: cannot find the devkit executable on PATH; install it with `uv tool install ./devkit`")
    return os.path.abspath(found)


def _agents(value: str) -> list[str]:
    names = [n.strip() for n in value.split(",") if n.strip()]
    unknown = [n for n in names if n not in AGENTS]
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"unknown agent {', '.join(unknown) or value!r} (supported: {', '.join(AGENTS)})")
    return names


def cmd_install(args: argparse.Namespace, *, remove: bool) -> int:
    from . import install

    if not args.agent and not args.git_hooks:
        print("devkit: nothing to do; pass --agent and/or --git-hooks", file=sys.stderr)
        return 2
    exe = None if remove else (args.exe or devkit_exe())
    steps = [(lambda a=a: install.uninstall(a) if remove else install.install(a, exe)) for a in args.agent or []]
    if args.git_hooks:
        cwd = os.getcwd()
        steps.append(lambda: install.uninstall_git_hook(cwd) if remove else install.install_git_hook(cwd, exe))
    status = 0
    for step in steps:
        try:
            print(step())
        except install.InstallError as e:
            print(f"devkit: {e}", file=sys.stderr)
            status = 1
    return status


def cmd_init(args: argparse.Namespace) -> int:
    from . import initcmd
    from .repo import find_top

    top = find_top(os.getcwd())
    if top is None:
        print("devkit: not in a git repository", file=sys.stderr)
        return 2
    try:
        lines = initcmd.init(top, agents_md=args.agents_md, openspec=args.openspec, ci=args.ci)
    except initcmd.InitError as e:
        print(f"devkit: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    print("next: `devkit status` shows the effective settings; `devkit install --git-hooks` adds the pre-commit check")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from . import config, install
    from .repo import find_top

    top = find_top(os.getcwd())
    try:
        cfg = config.load(top, strict=True)
    except config.ConfigError as e:
        print(f"devkit: invalid config: {e}", file=sys.stderr)
        return 1
    user = cfg.user_path
    print(f"user config: {user} ({'found' if user and user.is_file() else 'not found'})")
    if top is None:
        print("repo: not in a git repository")
    else:
        print(f"repo: {top} ({'opted in: .devkit.yaml' if cfg.opted_in else 'not opted in'})")
    print(f"repos: {cfg.repos}  ({cfg.repos_source}); style rules {'run' if cfg.in_scope else 'do not run'} here")
    print("rules:")
    for name, rc in cfg.rules.items():
        print(f"  {name:<16} {str(rc.level):<6} ({rc.sources['level']})")
        for key, value in rc.settings.items():
            print(f"    {key}: {json.dumps(value)}  ({rc.sources[key]})")
    if cfg.ignored:
        print("ignored settings:")
        for line in cfg.ignored:
            print(f"  {line}")
    print("agents:")
    for agent, target in install.AGENTS.items():
        state = "installed" if install.installed(agent) else "not installed"
        print(f"  {agent:<8} {state}  ({target.config_path()})")
        for warning in target.warnings():
            print(f"    warning: {warning}")
    if top is not None:
        try:
            print(f"git hook: {install.git_hook_state(str(top))}")
        except install.InstallError as e:
            print(f"git hook: unknown ({e})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devkit", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("hook", help="run as an agent hook (reads the hook JSON on stdin)")
    p.add_argument("agent", choices=list(AGENTS))
    # Not validated here: an argparse exit (2) would block the agent; run_hook checks it and fails open.
    p.add_argument("event", help="the adapter's hook event, e.g. pre-tool-use")

    for name, text in (("install", "register devkit hooks"), ("uninstall", "remove devkit hooks")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--agent", type=_agents, help=f"comma-separated: {', '.join(AGENTS)}")
        p.add_argument("--git-hooks", action="store_true", help="the current repo's git pre-commit hook")
        if name == "install":
            p.add_argument("--exe", help=argparse.SUPPRESS)  # tests: the devkit path to register

    sub.add_parser("status", help="show effective rule levels, where they came from, and what is installed")

    p = sub.add_parser("check", help="run the rules on staged changes or a commit range (exit 1: blocked, 2: error)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="staged changes against HEAD (for pre-commit)")
    mode.add_argument("--range", metavar="BASE..HEAD", help="the files changed on HEAD since BASE (for CI)")

    p = sub.add_parser("init", help="opt this repo in: write .devkit.yaml (never overwrites)")
    p.add_argument("--agents-md", action="store_true", help="also AGENTS.md, and CLAUDE.md importing it")
    p.add_argument("--openspec", action="store_true", help="also generic rules in openspec/config.yaml")
    p.add_argument("--ci", action="store_true", help="also a GitHub Actions workflow running `devkit check --range`")

    args = parser.parse_args(argv)
    if args.cmd == "hook":
        return run_hook(args.agent, args.event, sys.stdin, sys.stdout, sys.stderr)
    if args.cmd in ("install", "uninstall"):
        return cmd_install(args, remove=args.cmd == "uninstall")
    if args.cmd == "check":
        from .check import check

        return check(os.getcwd(), staged=args.staged, spec=args.range, out=sys.stdout, err=sys.stderr)
    if args.cmd == "init":
        return cmd_init(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
