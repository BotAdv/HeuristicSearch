# -*- coding: utf-8 -*-
"""随机基线策略：完全不使用反馈。

保留它的意义在于给出性能下界，并说明“无信息利用”在本游戏中不可解
（命中 45^10 空间中的唯一目标序列）。
"""
from __future__ import annotations

from typing import List

from core.game import Combo
from strategies.base import History, Strategy


class RandomStrategy(Strategy):
    key = "random"
    name = "随机基线（不使用反馈）"
    description = (
        "每个位置独立均匀随机选取候选组合，完全忽略反馈。"
        "用于给出性能下界：本游戏中它几乎不可能解出，只能作为参照。"
    )
    tags = ("baseline", "non-adaptive")
    params_spec = ()
    max_rounds_hint = 30

    def compute_guess(self, history: History) -> List[Combo]:
        return self._random_guess()

    def explain_criterion(self) -> str:
        return "随机基线（本策略完全不看反馈，每个位置独立均匀随机）"
