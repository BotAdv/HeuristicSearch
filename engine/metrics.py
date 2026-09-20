# -*- coding: utf-8 -*-
"""评估指标计算。

指标口径说明
------------
* **轮次（步数）**：一次对局中提交的猜测次数。
* **截尾（censored）**：超过 ``max_rounds`` 仍未解出的对局按 ``max_rounds`` 计入平均值/分位数，
  同时单独给出 ``solveRate`` 与 ``unsolved``，避免把失败样本悄悄丢掉。
* **配对比较**：所有策略使用**同一批秘密序列**，因此可以做逐局配对检验（符号检验）。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple


def percentile(values: Sequence[float], q: float) -> float:
    """线性插值分位数，``q`` 取 0~1。"""
    if not values:
        return 0.0
    s = sorted(float(v) for v in values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def histogram(values: Sequence[int]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[str(int(v))] = out.get(str(int(v)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def summarize(
    rounds: Sequence[int],
    solved: Sequence[bool],
    times_ms: Sequence[float],
    max_rounds: int,
) -> Dict[str, object]:
    """汇总单个策略在一批对局上的表现。"""
    n = len(rounds)
    solved_rounds = [r for r, ok in zip(rounds, solved) if ok]
    values = list(rounds)
    mean = sum(values) / n if n else 0.0
    var = sum((v - mean) ** 2 for v in values) / n if n else 0.0
    return {
        "games": n,
        "solved": len(solved_rounds),
        "unsolved": n - len(solved_rounds),
        "solveRate": (len(solved_rounds) / n) if n else 0.0,
        "mean": mean,
        "meanSolved": (sum(solved_rounds) / len(solved_rounds)) if solved_rounds else None,
        "median": percentile(values, 0.5),
        "p90": percentile(values, 0.9),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else 0,
        "min": min(values) if values else 0,
        "std": math.sqrt(var),
        "histogram": histogram(values),
        "meanMsPerGame": (sum(times_ms) / n) if n else 0.0,
        "totalSec": sum(times_ms) / 1000.0,
        "maxRounds": max_rounds,
    }


def binomial_two_sided(k: int, n: int) -> float:
    """符号检验的双侧精确 p 值（零假设 p=0.5）。"""
    if n <= 0:
        return 1.0
    k = min(k, n)
    m = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(0, m + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def pairwise_compare(
    results_by_key: Dict[str, List[int]],
    keys: Optional[Sequence[str]] = None,
) -> List[Dict[str, object]]:
    """逐局配对的策略两两比较。

    :param results_by_key: ``{策略key: 每局轮次列表}``，要求各列表等长且秘密序列逐局对应。
    """
    keys = list(keys) if keys is not None else list(results_by_key)
    out: List[Dict[str, object]] = []
    for a_idx in range(len(keys)):
        for b_idx in range(a_idx + 1, len(keys)):
            a, b = keys[a_idx], keys[b_idx]
            ra, rb = results_by_key[a], results_by_key[b]
            if len(ra) != len(rb):
                continue
            wins = losses = ties = 0
            diffs: List[float] = []
            for x, y in zip(ra, rb):
                diffs.append(x - y)
                if x < y:
                    wins += 1
                elif x > y:
                    losses += 1
                else:
                    ties += 1
            non_tie = wins + losses
            out.append(
                {
                    "a": a,
                    "b": b,
                    "games": len(ra),
                    "aWins": wins,
                    "bWins": losses,
                    "ties": ties,
                    "meanDiff": (sum(diffs) / len(diffs)) if diffs else 0.0,
                    "pValue": binomial_two_sided(wins, non_tie),
                    "significant": binomial_two_sided(wins, non_tie) < 0.05,
                }
            )
    return out
