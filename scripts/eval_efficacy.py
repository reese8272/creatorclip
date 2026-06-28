"""Runnable entrypoint for the personalization efficacy harness (Issue 198).

Opens a real DB session, evaluates every creator with enough labeled clips, and prints the
pooled (primary) + per-creator-above-N (secondary) ranking-metric table comparing
random / generic-signal / DNA+preference. Read-only; never calls Anthropic or YouTube.

Usage (on a host with DB access, e.g. the VM or a staging box):
    python3 scripts/eval_efficacy.py [--k 5] [--min-labels 30] [--train-frac 0.7]
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Personalization efficacy harness (Issue 198)")
    parser.add_argument("--k", type=int, default=5, help="rank cutoff for NDCG@k / MAP@k")
    parser.add_argument("--min-labels", type=int, default=30, help="min labels for per-creator rows")
    parser.add_argument("--train-frac", type=float, default=0.7, help="chronological train fraction")
    args = parser.parse_args()

    from sqlalchemy import select

    from db import get_sessionmaker  # type: ignore[import-untyped]
    from models import Creator
    from tests.eval.efficacy import RANKINGS, evaluate_creator, pool_metrics

    sessionmaker = get_sessionmaker()
    per_creator = []
    async with sessionmaker() as session:
        creator_ids = (await session.execute(select(Creator.id))).scalars().all()
        for cid in creator_ids:
            cm = await evaluate_creator(session, cid, k=args.k, train_frac=args.train_frac)
            if cm is not None:
                per_creator.append(cm)

    if not per_creator:
        print("No creators with enough chronologically-splittable labeled clips yet.")
        return 0

    pooled = pool_metrics(per_creator)
    print(f"\nPooled ranking metrics (k={args.k}, n_creators={len(per_creator)}) — point [95% CI]:")
    for metric, by_ranking in pooled.items():
        print(f"\n  {metric.upper()}@{args.k}:")
        for ranking in RANKINGS:
            point, lo, hi = by_ranking[ranking]
            print(f"    {ranking:<16} {point:.3f}  [{lo:.3f}, {hi:.3f}]")

    above = [cm for cm in per_creator if cm.n_eval >= args.min_labels]
    print(f"\nPer-creator rows with >= {args.min_labels} eval labels: {len(above)} "
          f"(others pooled-only — single-creator metrics below the floor are noise).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
