# -*- coding: utf-8 -*-
"""固定循环策略：不使用反馈，按预定顺序循环填装候选组合。

用于研究“固定策略 vs 自适应策略”的差距。
"""
from __future__ import annotations

from typing import List

from core.game import CANDIDATES, SEQ_LEN, Combo
from strategies.base import History, ParamSpec, Strategy


class FixedCycleStrategy(Strategy):
    key = "fixed_cycle"
    name = "固定循环（不使用反馈）"
    description = (
        "按候选表顺序循环填装，每轮整体平移 step 个位置。"
        "是典型的“固定策略”，反馈信息完全不参与决策。"
    )
    tags = ("baseline", "non-adaptive", "fixed")
    params_spec = (
        ParamSpec("step", "每轮平移步长", default=SEQ_LEN, type="int", min=1, max=45,
                  help="每轮相对上一轮整体平移的候选表下标数。"),
        ParamSpec("offset0", "初始偏移", default=0, type="int", min=0, max=44,
                  help="第 1 轮起始下标。"),
    )
    max_rounds_hint = 60

    def next_guess(self, history: History) -> List[Combo]:
        self._sync(history)
        round_index = len(history)
        step = int(self.params["step"])
        offset = int(self.params["offset0"])
        n = len(CANDIDATES)
        return [CANDIDATES[(offset + round_index * step + i) % n] for i in range(SEQ_LEN)]
