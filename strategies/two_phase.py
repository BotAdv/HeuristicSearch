# -*- coding: utf-8 -*-
"""两阶段策略：先确定组合集合（及重数），再确定顺序。

阶段 A —— 集合确定
    每轮用一个**互异**的组合批次，每个位置的反馈都能判定该位置的组合是否属于支持集：

    * ``CORRECT`` / ``MISPLACED`` ⇒ 该组合属于支持集；
    * ``PARTIAL`` / ``WRONG``     ⇒ 该组合不属于支持集。

    若 ``count_policy = "exact"``，还会用**常数序列** ``(c, c, …, c)`` 探测已发现组合的精确重数：
    常数序列下 ``CORRECT`` 的个数恰好等于 ``c`` 在秘密序列中的重数。

阶段 B —— 顺序确定
    已知支持集（或多重集）后把信念限制到支持集内，再做逐位置消除与安置：
    若某组合的精确重数已被全部安置，就把它从所有未定位置中删掉。

这个策略正是用户提到的“先确定组合集合，再确定顺序”范式，用来与混合策略对照。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from core.game import CANDIDATES, SECRET_DISTINCT, SEQ_LEN, Combo, constant_sequence
from strategies.base import History, ParamSpec, Strategy
from strategies.belief import IN, OUT, UNKNOWN, PositionBelief, SupportTracker


class TwoPhaseStrategy(Strategy):
    key = "two_phase"
    name = "两阶段：先定支持集再定顺序"
    description = (
        "阶段 A 用互异批次判定每个组合是否属于支持集；"
        "在“秘密组合互异”规则下，支持集大小为 10 且每个组合重数恒为 1，"
        "因此一旦发现 10 个支持集成员即可提前结束，无需任何常数序列探针。"
        "阶段 B 在已知集合内做“已安置值全局删除 + 轮内互异指派”。"
        "该范式不做位置与集合信息的交叉利用，因此是混合策略的对照基线。"
    )
    tags = ("two-phase", "structured", "exploration")
    params_spec = (
        ParamSpec(
            "count_policy",
            "重数确定方式",
            default="auto_exact",
            type="choice",
            choices=["auto_exact", "exact", "skip"],
            help="auto_exact：若规则本身保证组合互异则直接取重数 1，否则用常数序列探针；"
                 "exact：总是用常数序列探针（互异规则下每轮只能测 1 个组合，很慢）；"
                 "skip：只确定支持集。",
        ),
        ParamSpec(
            "probe_order",
            "未知组合排序",
            default="heuristic",
            type="choice",
            choices=["heuristic", "sequential"],
            help="heuristic：优先测试“仍然可能出现在更多位置”的组合。",
        ),
        ParamSpec("assume_distinct", "假定秘密组合互异", default=SECRET_DISTINCT, type="bool",
                  help="当前规则下应为 True（重数恒为 1，无需常数探针）；"
                       "仅在用规则外秘密做对照实验时关闭。"),
        ParamSpec("lock_known_positions", "已锁定位置沿用原值", default=True, type="bool",
                  help="True：阶段 A 把已经确定的位置继续填它的唯一候选（更直观，人工逐步猜测模式更友好）；"
                       "False：把这些位置也拿来试探新组合，每轮能多分类几个组合（沙盒里略快）。"),
    )
    max_rounds_hint = 40

    # ------------------------------------------------------------------ 状态
    def reset(self) -> None:
        super().reset()
        unique = bool(self.params["assume_distinct"])
        self.assume_distinct = unique
        self.belief = PositionBelief(global_prune=True, unique_values=unique)
        self.support = SupportTracker(unique_values=unique)
        #: auto_exact 在互异规则下等价于“直接取重数 1”，不需要任何探针
        policy = str(self.params["count_policy"])
        if policy == "auto_exact":
            self.count_mode = "exact" if not unique else "skip"
        else:
            self.count_mode = policy
        self.phase = "A"
        self.used: Dict[Combo, int] = {c: 0 for c in CANDIDATES}
        self._pending_probe: List[Optional[Combo]] = []
        self.phase_a_rounds = 0

    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        self.belief.observe(guess, feedback)
        probe = self._pending_probe.pop(0) if self._pending_probe else None
        if self.phase != "A":
            return
        if probe is not None:
            # 常数探针：CORRECT 的个数即精确重数
            self.support.observe(guess, feedback, exact_counts=True)
        else:
            # 逐位置分类：CORRECT/MISPLACED ⇒ 属于支持集；PARTIAL/WRONG ⇒ 不属于
            self.support.observe(guess, feedback, exact_counts=False)

    # ------------------------------------------------------------------ 主循环
    def compute_guess(self, history: History) -> List[Combo]:
        if self.phase == "A" and self._phase_a_done():
            self._enter_phase_b()
        if self.phase == "A":
            self.phase_a_rounds += 1
            guess = self._guess_phase_a()
        else:
            guess = self._guess_phase_b()
        for c in guess:
            self.used[c] = self.used.get(c, 0) + 1
        return guess

    # ------------------------------------------------------------------ 解释钩子
    def explain_phase(self) -> Optional[str]:
        return self.phase

    def explain_criterion(self) -> str:
        return "批次分类" if self.phase == "A" else "指派"

    def explain_extra_fields(self) -> Dict[str, Any]:
        #: two_phase 特有的展示字段：阶段 A 已经进行了几轮（前端与复盘报告都会用）
        return {"phaseARounds": self.phase_a_rounds}

    # ------------------------------------------------------------------ 阶段 A
    def _phase_a_done(self) -> bool:
        if all(len(self.belief.sets[i]) == 1 for i in range(SEQ_LEN)):
            return True
        # 互异规则：支持集恰好 seq_len 个元素，一旦已经证实这么多成员就已探全
        if self.support.unique_values and len(self.support.known_values()) >= self.support.seq_len:
            return True
        if self.count_mode == "exact":
            return self.support.support_complete()
        return not self.support.unknown()

    def _guess_phase_a(self) -> List[Combo]:
        support = self.support
        # 1) 优先补完已发现组合的精确重数（互异规则下此分支不会触发）
        if self.count_mode == "exact":
            pending = support.pending_count()
            if pending:
                ranked = sorted(
                    pending,
                    key=lambda c: (-self.belief.count_lower.get(c, 0), self.used.get(c, 0), self.rng.random()),
                )
                c = ranked[0]
                self._pending_probe.append(c)
                return constant_sequence(c, SEQ_LEN)

        # 2) 批量分类未知组合
        unknown = support.unknown()
        if not unknown:
            # 防御性分支：理论上前面已经判定阶段结束
            self._enter_phase_b()
            return self._guess_phase_b()

        if self.params["probe_order"] == "heuristic":
            ranked = sorted(
                unknown,
                key=lambda c: (
                    -sum(1 for i in range(SEQ_LEN) if c in self.belief.sets[i]),
                    self.used.get(c, 0),
                    c,
                ),
            )
        else:
            ranked = list(unknown)
        chosen = ranked[:SEQ_LEN]
        self._pending_probe.append(None)
        if self.explain:
            self._explain_extra["batch"] = {
                "chosen": list(chosen),
                "unknownTotal": len(unknown),
                "rankedTop": [
                    {
                        "combo": c,
                        "coverage": sum(1 for i in range(SEQ_LEN) if c in self.belief.sets[i]),
                        "used": self.used.get(c, 0),
                    }
                    for c in ranked[:SEQ_LEN]
                ],
            }
        return self._assemble_batch(chosen)

    def _assemble_batch(self, chosen: List[Combo]) -> List[Combo]:
        guess: List[Optional[Combo]] = [None] * SEQ_LEN
        keep_locked = bool(self.params["lock_known_positions"])
        # 0) 已经锁定的位置：默认继续沿用它唯一的候选（白拿一个 CORRECT、也更直观）；
        #    关闭后这些位置也参与试探，每轮能多分类几个组合（实测沙盒里略快）。
        if keep_locked:
            for i in range(SEQ_LEN):
                if len(self.belief.sets[i]) == 1:
                    guess[i] = next(iter(self.belief.sets[i]))
                    if self._explain_reasons:
                        self._explain_reasons[i] = (
                            f"已锁定：本位置候选集只剩 {guess[i]} 这一个，继续沿用（白拿一个 CORRECT）"
                        )
        placed = {c for c in guess if c is not None}
        # 1) 待分类批次优先分配给“还没锁定、且候选集合允许该组合”的位置
        avail = [c for c in chosen if c not in placed]
        order = sorted(
            (i for i in range(SEQ_LEN) if guess[i] is None),
            key=lambda i: (len(self.belief.sets[i]), i),
        )
        for i in order:
            if not avail:
                break
            hit = next((k for k, c in enumerate(avail) if c in self.belief.sets[i]), None)
            k = 0 if hit is None else hit
            guess[i] = avail.pop(k)
            if self._explain_reasons:
                self._explain_reasons[i] = (
                    f"批次分类：{guess[i]} 还没判定过是否属于支持集，且它仍在本位置候选集内"
                    if hit is not None
                    else f"批次分类：本位置候选集内已无剩余批次组合，改派批次里的 {guess[i]}"
                )
        for i in range(SEQ_LEN):
            if guess[i] is None:
                guess[i] = self._pad_value(i, avoid=[c for c in guess if c is not None])
                if self._explain_reasons:
                    self._explain_reasons[i] = (
                        f"填位：本轮待分类批次不到 10 个，从本位置候选集里取最少用过的 {guess[i]}"
                    )
        # 防止退化为常数序列（会被误解析为重数探针）
        if len(set(guess)) == 1:
            only = guess[0]
            for i in range(SEQ_LEN):
                alt = [c for c in sorted(self.belief.sets[i]) if c != only]
                if alt:
                    guess[i] = self.rng.choice(alt)
                    if self._explain_reasons:
                        self._explain_reasons[i] = "防退化：避免整轮同一个组合，改成等价的替代探测"
                    break
            else:
                alts = [c for c in CANDIDATES if c != only]
                guess[0] = self.rng.choice(alts)
        return [c for c in guess if c is not None]

    def _pad_value(self, i: int, avoid: Sequence[Combo] = ()) -> Combo:
        """为凑满 10 个位置选一个填位组合：优先落在该位置候选集内、且更少被用过的。"""
        pool = sorted(self.belief.sets[i]) or list(CANDIDATES)
        if avoid:
            trimmed = [c for c in pool if c not in set(avoid)]
            if trimmed:
                pool = trimmed
        in_support = [c for c in pool if self.support.status.get(c) == IN]
        if in_support:
            return min(in_support, key=lambda c: (self.used.get(c, 0), self.rng.random()))
        unknown = [c for c in pool if self.support.status.get(c) == UNKNOWN]
        if unknown:
            return min(unknown, key=lambda c: (self.used.get(c, 0), self.rng.random()))
        return min(pool, key=lambda c: (self.used.get(c, 0), self.rng.random()))

    # ------------------------------------------------------------------ 阶段 B
    def _enter_phase_b(self) -> None:
        self.phase = "B"
        allowed = set(self.support.known_values())
        if allowed:
            self.belief.restrict_to(allowed)
        # 阶段 A 的探针队列不再需要
        self._pending_probe = [None] * len(self._pending_probe)

    def _remaining_counts(self) -> Dict[Combo, int]:
        """每个组合还剩多少个副本没有被“已确定的位置”消耗掉。"""
        remaining: Dict[Combo, int] = {}
        if self.support.unique_values:
            # 规则保证：支持集内每个组合重数恒为 1
            for c in self.support.known_values():
                remaining[c] = 1 - self.belief.count_lower.get(c, 0)
        elif self.count_mode == "exact":
            for c in self.support.known_values():
                total = self.support.count.get(c) or 0
                remaining[c] = total - self.belief.count_lower.get(c, 0)
        else:
            return remaining
        # 重数已用完的组合不可能出现在其它位置
        for c, r in remaining.items():
            if r <= 0:
                self.belief.exclude(c)
        return remaining

    def _guess_phase_b(self) -> List[Combo]:
        remaining = self._remaining_counts()
        guess: List[Optional[Combo]] = [None] * SEQ_LEN
        budget = dict(remaining)
        used_in_round: set = set()
        for i in range(SEQ_LEN):
            if len(self.belief.sets[i]) == 1:
                guess[i] = next(iter(self.belief.sets[i]))
                used_in_round.add(guess[i])
                if self._explain_reasons:
                    self._explain_reasons[i] = (
                        f"已锁定：本位置候选集只剩 {guess[i]} 这一个，直接沿用"
                    )
        order = sorted(
            (i for i in range(SEQ_LEN) if guess[i] is None),
            key=lambda i: (len(self.belief.sets[i]), i),
        )
        for i in order:
            pool = sorted(self.belief.sets[i])
            if not pool:
                pool = list(CANDIDATES)
            # 互异规则下，一轮里重复同一个组合毫无意义（它只能命中一个位置）
            fresh = [c for c in pool if c not in used_in_round]
            if fresh:
                pool = fresh
            with_budget = [c for c in pool if budget.get(c, 0) > 0]
            if with_budget:
                best = max(budget.get(c, 0) for c in with_budget)
                tied = [c for c in with_budget if budget.get(c, 0) == best]
                pick = min(tied, key=lambda c: (self.used.get(c, 0), self.rng.random()))
                budget[pick] = budget.get(pick, 0) - 1
                if self._explain_reasons:
                    self._explain_reasons[i] = (
                        f"指派：{pick} 已在支持集内且还有副本未安置，"
                        f"本位置候选集 {len(self.belief.sets[i])} 个里优先安置它"
                    )
            else:
                pick = min(pool, key=lambda c: (self.used.get(c, 0), self.rng.random()))
                if self._explain_reasons:
                    # 区分两种退化原因：候选集里还有余量的组合被本轮其它位置先用掉了，
                    # 还是本位置候选集里根本不存在“尚有未安置副本”的组合。
                    occupied = [
                        c
                        for c in sorted(self.belief.sets[i])
                        if c in used_in_round and budget.get(c, 0) > 0
                    ]
                    if occupied:
                        self._explain_reasons[i] = (
                            f"退化：候选集里还有余量的 {'、'.join(str(c) for c in occupied)} 本轮已用于其它位置，"
                            f"只能重复取最少用过的 {pick}"
                        )
                    else:
                        self._explain_reasons[i] = (
                            "退化：候选集内已无“尚有未安置副本”的组合，"
                            f"只能取候选集里最少用过的 {pick}"
                        )
            guess[i] = pick
            used_in_round.add(pick)
        if self.explain:
            self._explain_extra["budget"] = {
                "remainingBefore": {str(c): remaining.get(c, 0) for c in self.support.known_values()},
                "remainingOfSupport": {str(c): budget.get(c, 0) for c in self.support.known_values()},
                "usedInRound": [str(c) for c in used_in_round],
            }
        return [c for c in guess if c is not None]

    # ------------------------------------------------------------------ 诊断
    def diagnostics(self) -> Dict[str, object]:
        return {
            "phase": self.phase,
            "phaseARounds": self.phase_a_rounds,
            "supportSize": len(self.support.known_values()),
            "support": [(c[0], c[1]) for c in self.support.known_values()],
            "counts": {f"{c[0]}-{c[1]}": self.support.count[c] for c in self.support.known_values()},
        }
