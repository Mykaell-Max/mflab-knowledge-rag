from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from mflab_knowledge.inventory import build_inventory, write_yaml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mflab-knowledge",
        description="Inventário e indexação somente leitura do MFLab.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "inventory",
        help="Descobre uma fonte local e gera um inventário YAML.",
    )
    inventory.add_argument("--source", required=True, type=Path)
    inventory.add_argument("--project", required=True)
    inventory.add_argument(
        "--access-class",
        default="lab",
        choices=("public", "lab", "project", "restricted", "pending"),
    )
    inventory.add_argument(
        "--profile",
        default="auto",
        choices=("auto", "generic", "mfsim-ng-pilot"),
        help="Política de seleção; 'auto' reconhece o MFSim-NG.",
    )
    inventory.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "inventory":
        try:
            inventory = build_inventory(
                source=args.source,
                project=args.project,
                access_class=args.access_class,
                profile=args.profile,
            )
            write_yaml(inventory, args.output)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"erro: {exc}") from exc

        summary = inventory["summary"]
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "discovered": summary["discovered_files"],
                    "indexable": summary["indexable_files"],
                    "excluded": summary["excluded_files"],
                    "errors": summary["errors"],
                },
                ensure_ascii=False,
            )
        )
        return 0

    return 2
