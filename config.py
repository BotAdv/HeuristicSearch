# -*- coding: utf-8 -*-
"""全局配置。

所有可调项均可通过同名环境变量覆盖，便于在不同机器上复现实验。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
GAMES_DIR = RUNTIME_DIR / "games"
RUNS_DIR = RUNTIME_DIR / "runs"
DOC_DIR = BASE_DIR / "Doc"

# ---------------------------------------------------------------- 服务
HOST = os.environ.get("HS_HOST", "127.0.0.1")
PORT = int(os.environ.get("HS_PORT", "5000"))
DEBUG = os.environ.get("HS_DEBUG", "0") == "1"

# ---------------------------------------------------------------- 模拟限制
MAX_GAMES_PER_RUN = int(os.environ.get("HS_MAX_GAMES", "5000"))
MAX_ROUNDS_DEFAULT = int(os.environ.get("HS_MAX_ROUNDS", "40"))
MAX_ROUNDS_LIMIT = int(os.environ.get("HS_MAX_ROUNDS_LIMIT", "2000"))
MAX_STRATEGIES_PER_RUN = 12

#: 后台模拟任务的并发上限（超过则排队），避免多任务同时抢占 CPU 导致计时失真
MAX_PARALLEL_JOBS = int(os.environ.get("HS_MAX_JOBS", "2"))

# ---------------------------------------------------------------- 记忆库/文档
MEMORY_DIR = BASE_DIR / "memory"

# ---------------------------------------------------------------- 文档日志
#: 是否把对局与模拟结果写入 Doc/ 下的日志文件
DOC_LOG_ENABLED = os.environ.get("HS_DOC_LOG", "1") == "1"
#: 是否把**每一轮**的猜测与反馈明细写入 Doc/（批量模拟默认只写汇总，避免文档爆炸）
DOC_LOG_ROUNDS = os.environ.get("HS_DOC_LOG_ROUNDS", "1") == "1"
#: 单次交互最多写入的轮次明细条数
DOC_LOG_ROUND_LIMIT = int(os.environ.get("HS_DOC_LOG_ROUND_LIMIT", "400"))


def ensure_dirs() -> None:
    for d in (RUNTIME_DIR, GAMES_DIR, RUNS_DIR, DOC_DIR, DOC_DIR / "_data"):
        d.mkdir(parents=True, exist_ok=True)
