# -*- coding: utf-8 -*-
"""朴素位置独立贪心策略。

最直观的“贪心策略”基线：把 10 个位置当作互不相关的子问题，
逐位消除并贪心地选一个“最少用过”的组合。
它对 MISPLACED 只做最小利用（知道 ``g_i != x_i``），不做全局推理。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from core.game import CANDIDATES, SECRET_DISTINCT, SEQ_LEN, Combo
from strategies import explain
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

    def _pick(self, i: int, trace: dict = None) -> Combo:
        cands = sorted(self.belief.sets[i])
        mode = self.params["pick_mode"]
        if len(cands) == 1:
            if trace is not None:
                trace["kind"] = "locked"
            return cands[0]
        if mode == "random":
            pick = self.rng.choice(cands)
            if trace is not None:
                trace.update({"kind": "random", "pool": len(cands)})
            return pick
        if mode == "first":
            if trace is not None:
                trace.update({"kind": "first", "pool": len(cands)})
            return cands[0]
        best = min(self.used[c] for c in cands)
        tied = [c for c in cands if self.used[c] == best]
        pick = self.rng.choice(tied)
        if trace is not None:
            trace.update({"kind": "least_used", "pool": len(cands), "used": best, "tied": len(tied)})
        return pick

    def compute_guess(self, history: History) -> List[Combo]:
        guess: List[Combo] = []
        for i in range(SEQ_LEN):
            trace: dict = {}
            c = self._pick(i, trace)
            guess.append(c)
            self.used[c] = self.used.get(c, 0) + 1
            self.note(i, self._reason(i, c, trace))
        return guess

    def _reason(self, i: int, combo: Combo, trace: dict) -> str:
        m = len(self.belief.sets[i])
        kind = trace.get("kind")
        if kind == "locked":
            return explain.locked_reason(self.belief, i, combo)
        if kind == "random":
            return f"随机取值：本位置候选 {m} 个，等概率随机选一个（本策略不做信息最优性判断）"
        if kind == "first":
            return f"取候选集首项：本位置候选 {m} 个（按字典序），取 {explain.combo_label(combo)}"
        detail = f"本位置候选 {m} 个里 {explain.combo_label(combo)} 全局只用过 {trace.get('used', 0)} 次（最少"
        if trace.get("tied", 1) > 1:
            detail += f"，并列 {trace['tied']} 个等概率取一个"
        detail += "）"
        note = explain.membership_note(self.belief, combo)
        return "最少使用：" + detail + (f"；{note}" if note else "")

    def explain_criterion(self) -> str:
        return {
            "least_used": "最少使用",
            "random": "随机取值",
            "first": "取候选集首项",
        }.get(str(self.params["pick_mode"]), "位置独立贪心")
