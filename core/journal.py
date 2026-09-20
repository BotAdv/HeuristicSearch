# -*- coding: utf-8 -*-
"""文档日志（Doc/）。

按用户要求：项目过程中的所有交互信息（提示词、每轮反馈）都要落到根目录的 ``Doc/`` 下，
按功能与任务类型分文件记录。本模块负责其中**运行期自动产生**的部分：

* ``Doc/08_对局与模拟日志.md``  —— 人类可读的追加式日志
* ``Doc/_data/events.jsonl``     —— 机器可读的事件流（便于后续统计）

开发过程本身（提示词、需求变更、修复）写在 ``Doc/07_交互与开发记录.md``，由人工/助手维护，
本模块提供 :func:`record_interaction` 供脚本追加。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import config
from core.game import FEEDBACK_LETTER, combo_str, feedback_letters, serialize_sequence

_LOCK = threading.Lock()

LOG_MD = config.DOC_DIR / "08_对局与模拟日志.md"
INTERACTION_MD = config.DOC_DIR / "07_交互与开发记录.md"
EVENTS_JSONL = config.DOC_DIR / "_data" / "events.jsonl"

_HEADER = """# 08 对局与模拟日志

> 本文件由程序自动追加（`core/journal.py`）。**请勿手工整理格式**，如需清理请整段删除。
>
> 记录内容：
> - 沙盒中每一局对局（含**每一轮**的猜测序列与逐位反馈）
> - 批量模拟运行的汇总指标
>
> 反馈字母含义：`C`=CORRECT，`M`=MISPLACED，`P`=PARTIAL，`W`=WRONG。

---
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(path: Path, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(header, encoding="utf-8")


def _append(path: Path, text: str, header: str = "") -> None:
    config.ensure_dirs()
    with _LOCK:
        _ensure(path, header)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)


def _emit_jsonl(kind: str, payload: Dict[str, Any]) -> None:
    """追加一条机器可读事件。"""
    config.ensure_dirs()
    record = {"ts": _now(), "kind": kind, **payload}
    with _LOCK:
        EVENTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_JSONL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# 通用文本
# --------------------------------------------------------------------------
def append_markdown(title: str, lines: Iterable[str], path: Optional[Path] = None) -> None:
    path = path or LOG_MD
    _append(path, f"\n### {title}\n\n" + "\n".join(lines) + "\n", _HEADER)


def record_interaction(kind: str, content: str, extras: Optional[Dict[str, Any]] = None) -> None:
    """记录一条交互/开发信息（提示词、变更说明等）。"""
    block = [f"**类型**：{kind}", "", content.strip(), ""]
    if extras:
        block.append("```json")
        block.append(json.dumps(extras, ensure_ascii=False, indent=2))
        block.append("```")
    append_markdown(f"交互记录 · {_now()} · {kind}", block, INTERACTION_MD)
    _emit_jsonl("interaction", {"kind": kind, "content": content, "extras": extras or {}})


