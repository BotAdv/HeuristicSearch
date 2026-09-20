# -*- coding: utf-8 -*-
"""共享的信念状态工具：逐位置候选集合 + 全局支持集分类。

这些工具被多个策略复用，避免重复实现同一套（容易出错的）推理。

核心推理（全部可证明保持 ``x_i`` 不被误删）
------------------------------------------
对第 i 位观察到的反馈 ``f``：

* ``CORRECT``  : ``x_i == g_i``，位置已确定；同时 ``g_i`` 属于支持集。
* ``MISPLACED``: ``g_i != x_i``，且 ``g_i`` 出现在其它位置 ⇒ ``g_i`` 属于支持集。
* ``PARTIAL``  : ``g_i != x_i``，``g_i`` **不属于**支持集（否则会判 MISPLACED），
  且 ``x_i`` 与 ``g_i`` 至少共享一个对象。
* ``WRONG``    : ``g_i != x_i``，``g_i`` 不属于支持集，且 ``x_i`` 与 ``g_i`` 无共享对象。

因此 ``PARTIAL`` / ``WRONG`` 可以**全局**地把 ``g_i`` 从所有位置的候选中删除
（被称为 ``global_prune``），这是本游戏里最强的一条剪枝。
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Set

from core.game import (
    CANDIDATES,
    CORRECT,
    MISPLACED,
    PARTIAL,
    SECRET_DISTINCT,
    SEQ_LEN,
    WRONG,
    Combo,
    shares,
)

UNKNOWN = "U"
IN = "IN"
OUT = "OUT"

#: 组合 -> 下标位掩码（用于 O(1) 位运算统计共享关系）
COMBO_BIT = {c: (1 << i) for i, c in enumerate(CANDIDATES)}
#: 组合 -> “与它至少共享一个对象”的所有组合的位掩码（含自身）
NEIGHBOR_MASK = {}
for _c in CANDIDATES:
    _m = 0
    for _d in CANDIDATES:
        if shares(_c, _d):
            _m |= COMBO_BIT[_d]
    NEIGHBOR_MASK[_c] = _m


def mask_of(values) -> int:
    m = 0
    for v in values:
        m |= COMBO_BIT[v]
    return m


def shannon(probs: Iterable[float]) -> float:
    total = 0.0
    for p in probs:
        if p > 1e-15:
            total -= p * math.log2(p)
    return total


class PositionBelief:
    """逐位置候选集合，支持可选的全局剪枝与“秘密组合互异”传播。

    ``unique_values=True`` 时启用新规则带来的推理：每个属于支持集的组合重数恒为 1，
    因此只要某个组合被确定“落在某个位置”（观察到 CORRECT 或某位置候选集收缩为单元素），
    它就可以从所有其它位置的候选集合中删除。
    """

    def __init__(
        self,
        candidates: Sequence[Combo] = tuple(CANDIDATES),
        seq_len: int = SEQ_LEN,
        global_prune: bool = False,
        track_support: bool = True,
        unique_values: Optional[bool] = None,
    ) -> None:
        self.seq_len = seq_len
        self.global_prune = global_prune
        self.track_support = track_support
        self.unique_values = SECRET_DISTINCT if unique_values is None else unique_values
        self.domain: Set[Combo] = set(candidates)
        self.sets: List[Set[Combo]] = [set(self.domain) for _ in range(seq_len)]
        self.status: Dict[Combo, str] = {c: UNKNOWN for c in self.domain}
        self.out: Set[Combo] = set()
        self.in_: Set[Combo] = set()
        #: 由 CORRECT 观测到的下界重数（互异规则下即“已安置”）
        self.count_lower: Dict[Combo, int] = {c: 0 for c in self.domain}
        self._mask_cache: Dict[int, int] = {}

    # ------------------------------------------------------------ 查询
    def size(self, i: int) -> int:
        return len(self.sets[i])

    def mask(self, i: int) -> int:
        """位置 i 候选集合的位掩码（带缓存，用于快速统计共享关系）。"""
        cached = self._mask_cache.get(i)
        if cached is None:
            cached = mask_of(self.sets[i])
            self._mask_cache[i] = cached
        return cached

    def solved_position(self, i: int) -> Optional[Combo]:
        if len(self.sets[i]) == 1:
            return next(iter(self.sets[i]))
        return None

    def total_candidates(self) -> int:
        return sum(len(s) for s in self.sets)

    def entropy(self, i: int) -> float:
        s = len(self.sets[i])
        return math.log2(s) if s > 0 else 0.0

    # ------------------------------------------------------------ 更新
    def _mark_in(self, c: Combo) -> None:
        if self.track_support and self.status.get(c) != IN:
            self.status[c] = IN
            self.in_.add(c)

    def _mark_out(self, c: Combo) -> None:
        if not self.track_support or self.status.get(c) == IN:
            # 与既有 IN 冲突（正常游戏流程不会发生）：以已证实的 IN 为准
            return
        self.status[c] = OUT
        self.out.add(c)

    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        self._mask_cache.clear()
        for i, (g, f) in enumerate(zip(guess, feedback)):
            if f == CORRECT:
                self.sets[i] = {g}
                self._mark_in(g)
                self.count_lower[g] = self.count_lower.get(g, 0) + 1
            elif f == MISPLACED:
                self.sets[i].discard(g)
                self._mark_in(g)
            elif f == PARTIAL:
                self._mark_out(g)
                cur = {c for c in self.sets[i] if c != g and shares(c, g)}
                self._replace(i, cur)
            elif f == WRONG:
                self._mark_out(g)
                cur = {c for c in self.sets[i] if not shares(c, g)}
                self._replace(i, cur)
        if self.global_prune and self.out:
            for i in range(self.seq_len):
                if len(self.sets[i]) > 1:
                    cur = self.sets[i] - self.out
                    self._replace(i, cur)
        if self.unique_values:
            self.propagate_unique()

    # ------------------------------------------------------- 互异约束传播
    def placed_values(self) -> Set[Combo]:
        """已确定“落在某个具体位置”的组合。

        两条来源都是可靠的：观察到了 CORRECT（此时该位置候选集已被置为单元素），
        或候选集合自行收缩为单元素。
        """
        return {next(iter(s)) for s in self.sets if len(s) == 1}

    def propagate_unique(self) -> int:
        """把“已安置组合”从所有未定位置的候选中删除，迭代到不动点。

        返回被删除的候选总个数。依据是“秘密序列的组合互不重复”，因此一个组合
        只能出现一次：它已经落在某个位置，就不可能再出现在别的位置。
        """
        removed = 0
        changed = True
        guard = 0
        while changed and guard <= self.seq_len:
            changed = False
            guard += 1
            for c in self.placed_values():
                if self.track_support and self.status.get(c) != IN:
                    self.status[c] = IN
                    self.in_.add(c)
                for j in range(self.seq_len):
                    if len(self.sets[j]) > 1 and c in self.sets[j]:
                        self.sets[j].discard(c)
                        removed += 1
                        changed = True
        if removed:
            self._mask_cache.clear()
            # 传播后可能出现新的“未定位置只剰一个候选”，同时清扫已被判定出局的组合
            if self.out:
                for i in range(self.seq_len):
                    if len(self.sets[i]) > 1:
                        cur = self.sets[i] - self.out
                        if cur:
                            self.sets[i] = cur
        return removed

    def _replace(self, i: int, new_set: Set[Combo]) -> None:
        if not new_set:
            # 兜底：理论上不会发生（推理是可靠的），避免策略崩溃
            return
        self.sets[i] = new_set

    def restrict_to(self, allowed: Iterable[Combo]) -> None:
        """把所有位置的候选限制在给定集合内（用于“支持集已知”后的第二阶段）。"""
        self._mask_cache.clear()
        allowed = set(allowed)
        for i in range(self.seq_len):
            if len(self.sets[i]) == 1:
                continue
            cur = self.sets[i] & allowed
            if cur:
                self.sets[i] = cur

    def exclude(self, value: Combo, protect_singleton: bool = True) -> None:
        """把某个组合从所有位置的候选中删除。

        仅在**已证实**该组合不在任何位置出现时使用（例如已计数的重数已全部安置）。
        """
        self._mask_cache.clear()
        for i in range(self.seq_len):
            if protect_singleton and len(self.sets[i]) == 1:
                continue
            if value in self.sets[i] and len(self.sets[i]) > 1:
                self.sets[i].discard(value)

    def union(self, i: int) -> Set[Combo]:
        return self.sets[i]


class SupportTracker:
    """记录每个组合是否属于秘密序列的支持集，以及（可选）精确重数。

    在“秘密组合互异”规则下，任何属于支持集的组合重数恒为 1，
    因此 ``unique_values=True`` 时一旦 ``mark_in`` 就能直接得到精确重数，
    无需再用常数序列探针去测（那会很贵）。
    """

    def __init__(
        self,
        candidates: Sequence[Combo] = tuple(CANDIDATES),
        seq_len: int = SEQ_LEN,
        unique_values: Optional[bool] = None,
    ) -> None:
        self.seq_len = seq_len
        self.unique_values = SECRET_DISTINCT if unique_values is None else unique_values
        self.status: Dict[Combo, str] = {c: UNKNOWN for c in candidates}
        self.count: Dict[Combo, Optional[int]] = {c: None for c in candidates}
        self.combos: List[Combo] = list(candidates)

    def mark_in(self, c: Combo, count: Optional[int] = None) -> None:
        self.status[c] = IN
        if self.unique_values:
            self.count[c] = 1          # 规则保证：支持集内每个组合只出现一次
        elif count is not None:
            self.count[c] = count
        elif self.count[c] is None:
            self.count[c] = None

    def mark_out(self, c: Combo) -> None:
        if self.status[c] != IN:
            self.status[c] = OUT

    def unknown(self) -> List[Combo]:
        return [c for c in self.combos if self.status[c] == UNKNOWN]

    def pending_count(self) -> List[Combo]:
        return [c for c in self.combos if self.status[c] == IN and self.count[c] is None]

    def known_values(self) -> List[Combo]:
        return [c for c in self.combos if self.status[c] == IN]

    def counted_total(self) -> Optional[int]:
        """若所有已判定属于支持集的组合都已计数，返回总数，否则 None。"""
        total = 0
        for c in self.known_values():
            if self.count[c] is None:
                return None
            total += int(self.count[c])
        return total

    def support_complete(self) -> bool:
        """支持集是否已完全确定：已计数值之和等于序列长度。"""
        return self.counted_total() == self.seq_len

    def observe(self, guess: Sequence[Combo], feedback: Sequence[str], exact_counts: bool = True) -> None:
        """通用观察：每个位置的反馈都能判定该位置的 ``g_i`` 是否属于支持集。

        这一步与位置是否互异无关：
        * ``CORRECT`` / ``MISPLACED`` ⇒ ``g_i`` 属于支持集；
        * ``PARTIAL`` / ``WRONG``     ⇒ ``g_i`` 不属于支持集。
        若这一轮是常数序列 ``(c, c, ..., c)``，则 CORRECT 的个数就是 ``c`` 的精确重数。
        """
        values = set(guess)
        is_constant = exact_counts and len(values) == 1
        if is_constant:
            c = next(iter(values))
            if all(f in (CORRECT, MISPLACED) for f in feedback):
                self.mark_in(c, sum(1 for f in feedback if f == CORRECT))
                return
            self.mark_out(c)
            return
        for g, f in zip(guess, feedback):
            if f in (CORRECT, MISPLACED):
                self.mark_in(g)
            else:
                self.mark_out(g)
