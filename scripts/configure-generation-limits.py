#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mflab_knowledge.generation import update_generation_limits


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atualiza limites não secretos do gerador local de forma atômica."
    )
    parser.add_argument("--config", type=Path, default=Path("generation.toml"))
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-context-characters", type=int)
    args = parser.parse_args()
    config = update_generation_limits(
        args.config,
        max_output_tokens=args.max_output_tokens,
        max_context_characters=args.max_context_characters,
    )
    print(
        json.dumps(
            {
                "config": str(config.path),
                "max_output_tokens": config.max_output_tokens,
                "max_context_characters": config.max_context_characters,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
