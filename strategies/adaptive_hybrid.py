# -*- coding: utf-8 -*-
"""自适应混合策略：把“集合探测”和“位置定位”交织在一轮里。

与两阶段策略的区别在于**不做严格分期**：

* 对候选集合仍然很大的位置（``|C_i| > explore_threshold``），
  该位置本轮用于**探测**——填一个尚未判定是否属于支持集的组合；
* 对候选已经收窄的位置，本轮用于**利用**——用熵贪心挑一个最可能正确的组合。

阈值本身随游戏推进自动变得“更容易触发利用”，因此策略在早期偏向探索、后期偏向定位，
这正是用户问题中“是否存在比先定集合再定顺序更优的混合策略”的候选答案。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from core.game import CANDIDATES, SECRET_DISTINCT, SEQ_LEN, Combo
from strategies.base import History, ParamSpec, Strategy
from strategies.belief import PositionBelief
from strategies.position_entropy import pick_value


class AdaptiveHybridStrategy(Strategy):
    key = "adaptive_hybrid"
    name = "自适应混合（探测/定位交织）"
    description = (
        "每轮按位置自适应分配任务：候选集合仍然很大的位置去做支持集探测，"
        "候选已收窄的位置做熵贪心定位。不做严格分期，因此集合信息与位置信息同步积累。"
    )
    tags = ("hybrid", "adaptive", "entropy")
    params_spec = (
        ParamSpec("explore_threshold", "探测阈值 |C_i|", default=1, type="int", min=1, max=45,
                  help="某位置候选数大于该值时本轮用于探测未知组合，否则用于定位。"
                       "阈值取 45 时不显式探测，退化为逐位置贪心。"),
        ParamSpec("global_prune", "启用全局剪枝", default=True, type="bool"),
        ParamSpec("max_explore", "每轮最多探测位置数", default=10, type="int", min=0, max=10,
                  help="限制单轮用于探索的位置数量，避免一直不收敛。"),
        ParamSpec("pick_mode", "定位取值准则", default="split", type="choice",
                  choices=["split", "entropy", "least_used"],
                  help="非探测位置采用哪种准则取值。"),
        ParamSpec("assume_distinct", "假定秘密组合互异", default=SECRET_DISTINCT, type="bool",
                  help="当前规则下应为 True；仅在用规则外秘密做对照实验时关闭。"),
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
        guess: List[Optional[Combo]] = [None] * SEQ_LEN
        used = dict(self.used)
        picked: List[Combo] = []

        for i in range(SEQ_LEN):
            c = self.belief.solved_position(i)
            if c is not None:
                guess[i] = c
                picked.append(c)
                used[c] = used.get(c, 0) + 1

        threshold = int(self.params["explore_threshold"])
        budget = int(self.params["max_explore"])
        # 候选数最多的位置优先用于探索（信息收益最大）
        undecided = [i for i in range(SEQ_LEN) if guess[i] is None]
        explore_slots = [i for i in undecided if len(self.belief.sets[i]) > threshold]
        explore_slots.sort(key=lambda i: (-len(self.belief.sets[i]), i))
        explore_slots = explore_slots[:budget]

        if explore_slots:
            ranked = self._rank_unknown()
            if not ranked:
                explore_slots = []
            else:
                for slot, combo in zip(explore_slots, ranked):
                    guess[slot] = combo
                    picked.append(combo)
                    used[combo] = used.get(combo, 0) + 1

        mode = self.params["pick_mode"]
        for i in sorted(undecided, key=lambda k: (len(self.belief.sets[k]), k)):
            if guess[i] is None:
                c = pick_value(self.belief, i, used, self.rng, mode, avoid=picked)
                guess[i] = c
                picked.append(c)
                used[c] = used.get(c, 0) + 1

        for c in guess:
            assert c is not None
            self.used[c] = self.used.get(c, 0) + 1
        return [c for c in guess if c is not None]

    # ------------------------------------------------------------------ 内部
    def _rank_unknown(self) -> List[Combo]:
        """按“仍然可能出现在多少位置上”降序排列尚未分类的组合。"""
        unknown = [c for c in CANDIDATES if self.belief.status.get(c) == "U"]
        if not unknown:
            return []
        cover: Dict[Combo, int] = {}
        for c in unknown:
            cover[c] = sum(1 for i in range(SEQ_LEN) if c in self.belief.sets[i])
        unknown.sort(key=lambda c: (-cover[c], self.used.get(c, 0), self.rng.random()))
        return unknown
