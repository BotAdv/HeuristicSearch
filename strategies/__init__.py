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
from strategies.fixed_cycle import FixedCycleStrategy
from strategies.naive_position import NaivePositionStrategy
from strategies.particle_entropy import ParticleEntropyStrategy
from strategies.position_entropy import PositionEntropyStrategy
from strategies.random_strategy import RandomStrategy
from strategies.two_phase import TwoPhaseStrategy

#: 注册顺序即前端展示顺序
CLASSES: List[Type[Strategy]] = [
    RandomStrategy,
    FixedCycleStrategy,
    NaivePositionStrategy,
    TwoPhaseStrategy,
    PositionEntropyStrategy,
    AdaptiveHybridStrategy,
    ParticleEntropyStrategy,
]

REGISTRY: Dict[str, Type[Strategy]] = {cls.key: cls for cls in CLASSES}

#: 建议的默认对比组合（去掉纯随机/纯固定这两个极端基线）
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
