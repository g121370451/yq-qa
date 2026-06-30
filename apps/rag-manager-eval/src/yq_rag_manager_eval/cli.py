from __future__ import annotations

import argparse
import json

from yq_rag_manager_eval.config import load_config
from yq_rag_manager_eval.runner import run_eval


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag-manager-eval")
    parser.add_argument("--config", required=True, help="Evaluation config yaml path.")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env file loaded before yaml placeholders are resolved.",
    )
    parser.add_argument(
        "--stage",
        choices=["import", "gen", "eval", "gen+eval", "del", "all"],
        default=None,
        help="Execution stage. Defaults to config.execution.stage or all.",
    )
    args = parser.parse_args()
    summary = run_eval(load_config(args.config, env_file=args.env_file), stage=args.stage)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
