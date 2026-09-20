# -*- coding: utf-8 -*-
"""策略注册表。

扩展方式
--------
1. 新建 ``strategies/your_strategy.py``，继承 :class:`strategies.base.Strategy`；
2. 设置 ``key`` / ``name`` / ``description`` / ``params_spec``；
3. 把它加入下面的 ``CLASSES``。

Flask 端会自动通过 ``/api/strategies`` 暴露给前端，无需改动任何前端代码。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from strategies.adaptive_hybrid import AdaptiveHybridStrategy
from strategies.base import History, ParamSpec, Strategy
from strategies.naive_position import NaivePositionStrategy
from strategies.particle_entropy import ParticleEntropyStrategy
from strategies.position_entropy import PositionEntropyStrategy
from strategies.two_phase import TwoPhaseStrategy

#: 注册顺序即前端展示顺序。
#: 注：曾经有两个“不使用反馈”的极端基线（``random`` / ``fixed_cycle``），
#: 它们在本游戏里不可能解出，而且会在同一位置反复填同一个错误组合，
#: 对“策略对比”没有信息量，已按需求移除。
CLASSES: List[Type[Strategy]] = [
    NaivePositionStrategy,
    TwoPhaseStrategy,
    PositionEntropyStrategy,
    AdaptiveHybridStrategy,
    ParticleEntropyStrategy,
]

REGISTRY: Dict[str, Type[Strategy]] = {cls.key: cls for cls in CLASSES}

#: 建议的默认对比组合
DEFAULT_PLAN_KEYS: List[str] = [
    "naive_position",
    "two_phase",
    "position_entropy",
    "adaptive_hybrid",
    "particle_entropy",
]


def get_strategy_class(key: str) -> Type[Strategy]:
    if key not in REGISTRY:
        raise KeyError(f"未知策略：{key}。可用策略：{sorted(REGISTRY)}")
    return REGISTRY[key]


def create(key: str, seed: Optional[int] = None, **params: Any) -> Strategy:
    """按 key 构造策略实例。"""
    cls = get_strategy_class(key)
    # 只传入该类声明过的参数，避免把无关 kwargs 注入
    allowed = {spec.name for spec in cls.params_spec}
    clean = {k: v for k, v in params.items() if k in allowed}
    return cls(seed=seed, **clean)


def catalog() -> List[Dict[str, Any]]:
    """返回全部策略的元信息，供前端渲染。"""
    return [
        {
            **cls.catalog_entry(),
            "default": cls.key in DEFAULT_PLAN_KEYS,
        }
        for cls in CLASSES
    ]


__all__ = [
    "Strategy",
    "ParamSpec",
    "History",
    "CLASSES",
    "REGISTRY",
    "DEFAULT_PLAN_KEYS",
    "catalog",
    "create",
    "get_strategy_class",
]
