# -*- coding: utf-8 -*-
"""朴素位置独立贪心策略。

最直观的“贪心策略”基线：把 10 个位置当作互不相关的子问题，
逐位消除并贪心地选一个“最少用过”的组合。
它对 MISPLACED 只做最小利用（知道 ``g_i != x_i``），不做全局推理。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from core.game import CANDIDATES, SECRET_DISTINCT, SEQ_LEN, Combo
from strategies.base import History, ParamSpec, Strategy
from strategies.belief import PositionBelief


class NaivePositionStrategy(Strategy):
    key = "naive_position"
    name = "朴素位置独立贪心"
    description = (
        "把每个位置独立处理：CORRECT 锁定、MISPLACED 仅排除该组合、"
        "PARTIAL/WRONG 进一步缩小共享关系。每轮选“全局使用次数最少”的组合，"
        "属于典型的贪心策略，也是理解本游戏难点的入门基线。"
    )
    tags = ("greedy", "baseline")
    params_spec = (
        ParamSpec(
            "global_prune",
            "启用全局剪枝",
            default=False,
            type="bool",
            help="开启后，PARTIAL/WRONG 判定的组合会从所有位置删除（更强但已不属于“朴素”范畴）。",
        ),
        ParamSpec(
            "pick_mode",
            "选值方式",
            default="least_used",
            type="choice",
            choices=["least_used", "random", "first"],
            help="每轮在每个位置的候选集合中如何取值。",
        ),
        ParamSpec(
            "assume_distinct",
            "假定秘密组合互异",
            default=SECRET_DISTINCT,
            type="bool",
            help="当前规则下应为 True；仅当用规则外的“允许重复”秘密做对照实验时才关闭。",
        ),
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

    def _pick(self, i: int) -> Combo:
        cands = sorted(self.belief.sets[i])
        mode = self.params["pick_mode"]
        if len(cands) == 1:
            return cands[0]
        if mode == "random":
            return self.rng.choice(cands)
        if mode == "first":
            return cands[0]
        best = min(self.used[c] for c in cands)
        tied = [c for c in cands if self.used[c] == best]
        return self.rng.choice(tied)

    def next_guess(self, history: History) -> List[Combo]:
        self._sync(history)
        guess: List[Combo] = []
        for i in range(SEQ_LEN):
            c = self._pick(i)
            guess.append(c)
            self.used[c] = self.used.get(c, 0) + 1
        return guess
