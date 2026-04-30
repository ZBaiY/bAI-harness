from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from .config.router import ProviderConfigStore
from .config.workspace import WorkspaceStore
from .core.errors import BaiUserError
from .execution.harness import Harness


BARE_USAGE = "bai: provide --workspace <name-or-id> and a request, or use 'bai run'."
USER_FACING_EXCEPTIONS = (BaiUserError, FileNotFoundError, KeyError, PermissionError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bai")
    subparsers = parser.add_subparsers(dest="command", required=True)

    workspace = subparsers.add_parser("workspace")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)

    add = workspace_sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("root_path")

    workspace_sub.add_parser("list")

    show = workspace_sub.add_parser("show")
    show.add_argument("name_or_id")

    allow_test = workspace_sub.add_parser("allow-test")
    allow_test.add_argument("name_or_id")
    allow_test.add_argument("--argv")
    allow_test.add_argument("--argv-prefix")
    allow_test.add_argument("--writable-path", action="append", default=[])

    provider = subparsers.add_parser("provider")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)

    provider_sub.add_parser("show")

    set_local = provider_sub.add_parser("set-local")
    set_local.add_argument("--model", required=True)
    set_local.add_argument("--endpoint", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--workspace", required=True)
    run.add_argument("--approve-mutation", action="append", default=[])
    run.add_argument("request")

    return parser


def build_bare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bai", add_help=False)
    parser.add_argument("--workspace")
    parser.add_argument("--approve-mutation", action="append", default=[])
    parser.add_argument("request", nargs=argparse.REMAINDER)
    return parser


def run_harness(workspace_ref: str, request: str, approved_mutation_paths: list[str]) -> int:
    result = Harness().run(
        workspace_ref=workspace_ref,
        request=request,
        approved_mutation_paths=approved_mutation_paths,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def parse_argv_json(raw: str | None, label: str) -> list[str] | None:
    if raw is None:
        return None
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BaiUserError(f"{label} must be a JSON array of strings") from exc
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise BaiUserError(f"{label} must be a JSON array of strings")
    return value


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(BARE_USAGE, file=sys.stderr)
        return 2
    if argv[0] not in {"workspace", "provider", "run", "-h", "--help"}:
        args = build_bare_parser().parse_args(argv)
        if not args.workspace or not args.request:
            print(BARE_USAGE, file=sys.stderr)
            return 2
        request = " ".join(args.request).strip()
        if not request:
            print(BARE_USAGE, file=sys.stderr)
            return 2
        try:
            return run_harness(args.workspace, request, args.approve_mutation)
        except USER_FACING_EXCEPTIONS as exc:
            print(f"bai: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"bai internal error: {type(exc).__name__}", file=sys.stderr)
            return 1

    args = build_parser().parse_args(argv)
    try:
        if args.command == "workspace":
            store = WorkspaceStore()
            if args.workspace_command == "add":
                record = store.add(args.name, args.root_path)
                print(json.dumps(asdict(record), indent=2, sort_keys=True))
                return 0
            if args.workspace_command == "list":
                print(
                    json.dumps(
                        [asdict(record) for record in store.list()],
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0
            if args.workspace_command == "show":
                print(json.dumps(asdict(store.get(args.name_or_id)), indent=2, sort_keys=True))
                return 0
            if args.workspace_command == "allow-test":
                record = store.allow_test(
                    args.name_or_id,
                    argv=parse_argv_json(args.argv, "--argv"),
                    argv_prefix=parse_argv_json(args.argv_prefix, "--argv-prefix"),
                    writable_paths=args.writable_path,
                )
                print(json.dumps(asdict(record), indent=2, sort_keys=True))
                return 0
        if args.command == "provider":
            store = ProviderConfigStore()
            if args.provider_command == "show":
                print(json.dumps(store.read(), indent=2, sort_keys=True))
                return 0
            if args.provider_command == "set-local":
                store.set_local(model=args.model, endpoint=args.endpoint)
                print(json.dumps(store.read(), indent=2, sort_keys=True))
                return 0
        if args.command == "run":
            return run_harness(args.workspace, args.request, args.approve_mutation)
    except USER_FACING_EXCEPTIONS as exc:
        print(f"bai: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"bai internal error: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
