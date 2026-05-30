"""CLI: ``python -m prismal.plugins <subcommand>`` (SPEC-EXT-008).

Subcommands:
    list             List installed plugins (without loading them).
    info <name>      Show details for one plugin.
    doctor           Attempt to load every plugin and report failures.
    enable <name>    Print the env-var change needed to allowlist a plugin.
    disable <name>   Print the env-var change needed to denylist a plugin.

Exit codes:
    0  success
    1  general error / no subcommand
    2  plugin not found
    3  one or more plugins failed to load (doctor)
"""

from __future__ import annotations

import argparse

from prismal.agents.extension.plugins import (
    discover_plugins,
    get_plugin_info,
    list_plugins,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m prismal.plugins")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="List installed plugins")
    info_p = sub.add_parser("info", help="Show details for one plugin")
    info_p.add_argument("name")
    sub.add_parser("doctor", help="Load every plugin and report failures")
    enable_p = sub.add_parser("enable", help="Allowlist a plugin (prints env var)")
    enable_p.add_argument("name")
    disable_p = sub.add_parser("disable", help="Denylist a plugin (prints env var)")
    disable_p.add_argument("name")
    return parser


def _cmd_list() -> int:
    infos = list_plugins()
    if not infos:
        print("No prismal plugins installed.")
        return 0
    print(f"{'NAME':<28} {'GROUP':<14} {'VERSION':<10} MODULE")
    for info in sorted(infos, key=lambda i: (i.group, i.name)):
        print(f"{info.name:<28} {info.group:<14} {info.dist_version:<10} {info.module}")
    return 0


def _cmd_info(name: str) -> int:
    info = get_plugin_info(name)
    if info is None:
        print(f"Plugin '{name}' not found.")
        return 2
    print(f"name:        {info.name}")
    print(f"group:       {info.group}")
    print(f"module:      {info.module}")
    print(f"object:      {info.object_name}")
    print(f"dist:        {info.dist_name}")
    print(f"version:     {info.dist_version}")
    return 0


def _cmd_doctor() -> int:
    report = discover_plugins()
    print(
        f"loaded={report.loaded_count} failed={report.failed_count} skipped={report.skipped_count}"
    )
    for result in report.failed:
        print(f"  FAIL {result.info.name} ({result.info.group}): {result.error}")
    return 3 if report.failed_count else 0


def _cmd_enable(name: str) -> int:
    print(
        f"To allowlist '{name}', add it to PRISMAL_PLUGINS_ALLOWLIST, e.g.:\n"
        f"  export PRISMAL_PLUGINS_ALLOWLIST='[\"{name}\"]'"
    )
    return 0


def _cmd_disable(name: str) -> int:
    print(
        f"To disable '{name}', add it to PRISMAL_PLUGINS_DENYLIST, e.g.:\n"
        f"  export PRISMAL_PLUGINS_DENYLIST='[\"{name}\"]'"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m prismal.plugins``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        return _cmd_list()
    if args.command == "info":
        return _cmd_info(args.name)
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "enable":
        return _cmd_enable(args.name)
    if args.command == "disable":
        return _cmd_disable(args.name)
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
