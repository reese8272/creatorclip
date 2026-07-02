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
    parser.add_argument(
        "--min-labels", type=int, default=30, help="min labels for per-creator rows"
    )
    parser.add_argument(
        "--train-frac", type=float, default=0.7, help="chronological train fraction"
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="grid-search DECAY_HALF_LIFE_DAYS on held-out NDCG@k instead of the "
        "3-ranking comparison (Issue 200); read-only, does NOT change the config",
    )
    args = parser.parse_args()

    from sqlalchemy import select

    from db import get_sessionmaker  # type: ignore[import-untyped]
    from models import Creator
    from preference.efficacy import (
        RANKINGS,
        evaluate_creator,
        load_labeled_clips,
        pool_metrics,
        select_best_half_life,
        sweep_half_life,
    )

    sessionmaker = get_sessionmaker()
    per_creator = []
    async with sessionmaker() as session:
        creator_ids = (await session.execute(select(Creator.id))).scalars().all()
        if args.sweep:
            all_clips = [await load_labeled_clips(session, cid) for cid in creator_ids]
            rows = sweep_half_life(
                [c for c in all_clips if c], k=args.k, train_frac=args.train_frac
            )
            print(f"\nHalf-life sweep — pooled NDCG@{args.k}, point [95% CI]:")
            for row in rows:
                print(
                    f"  H={row.half_life_days:>5.1f}d  {row.ndcg_at_k:.3f}  "
                    f"[{row.ci_low:.3f}, {row.ci_high:.3f}]  (n_creators={row.n_creators})"
                )
            if any(r.n_creators for r in rows):
                best = select_best_half_life(rows)
                print(
                    f"\nBest half-life: {best.half_life_days:.1f}d (ties break larger). "
                    "Change DECAY_HALF_LIFE_DAYS only if it clears the incumbent's CI."
                )
            else:
                print("\nNo creators with enough chronologically-splittable labeled clips yet.")
            return 0
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
    print(
        f"\nPer-creator rows with >= {args.min_labels} eval labels: {len(above)} "
        f"(others pooled-only — single-creator metrics below the floor are noise)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
