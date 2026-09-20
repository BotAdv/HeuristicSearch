# -*- coding: utf-8 -*-
"""逐位置信息熵贪心策略。

对每个位置独立地估计 4 种反馈结果出现的概率，选择让该位置反馈分布**熵最大**的组合，
等价于“让这一位的信息增益最大”的贪心近似。

概率模型（位置近似独立）
------------------------
对位置 ``i`` 的候选组合 ``c``（记 ``m = |C_i|``）：

* ``p_eq  = 1/m``                                  —— 假设 ``C_i`` 内近似均匀
* ``q(c)  = 1 - Π_{j≠i} (1 - 1/|C_j| ⁞ c ∈ C_j)`` —— ``c`` 在别处出现的概率
* ``p_mis = (1 - p_eq) · q(c)``
* 其余概率按 ``C_i`` 中与 ``c`` 共享元素的组合占比 ``r`` 拆给 PARTIAL / WRONG

该模型的优点是能自动反映“全局剪枝”的效果：一旦 ``c`` 被判为不属于支持集，
``q(c)`` 会归零，熵随之下降，策略自然转向探测其它组合。
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

from core.game import CANDIDATES, SECRET_DISTINCT, SEQ_LEN, Combo
from strategies.base import History, ParamSpec, Strategy
from strategies.belief import NEIGHBOR_MASK, PositionBelief, shannon


def outcome_distribution(belief: PositionBelief, i: int, c: Combo) -> Tuple[float, float, float, float]:
    """返回 ``(p_correct, p_misplaced, p_partial, p_wrong)``。"""
    ci = belief.sets[i]
    m = len(ci)
    if m == 0:
        return (0.0, 0.0, 0.0, 1.0)
    p_eq = 1.0 / m
    prod = 1.0
    for j in range(belief.seq_len):
        if j == i:
            continue
        cj = belief.sets[j]
        mj = len(cj)
        if mj and c in cj:
            prod *= 1.0 - 1.0 / mj
    q = 1.0 - prod
    p_mis = (1.0 - p_eq) * q
    rest = max(0.0, 1.0 - p_eq - p_mis)
    if m > 1:
        # 位运算统计“与 c 共享元素”的候选个数（含 c 自身，所以减 1）
        share_n = ((NEIGHBOR_MASK[c] & belief.mask(i)).bit_count()) - 1
        r = max(0.0, share_n) / float(m - 1)
    else:
        r = 0.0
    p_par = rest * r
    p_wro = rest - p_par
    return (p_eq, p_mis, p_par, p_wro)


def entropy_score(belief: PositionBelief, i: int, c: Combo) -> float:
    return shannon(outcome_distribution(belief, i, c))


def split_gain(belief: PositionBelief, i: int, c: Combo) -> float:
    """用 ``c`` 去探测位置 ``i`` 时的“二等分收益”（bit）。

    若 ``c`` 不在支持集中，反馈只会是 PARTIAL 或 WRONG，而两者恰好按
    “与 ``c`` 共享对象 / 不共享对象”把候选集合切成两半。切割越均匀，
    本轮获得的信息越多（最多 1 bit）。若 ``c`` 已在支持集中，则反馈是
    CORRECT / MISPLACED，对 ``x_i`` 几乎没有信息。

    由于“尚未分类的组合属于支持集”的先验对所有未测试组合近似相同，
    因此该指标可以把“选哪个组合去探测”这件事近似解耦出来。
    """
    m = len(belief.sets[i])
    if m <= 1:
        return 0.0
    shared = (NEIGHBOR_MASK[c] & belief.mask(i)).bit_count() - 1  # 排除 c 自身
    other = m - 1 - shared
    if shared <= 0 or other <= 0:
        return 0.0
    p = shared / float(m - 1)
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))


def best_split_value(
    belief: PositionBelief,
    i: int,
    used: Dict[Combo, int],
    rng,
    untested_only: bool = False,
    avoid: Sequence[Combo] = (),
) -> Combo:
    """选择“切割候选集合最均匀”的组合。"""
    pool = sorted(belief.sets[i]) or list(CANDIDATES)
    fresh = [c for c in pool if belief.status.get(c) == "U"]
    if untested_only and fresh:
        pool = fresh
    if avoid:
        avoid_set = set(avoid)
        trimmed = [c for c in pool if c not in avoid_set]
        if trimmed:
            pool = trimmed
    gains = {c: split_gain(belief, i, c) for c in pool}
    best = max(gains.values())
    tied = [c for c in pool if gains[c] >= best - 1e-12]
    return min(tied, key=lambda c: (used.get(c, 0), rng.random()))


def pick_value(
    belief: PositionBelief,
    i: int,
    used: Dict[Combo, int],
    rng,
    mode: str,
    avoid: Sequence[Combo] = (),
) -> Combo:
    """统一入口：``entropy`` / ``split`` / ``least_used``。

    ``avoid`` 用于“同一轮内尽量使用互异组合”：一轮里把 10 个位置分给 10 个**不同**的
    未测试组合，能在一轮内完成最多 10 次支持集分类，这在本游戏中非常关键。
    """
    if mode == "split":
        return best_split_value(belief, i, used, rng, untested_only=True, avoid=avoid)
    if mode == "least_used":
        pool = sorted(belief.sets[i]) or list(CANDIDATES)
        if len(pool) == 1:
            return pool[0]
        if avoid:
            avoid_set = set(avoid)
            trimmed = [c for c in pool if c not in avoid_set]
            if trimmed:
                pool = trimmed
        return min(pool, key=lambda c: (used.get(c, 0), rng.random()))
    return best_entropy_value(belief, i, used, rng, avoid=avoid)


def best_entropy_value(
    belief: PositionBelief,
    i: int,
    used: Dict[Combo, int],
    rng,
    candidates: Sequence[Combo] = None,
    avoid: Sequence[Combo] = (),
) -> Combo:
    """在位置 ``i`` 的候选集合中选熵最大的组合；同分时偏向“更少用过”的组合。"""
    pool = list(candidates) if candidates is not None else sorted(belief.sets[i])
    if not pool:
        pool = sorted(belief.sets[i]) or list(CANDIDATES)
    if avoid:
        avoid_set = set(avoid)
        trimmed = [c for c in pool if c not in avoid_set]
        if trimmed:
            pool = trimmed
    scores = {c: entropy_score(belief, i, c) for c in pool}
    best = max(scores.values())
    tied = [c for c in pool if scores[c] >= best - 1e-12]
    return min(tied, key=lambda c: (used.get(c, 0), rng.random()))


class PositionEntropyStrategy(Strategy):
    key = "position_entropy"
    name = "逐位置信息增益贪心"
    description = (
        "按位置独立决定取值，并提供三种取值准则："
        "split（默认）优先选能把该位置候选集合“二等分”的未测试组合，"
        "entropy 选使该位置反馈分布熵最大的组合，least_used 选全局最少用过的组合。"
        "配合 global_prune（把 PARTIAL/WRONG 判定的组合从所有位置删除）后，"
        "支持集探测与位置定位被自然地统一到一个准则里，是实测最优的策略。"
    )
    tags = ("greedy", "entropy", "adaptive")
    params_spec = (
        ParamSpec("global_prune", "启用全局剪枝", default=True, type="bool",
                  help="PARTIAL/WRONG 判定的组合会从所有位置删除。"),
        ParamSpec("pick_mode", "取值准则", default="split", type="choice",
                  choices=["split", "entropy", "least_used"],
                  help="split：优先选能把候选集合二等分的未测试组合（探测收益最大）；"
                       "entropy：最大化该位置反馈分布熵；least_used：选全局使用次数最少的组合。"),
        ParamSpec("explore_floor", "强制探索位置数", default=0, type="int", min=0, max=10,
                  help="每轮强行留出若干位置去探测尚未分类的组合，其余位置走所选准则。"),
        ParamSpec("assume_distinct", "假定秘密组合互异", default=SECRET_DISTINCT, type="bool",
                  help="当前规则下应为 True（启用“已安置值全局传播”）；"
                       "仅在用规则外秘密做对照实验时关闭。"),
    )

    def reset(self) -> None:
        super().reset()
        self.belief = PositionBelief(
            global_prune=bool(self.params["global_prune"]),
            unique_values=bool(self.params["assume_distinct"]),
        )
        self.used: Dict[Combo, int] = {c: 0 for c in CANDIDATES}

    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        self.belief.observe(guess, feedback)

    def next_guess(self, history: History) -> List[Combo]:
        self._sync(history)
        guess: List[Combo] = [None] * SEQ_LEN  # type: ignore[list-item]
        floor = int(self.params["explore_floor"])
        mode = self.params["pick_mode"]
        used = dict(self.used)
        picked: List[Combo] = []

        # 1) 已锁定的位置直接填
        free: List[int] = []
        for i in range(SEQ_LEN):
            solved = self.belief.solved_position(i)
            if solved is not None:
                guess[i] = solved
                picked.append(solved)
                used[solved] = used.get(solved, 0) + 1
            else:
                free.append(i)

        # 2) 强制探索：优先探测尚未分类的组合
        if floor > 0 and free:
            unknown = [c for c in CANDIDATES if self.belief.status.get(c) == "U"]
            ranked = sorted(
                unknown,
                key=lambda c: (-sum(1 for i in range(SEQ_LEN) if c in self.belief.sets[i]), used.get(c, 0)),
            )
            order = sorted(free, key=lambda i: (len(self.belief.sets[i]), i))
            for slot, combo in zip(order, ranked[:floor]):
                guess[slot] = combo
                picked.append(combo)
                used[combo] = used.get(combo, 0) + 1

        # 3) 其余位置走所选准则（按候选数从少到多处理，并尽量使用互异组合）
        for i in sorted(free, key=lambda k: (len(self.belief.sets[k]), k)):
            if guess[i] is None:
                c = pick_value(self.belief, i, used, self.rng, mode, avoid=picked)
                guess[i] = c
                picked.append(c)
                used[c] = used.get(c, 0) + 1

        for c in guess:
            assert c is not None
            self.used[c] = self.used.get(c, 0) + 1
        return [c for c in guess if c is not None]
