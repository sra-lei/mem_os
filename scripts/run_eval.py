"""CLI entry to run the MemOS evaluation suite.

Usage:
    python -m scripts.run_eval --phase base --notes "v0.1 框架验证"
    python -m scripts.run_eval --phase multi_session --limit 3 --top-k 5

Options:
    --phase            base | multi_session | proactive (default: base)
    --version          run version; defaults by phase (base->v0.1, ...)
    --top-k            retrieval top-k (default: 3)
    --threshold        judge pass threshold (default: 0.7)
    --llm              llm client: mock (default)
    --memory-provider  memory provider: stub (default) — your os_mem implementation plugs in here
    --memory-store     memory db location: tmp (throwaway per run, default) | file (persistent os_mem.db)
    --judge            judge: mock (default)
    --limit            run only first N cases (for smoke tests)
    --notes            note attached to the run
    --verbose, -v      print per-case progress, answers and errors
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from testing.db import get_session  # noqa: E402
from testing.db.models import TestRun  # noqa: E402
from testing.runner import PHASE_TO_VERSION, run_test_suite  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MemOS evaluation suite")
    parser.add_argument("--phase", default="base",
                        choices=["base", "multi_session", "proactive"])
    parser.add_argument("--version", default=None, help="defaults by phase")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--llm", default="mock", help="llm client (mock)")
    parser.add_argument("--memory-provider", default="stub",
                        help="memory provider (stub | your os_mem implementation)")
    parser.add_argument("--memory-store", default="tmp",
                        choices=["tmp", "file"],
                        help="memory db location: tmp (throwaway) | file (os_mem.db)")
    parser.add_argument("--judge", default="mock", help="judge (mock)")
    parser.add_argument("--limit", type=int, default=None, help="run first N cases")
    parser.add_argument("--notes", default=None)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print per-case progress, answers and errors")
    args = parser.parse_args()

    config = {
        "top_k": args.top_k,
        "judge_threshold": args.threshold,
        "llm_provider": args.llm,
        "memory_provider": args.memory_provider,
        "memory_store": args.memory_store,
        "judge_provider": args.judge,
    }
    version = args.version or PHASE_TO_VERSION.get(args.phase, "v0.1")

    print(f"running phase={args.phase} version={version} config={config}")
    run_id = run_test_suite(
        version=version,
        phase=args.phase,
        config=config,
        notes=args.notes,
        limit=args.limit,
        verbose=args.verbose,
    )

    with get_session() as session:
        run = session.get(TestRun, run_id)
        if run is None:
            print(f"run {run_id} not found in db")
            return
        print(f"\nrun_id={run_id} status={run.status} "
              f"passed={run.passed_count}/{run.total_cases} "
              f"pass_rate={run.pass_rate} duration={run.duration_seconds}s")
        print("open dashboard: uvicorn testing.api.main:app --port 8000")


if __name__ == "__main__":
    main()
