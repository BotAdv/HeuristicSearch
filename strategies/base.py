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

    def observe(self, guess: Sequence[Combo], feedback: Sequence[str]) -> None:
        """吸收一轮新反馈。子类按需覆盖。"""

    def next_guess(self, history: History) -> List[Combo]:
        raise NotImplementedError

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
