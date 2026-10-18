"""`devkit hook | install | uninstall | status`."""

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
        from .engine import evaluate

        adapter = importlib.import_module(AGENTS[agent])
        hook_event, phase, _ = adapter.HOOK_EVENTS[name]
        payload = json.load(stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
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

    if not args.agent:
        print("devkit: nothing to do; pass --agent", file=sys.stderr)
        return 2
    exe = None if remove else (args.exe or devkit_exe())
    status = 0
    for agent in args.agent:
        try:
            print(install.uninstall(agent) if remove else install.install(agent, exe))
        except install.InstallError as e:
            print(f"devkit: {e}", file=sys.stderr)
            status = 1
    return status


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
        if name == "install":
            p.add_argument("--exe", help=argparse.SUPPRESS)  # tests: the devkit path to register

    sub.add_parser("status", help="show effective rule levels, where they came from, and what is installed")

    args = parser.parse_args(argv)
    if args.cmd == "hook":
        return run_hook(args.agent, args.event, sys.stdin, sys.stdout, sys.stderr)
    if args.cmd in ("install", "uninstall"):
        return cmd_install(args, remove=args.cmd == "uninstall")
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
