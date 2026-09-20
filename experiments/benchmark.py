# -*- coding: utf-8 -*-
"""命令行基准实验。

示例::

    conda activate flask_env
    python experiments/benchmark.py --games 200 --max-rounds 60
    python experiments/benchmark.py --plan two_phase,adaptive_hybrid,particle_entropy --games 50
    python experiments/benchmark.py --secret-mode constant --games 45
    python experiments/benchmark.py --all-presets --games 100

结果会同时：
* 打印到终端（Markdown 表格）
* 写入 ``Doc/08_对局与模拟日志.md``
* 保存 JSON 到 ``runtime/runs/``
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config  # noqa: E402
from core import journal  # noqa: E402
from engine.simulator import run_benchmark  # noqa: E402
from strategies import DEFAULT_PLAN_KEYS, catalog  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


PRESETS = {
    # 名称: (计划条目, 默认局数, 默认轮次上限, 秘密生成方式)
    "greedy_vs_adaptive": ([
        {"key": k} for k in DEFAULT_PLAN_KEYS
    ], 200, 40, "distinct"),
    "two_phase_vs_hybrid": ([
        {"key": "two_phase", "params": {"count_policy": "auto_exact"}},
        {"key": "two_phase", "params": {"count_policy": "auto_exact", "lock_known_positions": False}},
        {"key": "two_phase", "params": {"count_policy": "skip"}},
        {"key": "two_phase", "params": {"count_policy": "exact"}},
        {"key": "adaptive_hybrid", "params": {"explore_threshold": 1, "pick_mode": "split"}},
        {"key": "position_entropy", "params": {"pick_mode": "split"}},
        {"key": "particle_entropy", "params": {"objective": "expected_correct"}},
    ], 200, 40, "distinct"),
    "misplaced_cost": ([
        {"key": "naive_position", "params": {"global_prune": False}},
        {"key": "naive_position", "params": {"global_prune": True}},
        {"key": "position_entropy", "params": {"pick_mode": "split", "global_prune": False}},
        {"key": "position_entropy", "params": {"pick_mode": "split"}},
        {"key": "position_entropy", "params": {"pick_mode": "entropy"}},
        {"key": "adaptive_hybrid", "params": {"explore_threshold": 1, "pick_mode": "split"}},
    ], 200, 40, "distinct"),
    "out_of_rule": ([{"key": k} for k in DEFAULT_PLAN_KEYS], 45, 60, "constant"),
    "repeated_rule": ([{"key": k} for k in DEFAULT_PLAN_KEYS], 200, 60, "repeated"),
    "particle_scaling": ([
        {"key": "particle_entropy", "params": {"particles": 32}},
        {"key": "particle_entropy", "params": {"particles": 96}},
        {"key": "particle_entropy", "params": {"particles": 512}},
        {"key": "particle_entropy", "params": {"particles": 96, "objective": "entropy"}},
    ], 100, 40, "distinct"),
    "objective_sweep": ([
        {"key": "particle_entropy", "params": {"objective": "entropy"}},
        {"key": "particle_entropy", "params": {"objective": "entropy_correct"}},
        {"key": "particle_entropy", "params": {"objective": "expected_remaining"}},
        {"key": "particle_entropy", "params": {"objective": "expected_correct"}},
    ], 200, 40, "distinct"),
}


def coerce(text: str):
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def parse_entry(text: str) -> dict:
    """解析 ``key`` 或 ``key:param=value;param=value`` 形式的计划条目。"""
    text = text.strip()
    if not text:
        return {}
    if ":" not in text:
        return {"key": text, "params": {}}
    key, rest = text.split(":", 1)
    params = {}
    for part in rest.split(";"):
        if not part.strip():
            continue
        name, _, value = part.partition("=")
        params[name.strip()] = coerce(value.strip())
    return {"key": key.strip(), "params": params}


def build_plan(specs):
    """``specs`` 可以是 key 字符串列表，也可以直接是计划条目字典列表。"""
    available = {entry["key"]: entry for entry in catalog()}
    plan = []
    for spec in specs:
        entry = spec if isinstance(spec, dict) else parse_entry(spec)
        key = entry.get("key")
        if not key:
            continue
        if key not in available:
            raise SystemExit(f"未知策略 {key}；可用：{sorted(available)}")
        plan.append({"key": key, "params": dict(entry.get("params") or {})})
    if not plan:
        raise SystemExit("计划为空")
    return plan


def print_report(report, plan):
    names = {entry["key"]: entry["name"] for entry in catalog()}
    labels: Dict[str, str] = {}
    for entry in report["meta"]["plan"]:
        ident = entry["id"]
        bits = ", ".join(f"{k}={v}" for k, v in (entry.get("params") or {}).items())
        suffix = f" [{bits}]" if bits else ""
        labels[ident] = f"{names.get(entry['key'], entry['key'])}{suffix}"

    print()
    print(f"种子 {report['meta']['seed']}｜每策略 {report['meta']['games']} 局｜"
          f"轮次上限 {report['meta']['maxRounds']}｜秘密 {report['meta']['secretMode']}｜"
          f"总耗时 {report['meta']['elapsedSec']}s")
    header = f"{'策略':<44}{'平均':>8}{'中位数':>8}{'P90':>7}{'P99':>7}{'最坏':>7}{'解出率':>9}{'ms/局':>9}"
    print(header)
    print("-" * len(header))
    for ident, entry in report["strategies"].items():
        agg = entry["aggregate"]
        print(
            f"{labels.get(ident, ident):<44}"
            f"{agg['mean']:>8.3f}{agg['median']:>8.1f}{agg['p90']:>7.1f}{agg['p99']:>7.1f}"
            f"{agg['max']:>7}{agg['solveRate'] * 100:>8.1f}%{agg['meanMsPerGame']:>9.2f}"
        )
    print()
    print("两两配对比较（meanDiff = a − b，负数表示 a 更快；* 表示 p<0.05）：")
    for row in report["pairwise"]:
        star = "*" if row["significant"] else " "
        print(
            f"  {labels.get(row['a'], row['a']):<42} vs {labels.get(row['b'], row['b']):<42}"
            f" a优 {row['aWins']:>4} / b优 {row['bWins']:>4} / 平 {row['ties']:>4}"
            f"  Δ={row['meanDiff']:>7.3f}  p={row['pValue']:.4f} {star}"
        )


def main():
    parser = argparse.ArgumentParser(description="组合猜序列启发式策略基准实验")
    parser.add_argument("--plan", help="逗号分隔的策略 key 列表")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="使用预置实验方案")
    parser.add_argument("--all-presets", action="store_true", help="依次运行全部预置方案")
    parser.add_argument("--games", type=int, help="每策略局数")
    parser.add_argument("--max-rounds", type=int, help="轮次上限")
    parser.add_argument("--seed", type=int, default=20260920)
    parser.add_argument("--secret-mode", choices=["distinct", "repeated", "constant"],
                        help="默认 distinct（规则内：10 个互异组合）；repeated/constant 属于规则外对照")
    parser.add_argument("--no-log", action="store_true", help="不写入 Doc/ 日志")
    args = parser.parse_args()

    config.ensure_dirs()

    jobs = []
    override_mode = args.secret_mode
    if args.all_presets:
        for name, (specs, games, max_rounds, mode) in PRESETS.items():
            jobs.append((name, build_plan(specs), args.games or games, args.max_rounds or max_rounds,
                         override_mode or mode))
    elif args.preset:
        specs, games, max_rounds, mode = PRESETS[args.preset]
        jobs.append((args.preset, build_plan(specs), args.games or games, args.max_rounds or max_rounds,
                     override_mode or mode))
    else:
        specs = [s for s in args.plan.split(",") if s.strip()] if args.plan else list(DEFAULT_PLAN_KEYS)
        jobs.append(("custom", build_plan(specs), args.games or 200, args.max_rounds or 40,
                     override_mode or "distinct"))

    for name, plan, games, max_rounds, mode in jobs:
        print(f"\n=== 方案 {name} ===")
        started = time.perf_counter()
        report = run_benchmark(plan, games=games, seed=args.seed, max_rounds=max_rounds, secret_mode=mode)
        print_report(report, plan)
        if not args.no_log:
            journal.record_simulation(plan, report["meta"], report)
        out = config.RUNS_DIR / f"cli-{name}-{int(time.time())}.json"
        out.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        print(f"已保存：{out}（{time.perf_counter() - started:.1f}s）")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
