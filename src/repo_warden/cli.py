"""`repo-warden --repo <path>`: startup checks, wiring, then serve MCP over stdio."""

from __future__ import annotations

import argparse
import sys

import anyio

from .audit import Auditor, AuditError, default_audit_path
from .broker import Broker
from .execution import DirectBackend, ExecutionBackend
from .handlers import default_registry
from .mcp_server import build_server
from .paths import SensitiveMatcher
from .policy import PolicyError, PolicyGate, default_policy_path, load_policy
from .repo import RepoError, open_repo

DIRECT_WARNING = """\
************************************************************************
* repo-warden: --backend direct                                        *
* NO OS CONTAINMENT IS VERIFIED. Child processes (git, rg) run with    *
* this user's full filesystem and network access. Use the default      *
* cplt backend for contained operation.                                *
************************************************************************"""


class StartupError(Exception):
    pass


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="repo-warden",
                                description="Capability broker bound to one git repository (MCP over stdio).")
    p.add_argument("--repo", required=True, help="top level of the git working tree to bind to")
    p.add_argument("--policy", default=None, help=f"policy file (default: {default_policy_path()})")
    p.add_argument("--audit", default=None, help=f"audit JSONL path (default: {default_audit_path()})")
    p.add_argument("--backend", choices=("cplt", "direct"), default="cplt",
                   help="execution backend (default: cplt)")
    return p.parse_args(argv)


def make_backend(name: str) -> ExecutionBackend:
    if name == "direct":
        print(DIRECT_WARNING, file=sys.stderr, flush=True)
        return DirectBackend()
    raise StartupError("the cplt backend is not available in this build yet; "
                       "run with --backend direct to start without verified containment")


async def run(args: argparse.Namespace) -> None:
    backend = make_backend(args.backend)
    repo = await open_repo(args.repo, backend)
    try:
        loaded = load_policy(args.policy or default_policy_path(), repo.root)
        auditor = Auditor.open(args.audit or default_audit_path(), repo, backend.name)
        try:
            registry = default_registry()
            broker = Broker(registry=registry, gate=PolicyGate(loaded.policy), auditor=auditor, repo=repo,
                            backend=backend, sensitive=SensitiveMatcher(loaded.config.sensitive_paths_extra))
            print(f"repo-warden: serving {repo.root} (session {repo.session_id}, backend {backend.name})",
                  file=sys.stderr, flush=True)
            server = build_server(broker, registry)
            from mcp.server.stdio import stdio_server
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
        finally:
            auditor.close()
    finally:
        repo.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        anyio.run(run, args)
    except (StartupError, RepoError, PolicyError, AuditError) as e:
        print(f"repo-warden: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
