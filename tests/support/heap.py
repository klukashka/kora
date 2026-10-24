import gc
import tracemalloc


def snapshot() -> tracemalloc.Snapshot:
    gc.collect()
    return tracemalloc.take_snapshot()


def grown_lines(
    before: tracemalloc.Snapshot, after: tracemalloc.Snapshot
) -> dict[str, tracemalloc.StatisticDiff]:
    return {
        str(stat.traceback): stat
        for stat in after.compare_to(before, "lineno")
        if stat.size_diff > 0
    }


def recurring_growth(
    before: tracemalloc.Snapshot,
    middle: tracemalloc.Snapshot,
    after: tracemalloc.Snapshot,
    cycles: int,
) -> int:
    first = grown_lines(before, middle)
    second = grown_lines(middle, after)
    return sum(
        stat.size_diff
        for line, stat in grown_lines(before, after).items()
        if stat.count_diff >= cycles // 2 or (line in first and line in second)
    )