# --------------------------------------------------------------------------
# 对局记录
# --------------------------------------------------------------------------
def record_game(
    report: Dict[str, Any],
    strategy_key: str,
    strategy_name: str,
    params: Optional[Dict[str, Any]] = None,
    tag: str = "sandbox",
    include_rounds: Optional[bool] = None,
    secret_override: Optional[Dict[str, Any]] = None,
) -> None:
    """记录一局自动对局的完整轮次明细。"""
    if not config.DOC_LOG_ENABLED:
        return
    if include_rounds is None:
        include_rounds = config.DOC_LOG_ROUNDS

    lines: List[str] = [
        f"- 时间：{_now()}",
        f"- 阶段：{tag}",
        f"- 策略：`{strategy_key}`（{strategy_name}）",
        f"- 参数：`{json.dumps(params or {}, ensure_ascii=False)}`",
        f"- 结果：**{'成功' if report.get('solved') else '未在轮次上限内成功'}**，"
        f"共 {report.get('rounds')} 轮",
        f"- 秘密序列：`{' '.join(combo_str(tuple(c)) for c in report.get('secret', []))}`",
    ]
    if include_rounds:
        trace = report.get("trace", [])[: config.DOC_LOG_ROUND_LIMIT]
        lines.append("")
        lines.append("| 轮次 | 猜测序列 | 反馈 |")
        lines.append("| --- | --- | --- |")
        for idx, item in enumerate(trace, start=1):
            guess = " ".join(combo_str(tuple(c)) for c in item["guess"])
            letters = feedback_letters(item["feedback"])
            lines.append(f"| {idx} | `{guess}` | `{letters}` |")
        if len(report.get("trace", [])) > len(trace):
            lines.append(f"| … | 省略 {len(report['trace']) - len(trace)} 轮 | |")

    append_markdown(f"对局 · {strategy_name} · {report.get('gameId', '')}", lines)
    _emit_jsonl(
        "game",
        {
            "tag": tag,
            "strategy": strategy_key,
            "params": params or {},
            "gameId": report.get("gameId"),
            "solved": report.get("solved"),
            "rounds": report.get("rounds"),
            "secret": serialize_sequence(report.get("secret", [])),
            "trace": report.get("trace") if include_rounds else None,
            **(secret_override or {}),
        },
    )


def record_manual_round(
    game_id: str,
    index: int,
    guess: List,
    feedback: List[str],
    solved: bool,
    strategy_key: str = "human",
) -> None:
    """记录沙盒手动模式的一轮。"""
    if not (config.DOC_LOG_ENABLED and config.DOC_LOG_ROUNDS):
        return
    guess_text = " ".join(combo_str(tuple(c)) for c in guess)
    letters = feedback_letters(feedback)
    append_markdown(
        f"手动对局轮次 · {game_id} · 第 {index} 轮",
        [
            f"- 时间：{_now()}",
            f"- 玩家/策略：`{strategy_key}`",
            f"- 猜测序列：`{guess_text}`",
            f"- 反馈：`{letters}`（{summarize_text(feedback)}）",
            f"- 是否结束：{'是' if solved else '否'}",
        ],
    )
    _emit_jsonl(
        "manual_round",
        {
            "gameId": game_id,
            "index": index,
            "strategy": strategy_key,
            "guess": guess,
            "feedback": feedback,
            "solved": solved,
        },
    )


def summarize_text(feedback: Sequence[str]) -> str:
    from collections import Counter

    counter = Counter(feedback)
    return "，".join(f"{FEEDBACK_LETTER[k]}×{counter[k]}" for k in ("CORRECT", "MISPLACED", "PARTIAL", "WRONG") if counter[k])


def record_simulation(plan: List[Dict[str, Any]], meta: Dict[str, Any], report: Dict[str, Any]) -> None:
    """记录一次批量模拟的汇总（不写逐局明细）。"""
    if not config.DOC_LOG_ENABLED:
        return
    lines: List[str] = [
        f"- 时间：{_now()}",
        f"- 基种子：`{meta.get('seed')}`，每策略局数：`{meta.get('games')}`，"
        f"轮次上限：`{meta.get('maxRounds')}`",
        f"- 秘密生成方式：`{meta.get('secretMode')}`",
        f"- 总耗时：`{meta.get('elapsedSec')}` 秒",
        "",
        "| 策略 | 平均步数 | 中位数 | P90 | P99 | 最坏 | 未解出 | 平均耗时(ms/局) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, entry in report.get("strategies", {}).items():
        agg = entry["aggregate"]
        lines.append(
            "| `{k}` | {mean:.3f} | {med} | {p90} | {p99} | {mx} | {uns} | {t:.1f} |".format(
                k=key,
                mean=agg["mean"],
                med=agg["median"],
                p90=agg["p90"],
                p99=agg["p99"],
                mx=agg["max"],
                uns=agg["unsolved"],
                t=agg["meanMsPerGame"],
            )
        )
    lines.append("")
    lines.append(f"- 参数：`{json.dumps(plan, ensure_ascii=False)}`")
    append_markdown("批量模拟汇总", lines)
    _emit_jsonl("simulation", {"meta": meta, "plan": plan, "report": report})
