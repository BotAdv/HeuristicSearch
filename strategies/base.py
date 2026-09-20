# -*- coding: utf-8 -*-
"""策略基类与参数声明。

策略接口（用户约定）::

    class Strategy:
        def next_guess(self, history) -> List[Tuple[int, int]]

``history`` 是 ``[(guess, feedback), ...]``，其中 ``guess`` 为长度 10 的组合元组，
``feedback`` 为长度 10 的字符串元组。策略内部可以维护自己的信念状态。

新增策略只需：
1. 在本包内新建模块，定义 ``Strategy`` 子类并设置 ``key`` / ``name`` / ``params_spec``；
2. 在 ``strategies/__init__.py`` 的 ``CLASSES`` 中注册。
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.game import CANDIDATES, SEQ_LEN, Combo, random_sequence
from strategies.explain import build_decision, fill_missing_reasons

History = List[Tuple[Tuple[Combo, ...], Tuple[str, ...]]]


@dataclass
class ParamSpec:
    """策略参数声明，前端据此自动渲染控件。"""

    name: str
    label: str
    default: Any
    type: str = "int"  # int | float | bool | choice
    min: Optional[float] = None
    max: Optional[float] = None
    step: float = 1
    choices: Optional[List[Any]] = None
    help: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Strategy:
    """所有启发式策略的基类。"""

    #: 唯一标识（用于 API / 前端路由）
    key: str = "base"
    #: 中文显示名
    name: str = "基础策略"
    #: 一句话说明
    description: str = ""
    #: 分类标签，便于分组对比
    tags: Tuple[str, ...] = ()
    #: 参数声明
    params_spec: Tuple[ParamSpec, ...] = ()
    #: 该类策略的建议轮次上限（超过即视为失败）
    max_rounds_hint: int = 120

    def __init__(self, seed: Optional[int] = None, **params: Any) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.params: Dict[str, Any] = self._resolve_params(params)
        self.reset()

    # ------------------------------------------------------------- 参数
    @classmethod
    def _resolve_params(cls, given: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for spec in cls.params_spec:
            value = given.get(spec.name, spec.default)
            try:
                if spec.type == "int":
                    value = int(value)
                elif spec.type == "float":
                    value = float(value)
                elif spec.type == "bool":
                    value = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
                elif spec.type == "choice" and spec.choices:
                    if value not in spec.choices:
                        value = spec.default
            except (TypeError, ValueError):
                value = spec.default
            if spec.type in ("int", "float"):
                if spec.min is not None:
                    value = max(spec.min, value)
                if spec.max is not None:
                    value = min(spec.max, value)
                if spec.type == "int":
                    value = int(value)
                else:
                    value = float(value)
            out[spec.name] = value
        # 允许传入未声明的额外参数（便于实验），但记录在案
        for k, v in given.items():
            if k not in out:
                out[k] = v
        return out

    # ------------------------------------------------------------- 生命周期
    def reset(self) -> None:
        """重置内部信念状态。子类覆盖时必须先调用 ``super().reset()``。"""
        self._absorbed = 0
        self._history: History = []
        # ------- 决策解释（默认关闭；打开后只在 last_decision 里留下结构化理由，
        #         不调用随机数、不改任何信念，因此不影响策略行为）
        self.explain: bool = False
        self.last_decision: Optional[Dict[str, Any]] = None
        self._explain_reasons: List[Optional[str]] = []
        self._explain_extra: Dict[str, Any] = {}
        self._explain_round: int = 0

    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        """吸收一轮新反馈。子类按需覆盖。"""

    # ------------------------------------------------------------- 主循环
    def next_guess(self, history: History) -> List[Combo]:
        """模板方法：同步历史 → 收集解释 → 交给子类的 :meth:`compute_guess`。

        子类不要再覆盖本方法，而是实现 :meth:`compute_guess`；
        这样解释钩子对**所有**策略一律生效，不会出现"某个策略忘了写解释"的情况。
        """
        self._sync(history)
        self.explain_begin(history)
        guess = self.compute_guess(history)
        self.explain_end(guess, history)
        return guess

    def compute_guess(self, history: History) -> List[Combo]:
        """子类实现：基于当前信念给出下一轮猜测。"""
        raise NotImplementedError

    # ------------------------------------------------------------- 决策解释
    def explain_begin(self, history: History) -> None:
        """开始记录本轮解释（关闭解释时直接返回）。"""
        if not self.explain:
            return
        self._explain_round = len(history) + 1
        self._explain_reasons = [None] * SEQ_LEN
        self._explain_extra = {}

    def note(self, i: int, text: str) -> None:
        """给第 ``i`` 个位置（0-based）记一条中文理由。"""
        if self.explain and 0 <= i < len(self._explain_reasons):
            self._explain_reasons[i] = text

    def note_extra(self, key: str, value: Any) -> None:
        """记一条与位置无关的额外统计（批次、预算、准则分数…）。"""
        if self.explain:
            self._explain_extra[key] = value

    def explain_phase(self) -> Optional[str]:
        """阶段性策略可返回当前阶段名（用于前端分组展示）。"""
        return None

    def explain_criterion(self) -> str:
        """通用兜底理由里使用的准则名（子类可覆盖成更具体的说法）。"""
        return "按本策略的取值准则"

    def explain_extra_fields(self) -> Dict[str, Any]:
        """子类可补充与位置无关的额外字段（合并进 last_decision 顶层）。"""
        return {}

    def explain_end(self, guess: List[Combo], history: History) -> None:
        """结束记录：把空缺理由补上，并组装 ``last_decision``。"""
        if not self.explain:
            return
        fill_missing_reasons(self, guess, self._explain_reasons, self.explain_criterion())
        extra = dict(self._explain_extra)
        extra.update(self.explain_extra_fields())
        self.last_decision = build_decision(
            self,
            guess,
            self._explain_round,
            self._explain_reasons,
            extra,
            phase=self.explain_phase(),
        )

    # ------------------------------------------------------------- 工具
    def _sync(self, history: History) -> None:
        """只把尚未吸收的轮次交给 :meth:`observe`，保证与外部 history 一致。"""
        self._history = history
        if self._absorbed > len(history):
            # 传入的 history 变短（例如被外部重置），重建状态
            self.reset()
        for guess, feedback in history[self._absorbed :]:
            self.observe(guess, feedback)
        self._absorbed = len(history)

    def _random_guess(self) -> List[Combo]:
        return random_sequence(self.rng, SEQ_LEN)

    def _pad_guess(self, partial: List[Optional[Combo]]) -> List[Combo]:
        """把含空位的位置列表补全为合法猜测。"""
        filled: List[Combo] = []
        for item in partial:
            filled.append(item if item is not None else self.rng.choice(CANDIDATES))
        return filled

    # ------------------------------------------------------------- 元信息
    @classmethod
    def catalog_entry(cls) -> Dict[str, Any]:
        return {
            "key": cls.key,
            "name": cls.name,
            "description": cls.description,
            "tags": list(cls.tags),
            "params": [p.to_dict() for p in cls.params_spec],
            "maxRoundsHint": cls.max_rounds_hint,
            "source": f"{cls.__module__}.{cls.__qualname__}",
        }
