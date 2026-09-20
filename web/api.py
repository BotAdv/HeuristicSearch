# -*- coding: utf-8 -*-
"""REST API（Flask Blueprint）。

路由一览
--------
``GET  /api/meta``                     规则与候选空间
``GET  /api/strategies``               策略库（含参数声明）
``GET  /api/experiments/presets``      预设实验方案
``POST /api/games``                    新建一局（手动 / 指定策略）
``GET  /api/games/<gid>``              查询对局状态
``POST /api/games/<gid>/step``         推进一轮（可传入手动猜测）
``POST /api/games/<gid>/play``         自动跑到底，返回完整轨迹
``DELETE /api/games/<gid>``            删除对局
``POST /api/quickplay``                一次性快速对局（用于策略卡片预览）
``POST /api/simulations``              提交批量模拟任务
``GET  /api/jobs``                     任务列表
``GET  /api/jobs/<jid>``               任务详情（?result=1 取结果）
``POST /api/jobs/<jid>/cancel``        取消任务
``GET  /api/jobs/<jid>/export``        导出结果（csv / json）
``GET  /api/runs``                     历史运行列表
``GET  /api/runs/<rid>``               某次运行的完整报告

逐步猜测（人工录入反馈，前缀 ``/api/human``）：
``POST /api/human/games``              新建对局（默认立刻让策略给出首轮猜测）
``GET  /api/human/games/<gid>``        查询状态（含 pendingGuess / roundWarnings / support）
``POST /api/human/games/<gid>/next``   让策略给出本轮猜测
``POST /api/human/games/<gid>/feedback`` 录入本轮逐位反馈（可开启严格校验）
``POST /api/human/games/<gid>/undo``   撤销上一轮（并恢复该轮猜测为待反馈）
``POST /api/human/games/<gid>/save``   落盘并记录到 runtime/games 与 Doc/
``DELETE /api/human/games/<gid>``      删除会话
``GET  /api/human/archive``            已落盘对局列表
``GET  /api/human/archive/<gid>``      某局完整存档
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request

import config
from core import journal
from core.game import (
    ALL_COMBOS,
    CANDIDATES,
    FEEDBACK_KINDS,
    FEEDBACK_LETTER,
    FEEDBACK_MEANING,
    OBJECTS,
    REMOVED_COMBOS,
    SECRET_DISTINCT,
    SEQ_LEN,
    combo_str,
    serialize_sequence,
    validate_sequence,
    validate_secret,
)
from core.feedback import feedback_to_letters, parse_feedback
from core.sequence_game import HumanFeedbackGame, SequenceGame
from engine import jobs as jobs_mod
from engine import simulator
from engine.simulator import SECRET_MODES
from strategies import DEFAULT_PLAN_KEYS, catalog, create, get_strategy_class

api = Blueprint("api", __name__, url_prefix="/api")

# --------------------------------------------------------------------------
# 对局会话
# --------------------------------------------------------------------------
_GAMES: Dict[str, "GameSession"] = {}
_GAME_LOCK = threading.Lock()
MAX_SESSIONS = 200


class GameSession:
    """一个对局会话：游戏状态 + 可选的策略实例。"""

    def __init__(
        self,
        game: SequenceGame,
        mode: str,
        strategy_key: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        strategy_seed: Optional[int] = None,
        max_rounds: int = config.MAX_ROUNDS_DEFAULT,
        tag: str = "sandbox",
    ) -> None:
        self.game = game
        self.mode = mode
        self.strategy_key = strategy_key
        self.params = params or {}
        self.tag = tag
        self.max_rounds = max_rounds
        self.logged_rounds = 0
        self.strategy = (
            create(strategy_key, seed=strategy_seed, **self.params) if strategy_key else None
        )

    # ------------------------------------------------------------------ 操作
    def step(self, guess=None) -> Dict[str, Any]:
        if self.game.solved:
            raise RuntimeError("本局已经结束")
        if self.game.rounds >= self.max_rounds:
            raise RuntimeError(f"已达轮次上限 {self.max_rounds}")
        if guess is None:
            if self.strategy is None:
                raise ValueError("本局没有绑定策略，必须提供猜测序列")
            guess = self.strategy.next_guess(self.game.history)
        feedback = self.game.submit(guess)
        index = self.game.rounds
        journal.record_manual_round(
            self.game.game_id,
            index,
            serialize_sequence(self.game.history[-1][0]),
            list(feedback),
            self.game.solved,
            strategy_key=self.strategy_key or "human",
        )
        self.logged_rounds = index
        return {
            "index": index,
            "guess": serialize_sequence(self.game.history[-1][0]),
            "feedback": list(feedback),
            "letters": "".join(FEEDBACK_LETTER[f] for f in feedback),
            "solved": self.game.solved,
            "rounds": self.game.rounds,
        }

    def payload(self, reveal: bool = False, with_history: bool = True) -> Dict[str, Any]:
        data = self.game.state(reveal_secret=reveal)
        data.update(
            {
                "mode": self.mode,
                "strategy": self.strategy_key,
                "params": self.params,
                "maxRounds": self.max_rounds,
                "exhausted": self.game.rounds >= self.max_rounds and not self.game.solved,
            }
        )
        if not with_history:
            data.pop("history", None)
        return data


def _gc_sessions() -> None:
    if len(_GAMES) <= MAX_SESSIONS:
        return
    ordered = sorted(_GAMES.items(), key=lambda kv: kv[1].game.game_id)
    for key, _ in ordered[: len(_GAMES) - MAX_SESSIONS]:
        _GAMES.pop(key, None)


def _get_session(gid: str) -> GameSession:
    session = _GAMES.get(gid)
    if session is None:
        raise KeyError(f"对局 {gid} 不存在（可能已被清理，请重新开一局）")
    return session


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------
def _parse_lenient_combo(value) -> tuple:
    """比 ``parse_combo`` 更宽松：允许被删除的组合，用于构造“非法猜测”提示。"""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        a, b = int(value[0]), int(value[1])
    elif isinstance(value, str):
        text = value.strip().strip("()").replace("-", ",").replace(" ", ",")
        parts = [p for p in text.split(",") if p]
        a, b = int(parts[0]), int(parts[1])
    else:
        raise ValueError(f"无法解析组合：{value!r}")
    if a > b:
        a, b = b, a
    return (a, b)


def _parse_guess(raw) -> List:
    if not isinstance(raw, (list, tuple)) or len(raw) != SEQ_LEN:
        raise ValueError(f"猜测序列必须包含 {SEQ_LEN} 个组合")
    return validate_sequence([_parse_lenient_combo(x) for x in raw], SEQ_LEN)


def _parse_secret(raw) -> Optional[List]:
    """解析并校验一个**秘密**序列（需满足当前规则：组合互异）。"""
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip() == "":
        return None
    return validate_secret([_parse_lenient_combo(x) for x in raw], SEQ_LEN)


def _int_field(payload: Dict[str, Any], name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(payload.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _parse_plan(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get("plan")
    if not raw:
        raw = [{"key": k} for k in DEFAULT_PLAN_KEYS]
    plan: List[Dict[str, Any]] = []
    seen = set()
    for item in raw[: config.MAX_STRATEGIES_PER_RUN * 2]:
        key = item.get("key") or item.get("strategy")
        if not key:
            continue
        get_strategy_class(key)  # 未知策略直接抛错
        params = dict(item.get("params") or {})
        signature = key + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        if signature in seen:
            continue
        seen.add(signature)
        plan.append({"key": key, "params": params})
    if not plan:
        raise ValueError("至少需要选择一个策略")
    if len(plan) > config.MAX_STRATEGIES_PER_RUN * 2:
        raise ValueError(f"一次最多运行 {config.MAX_STRATEGIES_PER_RUN * 2} 个策略变体")
    return plan


# --------------------------------------------------------------------------
# 元信息
# --------------------------------------------------------------------------
@api.get("/meta")
def api_meta():
    return jsonify(
        {
            "objects": list(OBJECTS),
            "seqLen": SEQ_LEN,
            "allCombos": [serialize_sequence([c])[0] for c in ALL_COMBOS],
            "removedCombos": [serialize_sequence([c])[0] for c in REMOVED_COMBOS],
            "candidates": [serialize_sequence([c])[0] for c in CANDIDATES],
            "candidateCount": len(CANDIDATES),
            "secretDistinct": SECRET_DISTINCT,
            "secretModes": [
                {"value": key, **meta} for key, meta in SECRET_MODES.items()
            ],
            "feedbackKinds": list(FEEDBACK_KINDS),
            "feedbackLetters": FEEDBACK_LETTER,
            "feedbackMeaning": FEEDBACK_MEANING,
            "defaults": {
                "maxRounds": config.MAX_ROUNDS_DEFAULT,
                "maxRoundsLimit": config.MAX_ROUNDS_LIMIT,
                "maxGames": config.MAX_GAMES_PER_RUN,
                "docLog": config.DOC_LOG_ENABLED,
                "docLogRounds": config.DOC_LOG_ROUNDS,
            },
        }
    )


@api.get("/strategies")
def api_strategies():
    return jsonify({"strategies": catalog(), "defaultPlan": DEFAULT_PLAN_KEYS})


@api.get("/experiments/presets")
def api_presets():
    """预置实验方案，直接对应需要验证的四个核心问题。"""
    return jsonify(
        {
            "presets": [
                {
                    "id": "greedy_vs_adaptive",
                    "name": "问题 1：不同启发式策略的期望步数差异",
                    "plan": [{"key": k} for k in DEFAULT_PLAN_KEYS] + [{"key": "fixed_cycle"}],
                    "games": 200,
                    "maxRounds": 40,
                    "secretMode": "distinct",
                },
                {
                    "id": "two_phase_vs_hybrid",
                    "name": "问题 2：两阶段 vs 混合策略",
                    "plan": [
                        {"key": "two_phase", "params": {"count_policy": "auto_exact"}},
                        {"key": "two_phase", "params": {"count_policy": "auto_exact", "lock_known_positions": False}},
                        {"key": "adaptive_hybrid", "params": {"explore_threshold": 1, "pick_mode": "split"}},
                        {"key": "position_entropy", "params": {"pick_mode": "split"}},
                        {"key": "particle_entropy", "params": {"objective": "expected_correct"}},
                    ],
                    "games": 200,
                    "maxRounds": 40,
                    "secretMode": "distinct",
                },
                {
                    "id": "misplaced_cost",
                    "name": "问题 3：MISPLACED 掩盖效应与全局剪枝消融",
                    "plan": [
                        {"key": "naive_position", "params": {"global_prune": False}},
                        {"key": "naive_position", "params": {"global_prune": True}},
                        {"key": "position_entropy", "params": {"pick_mode": "split", "global_prune": False}},
                        {"key": "position_entropy", "params": {"pick_mode": "split"}},
                        {"key": "position_entropy", "params": {"pick_mode": "entropy"}},
                        {"key": "adaptive_hybrid", "params": {"explore_threshold": 1, "pick_mode": "split"}},
                    ],
                    "games": 200,
                    "maxRounds": 40,
                    "secretMode": "distinct",
                },
                {
                    "id": "out_of_rule",
                    "name": "问题 3b：规则外场景对照（允许重复 / 常数序列）",
                    "plan": [{"key": k} for k in DEFAULT_PLAN_KEYS],
                    "games": 45,
                    "maxRounds": 60,
                    "secretMode": "constant",
                },
                {
                    "id": "particle_scaling",
                    "name": "问题 4：粒子数对精度/耗时的影响",
                    "plan": [
                        {"key": "particle_entropy", "params": {"particles": 32}},
                        {"key": "particle_entropy", "params": {"particles": 96}},
                        {"key": "particle_entropy", "params": {"particles": 512}},
                        {"key": "particle_entropy", "params": {"particles": 96, "objective": "entropy"}},
                    ],
                    "games": 100,
                    "maxRounds": 40,
                    "secretMode": "distinct",
                },
            ]
        }
    )


# --------------------------------------------------------------------------
# 对局
# --------------------------------------------------------------------------
@api.post("/games")
def api_create_game():
    payload = request.get_json(silent=True) or {}
    try:
        secret = _parse_secret(payload.get("secret"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    mode = payload.get("mode") or ("strategy" if payload.get("strategy") else "manual")
    strategy_key = payload.get("strategy") if mode == "strategy" else None
    if strategy_key:
        try:
            get_strategy_class(strategy_key)
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 400
    seed = payload.get("seed")
    seed = int(seed) if seed not in (None, "") else None
    strategy_seed = payload.get("strategySeed")
    strategy_seed = int(strategy_seed) if strategy_seed not in (None, "") else seed
    session = GameSession(
        SequenceGame(secret=secret, seed=seed if secret is None else None),
        mode=mode,
        strategy_key=strategy_key,
        params=payload.get("params") or {},
        strategy_seed=strategy_seed,
        max_rounds=_int_field(payload, "maxRounds", config.MAX_ROUNDS_DEFAULT, 1, config.MAX_ROUNDS_LIMIT),
        tag=payload.get("tag") or "sandbox",
    )
    with _GAME_LOCK:
        _GAMES[session.game.game_id] = session
        _gc_sessions()
    reveal = bool(payload.get("reveal"))
    return jsonify(session.payload(reveal=reveal))


@api.get("/games/<gid>")
def api_get_game(gid: str):
    try:
        session = _get_session(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    reveal = request.args.get("reveal") in ("1", "true", "yes")
    return jsonify(session.payload(reveal=reveal))


@api.post("/games/<gid>/step")
def api_step_game(gid: str):
    try:
        session = _get_session(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    payload = request.get_json(silent=True) or {}
    guess = None
    if payload.get("guess"):
        try:
            guess = _parse_guess(payload["guess"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    try:
        round_info = session.step(guess)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - 策略内部异常
        return jsonify({"error": f"策略执行失败：{type(exc).__name__}: {exc}"}), 500
    return jsonify({"round": round_info, "state": session.payload()})


@api.post("/games/<gid>/play")
def api_play_game(gid: str):
    try:
        session = _get_session(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    payload = request.get_json(silent=True) or {}
    reveal = bool(payload.get("reveal"))
    trace: List[Dict[str, Any]] = []
    try:
        while not session.game.solved and session.game.rounds < session.max_rounds:
            trace.append(session.step())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"策略执行失败：{type(exc).__name__}: {exc}", "trace": trace}), 500
    if session.strategy_key:
        journal.record_game(
            {
                "gameId": session.game.game_id,
                "solved": session.game.solved,
                "rounds": session.game.rounds,
                "trace": trace,
                "secret": session.game.secret,
            },
            strategy_key=session.strategy_key,
            strategy_name=session.strategy.name if session.strategy else session.strategy_key,
            params=session.params,
            tag=session.tag,
            include_rounds=False,
        )
    return jsonify({"trace": trace, "state": session.payload(reveal=reveal)})


@api.delete("/games/<gid>")
def api_delete_game(gid: str):
    with _GAME_LOCK:
        removed = _GAMES.pop(gid, None)
    return jsonify({"removed": bool(removed)})


@api.post("/quickplay")
def api_quickplay():
    """一次性对局：不需要会话，直接返回轨迹。"""
    payload = request.get_json(silent=True) or {}
    try:
        key = payload.get("strategy")
        get_strategy_class(key)
        secret = _parse_secret(payload.get("secret"))
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    seed = payload.get("seed")
    seed = int(seed) if seed not in (None, "") else int(time.time() * 1000) % 1_000_000
    max_rounds = _int_field(payload, "maxRounds", config.MAX_ROUNDS_DEFAULT, 1, config.MAX_ROUNDS_LIMIT)
    params = payload.get("params") or {}
    secret_mode = payload.get("secretMode") or "distinct"
    if secret_mode == "random":
        secret_mode = "repeated"
    if secret is None:
        secrets = simulator.make_secrets(1, seed, mode=secret_mode)
        secret = secrets[0]
        rule_distinct = secret_mode == "distinct"
    else:
        # 用户显式给了秘密序列：按它自身的性质决定要不要套用互异推理
        rule_distinct = len(set(secret)) == len(secret)
    result = simulator.replay_transcript(
        key,
        params,
        secret,
        max_rounds=max_rounds,
        strategy_seed=seed,
        rule_distinct=rule_distinct,
    )
    if payload.get("log", True):
        journal.record_game(
            {
                "gameId": f"quick-{seed}",
                "solved": result["solved"],
                "rounds": result["rounds"],
                "trace": [{"guess": t["guess"], "feedback": t["feedback"]} for t in (result["trace"] or [])],
                "secret": serialize_sequence(secret),
            },
            strategy_key=key,
            strategy_name=get_strategy_class(key).name,
            params=params,
            tag=payload.get("tag") or "quickplay",
            include_rounds=bool(payload.get("logRounds")),
        )
    return jsonify(
        {
            "strategy": key,
            "params": params,
            "secret": serialize_sequence(secret),
            "solved": result["solved"],
            "rounds": result["rounds"],
            "seconds": result["seconds"],
            "error": result["error"],
            "trace": result["trace"],
        }
    )


# --------------------------------------------------------------------------
# 批量模拟
# --------------------------------------------------------------------------
def _run_simulation(job: jobs_mod.Job) -> Dict[str, Any]:
    payload = job.meta["payload"]
    plan = job.meta["plan"]
    secrets = job.meta.get("secrets")
    if secrets:
        secrets = [validate_sequence([tuple(c) for c in row], SEQ_LEN) for row in secrets]

    def progress(info: Dict[str, Any]) -> None:
        job.progress = {
            "done": info["done"],
            "total": info["total"],
            "current": info["current"],
            "message": f"正在跑 {info['current']}：{info['doneInStrategy']}/{info['gamesInStrategy']}",
        }

    report = simulator.run_benchmark(
        plan,
        games=payload["games"],
        seed=payload["seed"],
        max_rounds=payload["maxRounds"],
        secret_mode=payload["secretMode"],
        secrets=secrets,
        progress=progress,
        cancel_event=job.cancel_event,
    )
    journal.record_simulation(plan, report["meta"], report)
    try:
        config.ensure_dirs()
        path = config.RUNS_DIR / f"{job.id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        report["savedTo"] = str(path)
    except OSError as exc:  # 保存失败不影响返回结果
        report["savedTo"] = None
        report["saveError"] = str(exc)
    return report


@api.post("/simulations")
def api_start_simulation():
    payload = request.get_json(silent=True) or {}
    try:
        plan = _parse_plan(payload)
    except (KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    games = _int_field(payload, "games", 100, 1, config.MAX_GAMES_PER_RUN)
    seed = _int_field(payload, "seed", 20260920, 0, 2**31 - 1)
    max_rounds = _int_field(payload, "maxRounds", config.MAX_ROUNDS_DEFAULT, 1, config.MAX_ROUNDS_LIMIT)
    secret_mode = payload.get("secretMode") or "distinct"
    if secret_mode == "random":  # 兼容旧参数名
        secret_mode = "repeated"
    if secret_mode not in SECRET_MODES:
        return jsonify({"error": f"未知的秘密生成方式：{secret_mode}（可用：{sorted(SECRET_MODES)}）"}), 400
    meta_payload = {
        "plan": plan,
        "games": games,
        "seed": seed,
        "maxRounds": max_rounds,
        "secretMode": secret_mode,
    }
    job = jobs_mod.submit(
        "simulation",
        {"payload": meta_payload, "plan": plan},
        _run_simulation,
    )
    return jsonify({"job": job.snapshot()}), 202


@api.get("/jobs")
def api_list_jobs():
    return jsonify({"jobs": jobs_mod.list_jobs()})


@api.get("/jobs/<jid>")
def api_get_job(jid: str):
    job = jobs_mod.get(jid)
    if job is None:
        return jsonify({"error": "任务不存在"}), 404
    include = request.args.get("result") in ("1", "true", "yes")
    return jsonify({"job": job.snapshot(include_result=include)})


@api.post("/jobs/<jid>/cancel")
def api_cancel_job(jid: str):
    if not jobs_mod.cancel(jid):
        return jsonify({"error": "任务不存在"}), 404
    job = jobs_mod.get(jid)
    return jsonify({"job": job.snapshot() if job else None})


@api.get("/jobs/<jid>/export")
def api_export_job(jid: str):
    job = jobs_mod.get(jid)
    if job is None or job.result is None:
        return jsonify({"error": "任务不存在或尚未完成"}), 404
    fmt = (request.args.get("format") or "csv").lower()
    report = job.result
    if fmt == "json":
        body = json.dumps(report, ensure_ascii=False, indent=2)
        return Response(
            body,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="run-{jid}.json"'},
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["section", "strategy", "game_index", "rounds", "solved", "ms"])
    for key, entry in report["strategies"].items():
        rounds = entry["rounds"]
        flags = entry["solvedFlags"]
        times = entry.get("timesMs") or [""] * len(rounds)
        for index, (r, ok) in enumerate(zip(rounds, flags)):
            writer.writerow(["per_game", key, index + 1, r, int(bool(ok)), f"{times[index]:.3f}" if times[index] != "" else ""])
    writer.writerow([])
    writer.writerow(
        ["section", "strategy", "games", "mean", "median", "p90", "p99", "max", "unsolved", "meanMsPerGame", "totalSec"]
    )
    for key, entry in report["strategies"].items():
        agg = entry["aggregate"]
        writer.writerow(
            [
                "aggregate",
                key,
                agg["games"],
                f"{agg['mean']:.4f}",
                f"{agg['median']:.2f}",
                f"{agg['p90']:.2f}",
                f"{agg['p99']:.2f}",
                agg["max"],
                agg["unsolved"],
                f"{agg['meanMsPerGame']:.3f}",
                f"{agg['totalSec']:.3f}",
            ]
        )
    writer.writerow([])
    writer.writerow(["section", "a", "b", "games", "aWins", "bWins", "ties", "meanDiff", "pValue", "significant"])
    for row in report.get("pairwise", []):
        writer.writerow(
            [
                "pairwise",
                row["a"],
                row["b"],
                row["games"],
                row["aWins"],
                row["bWins"],
                row["ties"],
                f"{row['meanDiff']:.4f}",
                f"{row['pValue']:.5f}",
                int(bool(row["significant"])),
            ]
        )
    body = "\ufeff" + buffer.getvalue()  # BOM 让 Excel 正确识别 UTF-8
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="run-{jid}.csv"'},
    )


@api.get("/runs")
def api_runs():
    config.ensure_dirs()
    items = []
    for path in sorted(config.RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            meta = raw.get("meta", {})
            items.append(
                {
                    "id": path.stem,
                    "savedAt": path.stat().st_mtime,
                    "meta": meta,
                    "summary": [
                        {
                            "key": key,
                            "mean": entry["aggregate"]["mean"],
                            "median": entry["aggregate"]["median"],
                            "max": entry["aggregate"]["max"],
                            "unsolved": entry["aggregate"]["unsolved"],
                        }
                        for key, entry in raw.get("strategies", {}).items()
                    ],
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    return jsonify({"runs": items})


@api.get("/runs/<rid>")
def api_run_detail(rid: str):
    path = config.RUNS_DIR / f"{rid}.json"
    if not path.exists():
        return jsonify({"error": "运行记录不存在"}), 404
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# 逐步猜测模式（秘密未知，逐位反馈由玩家手工录入）
# --------------------------------------------------------------------------
_HUMAN: Dict[str, "HumanSession"] = {}
_HUMAN_LOCK = threading.Lock()
MAX_HUMAN_SESSIONS = 100


class HumanSession:
    """一个「逐步猜测」会话：人工反馈的对局 + 出招的策略实例。"""

    def __init__(
        self,
        game: HumanFeedbackGame,
        strategy_key: str,
        params: Dict[str, Any],
        strategy_seed: Optional[int] = None,
        strict: bool = False,
        auto_save: bool = True,
    ) -> None:
        self.game = game
        self.strategy_key = strategy_key
        self.params = params
        self.strict = strict
        self.auto_save = auto_save
        self.saved = False
        self.record: Optional[Dict[str, Any]] = None
        self.strategy = create(strategy_key, seed=strategy_seed, **params)
        self.strategy_name = get_strategy_class(strategy_key).name

    # ------------------------------------------------------------------ 操作
    def issue_guess(self) -> List[List[int]]:
        guess = self.strategy.next_guess(self.game.history)
        return serialize_sequence(self.game.set_pending(guess))

    def submit(self, feedback: List[str]) -> List[str]:
        warnings = self.game.submit_feedback(feedback)
        if self.strict and warnings:
            # 严格模式：退回这一轮，并保留同一个猜测继续等待反馈（不污染历史）
            self.game.reject_last_round()
            raise ValueError("反馈与已有记录矛盾（严格模式已拒绝）：" + "；".join(warnings))
        if self.game.solved and self.auto_save:
            self.save()
        return warnings

    def save(self) -> Dict[str, Any]:
        self.record = self.game.record(strategy_key=self.strategy_key, params=self.params)
        path = journal.archive_game(self.record)
        self.record["savedPath"] = path
        self.game.saved_path = path
        journal.record_human_game(self.record)
        self.saved = True
        return self.record

    def rollback_save(self) -> None:
        self.saved = False
        self.record = None

    def payload(self) -> Dict[str, Any]:
        data = self.game.state()
        data.update(
            {
                "strategy": self.strategy_key,
                "strategyName": self.strategy_name,
                "params": self.params,
                "strict": self.strict,
                "autoSave": self.auto_save,
                "saved": self.saved,
                "canUndo": bool(self.game.history) or self.game.pending_guess is not None,
            }
        )
        return data


def _get_human(gid: str) -> HumanSession:
    session = _HUMAN.get(gid)
    if session is None:
        raise KeyError(f"逐步猜测对局 {gid} 不存在（可能已被清理，请重新开始）")
    return session


def _gc_human() -> None:
    if len(_HUMAN) <= MAX_HUMAN_SESSIONS:
        return
    ordered = sorted(_HUMAN.items(), key=lambda kv: kv[1].game.created)
    for key, _ in ordered[: len(_HUMAN) - MAX_HUMAN_SESSIONS]:
        _HUMAN.pop(key, None)


@api.post("/human/games")
def api_human_create():
    payload = request.get_json(silent=True) or {}
    key = payload.get("strategy")
    try:
        get_strategy_class(key)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 400
    params = dict(payload.get("params") or {})
    assume_distinct = payload.get("assumeDistinct")
    seed = payload.get("strategySeed")
    seed = int(seed) if seed not in (None, "") else None
    game = HumanFeedbackGame(
        assume_distinct=None if assume_distinct is None else bool(assume_distinct),
        label=str(payload.get("label") or ""),
        note=str(payload.get("note") or ""),
    )
    session = HumanSession(
        game,
        key,
        params,
        strategy_seed=seed,
        strict=bool(payload.get("strict")),
        auto_save=payload.get("autoSave", True) is not False,
    )
    with _HUMAN_LOCK:
        _HUMAN[game.game_id] = session
        _gc_human()
    if payload.get("autoStart", True):
        try:
            session.issue_guess()
        except Exception as exc:  # noqa: BLE001 - 策略异常不应导致会话创建失败
            return jsonify({"error": f"策略首轮出招失败：{type(exc).__name__}: {exc}"}), 500
    return jsonify(session.payload())


@api.get("/human/games/<gid>")
def api_human_get(gid: str):
    try:
        session = _get_human(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(session.payload())


@api.post("/human/games/<gid>/next")
def api_human_next(gid: str):
    try:
        session = _get_human(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    if session.game.solved:
        return jsonify({"error": "本局已全部 CORRECT，无需再猜"}), 400
    if session.game.pending_guess is not None:
        return jsonify({"error": "上一轮猜测还在等待你录入反馈"}), 400
    try:
        session.issue_guess()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"策略出招失败：{type(exc).__name__}: {exc}"}), 500
    return jsonify(session.payload())


@api.post("/human/games/<gid>/feedback")
def api_human_feedback(gid: str):
    try:
        session = _get_human(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    payload = request.get_json(silent=True) or {}
    try:
        feedback = parse_feedback(payload.get("feedback", payload.get("letters")), SEQ_LEN)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        warnings = session.submit(feedback)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    state = session.payload()
    round_info = state["history"][-1] if state["history"] else None
    return jsonify(
        {
            "accepted": True,
            "warnings": warnings,
            "consistent": not warnings,
            "feedback": list(feedback),
            "letters": feedback_to_letters(feedback),
            "round": round_info,
            "state": state,
            "record": session.record if session.saved else None,
        }
    )


@api.post("/human/games/<gid>/undo")
def api_human_undo(gid: str):
    try:
        session = _get_human(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    undone = session.game.undo()
    session.rollback_save()
    return jsonify({"undone": undone, "state": session.payload()})


@api.post("/human/games/<gid>/save")
def api_human_save(gid: str):
    try:
        session = _get_human(gid)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 404
    if not session.game.history:
        return jsonify({"error": "还没有任何已完成的轮次，无需落盘"}), 400
    if session.game.pending_guess is not None:
        return jsonify({"error": "当前猜测还没有录入反馈，请先提交或撤销后再落盘"}), 400
    record = session.save()
    return jsonify({"saved": True, "path": record.get("savedPath"), "record": record,
                    "state": session.payload()})


@api.delete("/human/games/<gid>")
def api_human_delete(gid: str):
    with _HUMAN_LOCK:
        removed = _HUMAN.pop(gid, None)
    return jsonify({"removed": bool(removed)})


@api.get("/human/archive")
def api_human_archive():
    return jsonify({"games": journal.list_archived_games(limit=_int_field(
        request.args.to_dict(), "limit", 50, 1, 500))})


@api.get("/human/archive/<gid>")
def api_human_archive_detail(gid: str):
    record = journal.read_archived_game(gid)
    if record is None:
        return jsonify({"error": "找不到该对局存档"}), 404
    return jsonify(record)


@api.get("/health")
def api_health():
    return jsonify({"ok": True, "time": time.time(), "jobs": len(jobs_mod.JOBS)})
