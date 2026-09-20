# -*- coding: utf-8 -*-
"""对局复盘分析器。

对 ``runtime/games/<gameId>.json``（逐步猜测模式的落盘存档）做**可验证的复盘**：

1. 用存档里的策略与参数**回放**每一轮，检查能否复现记录中的猜测；
   再用多个随机种子跑一遍，区分“由约束逼出的必然选择”与“依赖平局随机的选择”；
2. 打开策略的解释钩子（``strategy.explain = True``），逐轮导出：
   所处阶段、支持集状态、每个位置的候选集大小与**该位置为什么选这个组合**；
3. 对回放过程做一致性自检（反馈推理是否被正确应用），
   既保证结论可靠，也顺带当一次回归测试。

用法::

    python experiments/analyze_game.py cf2d34c94be5
    python experiments/analyze_game.py runtime/games/cf2d34c94be5.json --out Doc/11_对局复盘.md
    python experiments/analyze_game.py cf2d34c94be5 --print      # 直接打印不写文件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config  # noqa: E402
from core.game import (  # noqa: E402
    CANDIDATES,
    CORRECT,
    FEEDBACK_LETTER,
    MISPLACED,
    PARTIAL,
    SEQ_LEN,
    WRONG,
    combo_str,
    shares,
)
from strategies import create  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def resolve_record(target: str) -> Path:
    """``target`` 可以是 gameId、文件名或完整路径。"""
    path = Path(target)
    if path.exists():
        return path
    candidate = config.GAMES_DIR / f"{target}.json"
    if candidate.exists():
        return candidate
    raise SystemExit(f"找不到对局存档：{target}（可先看 runtime/games/ 目录）")


def load_record(target: str) -> Dict:
    return json.loads(resolve_record(target).read_text(encoding="utf-8"))


def snapshot(strategy) -> Dict:
    """抓取策略当前的信念快照（只读）。"""
    support = strategy.support
    belief = strategy.belief
    return {
        "phase": strategy.phase,
        "phaseARounds": strategy.phase_a_rounds,
        "sizes": [len(belief.sets[i]) for i in range(belief.seq_len)],
        "sets": [sorted(belief.sets[i]) for i in range(belief.seq_len)],
        "inList": [c for c in CANDIDATES if support.status.get(c) == "IN"],
        "outList": [c for c in CANDIDATES if support.status.get(c) == "OUT"],
        "unknownCount": sum(1 for c in CANDIDATES if support.status.get(c) == "U"),
    }


def replay(record: Dict, seed: Optional[int], explain: bool):
    """回放整局，返回 (逐轮结果, 策略实例)。"""
    params = dict(record.get("params") or {})
    strategy = create(record["strategy"], seed=seed, **params)
    strategy.explain = explain
    history: List[Tuple[Tuple, Tuple[str, ...]]] = []
    rounds = []
    for item in record["trace"]:
        recorded_guess = tuple(tuple(c) for c in item["guess"])
        # 先把历史同步进策略，再抓快照——否则快照会比实际决策时刻“慢一轮”
        strategy._sync(history)
        before = snapshot(strategy)
        made = tuple(strategy.next_guess(history))
        decision = getattr(strategy, "last_decision", None)
        rounds.append(
            {
                "index": item["index"],
                "recordedGuess": recorded_guess,
                "madeGuess": made,
                "match": made == recorded_guess,
                "feedback": tuple(item["feedback"]),
                "letters": item["letters"],
                "warnings": item.get("warnings") or [],
                "before": before,
                "decision": decision,
            }
        )
        # 用**记录里的**反馈推进回放（这样即使猜测不同也能继续走下去）
        history.append((recorded_guess, tuple(item["feedback"])))
    rounds.append(
        {
            "index": len(record["trace"]) + 1,
            "before": snapshot(strategy),
            "after": None,
            "match": None,
            "decision": None,
        }
    )
    return rounds, strategy


# --------------------------------------------------------------------------
# 一致性自检
# --------------------------------------------------------------------------
def check_after(after: Dict, guess: Sequence, feedback: Sequence[str]) -> List[str]:
    """回放推进一轮后，校验信念是否满足该轮反馈的必然推论。"""
    problems: List[str] = []
    for i, (g, f) in enumerate(zip(guess, feedback)):
        ci = after["sets"][i]
        if f == CORRECT:
            if ci != [g]:
                problems.append(f"第 {i + 1} 位标为 CORRECT，但候选集不是单元素 {combo_str(g)}：{ci[:4]}")
        elif f == MISPLACED:
            if g in ci:
                problems.append(f"第 {i + 1} 位标为 MISPLACED，但 {combo_str(g)} 仍在候选集里")
        elif f == PARTIAL:
            bad = [c for c in ci if not shares(c, g)]
            if bad:
                problems.append(f"第 {i + 1} 位标为 PARTIAL，但候选集里有与 {combo_str(g)} 不共享元素的：{bad[:3]}")
        elif f == WRONG:
            bad = [c for c in ci if shares(c, g)]
            if bad:
                problems.append(f"第 {i + 1} 位标为 WRONG，但候选集里有与 {combo_str(g)} 共享元素的：{bad[:3]}")
        # 支持集归属
        if f in (CORRECT, MISPLACED) and g not in after["inList"]:
            problems.append(f"第 {i + 1} 位标为 {FEEDBACK_LETTER[f]}，但 {combo_str(g)} 没被记入支持集")
        if f in (PARTIAL, WRONG) and g in after["inList"]:
            problems.append(f"第 {i + 1} 位标为 {FEEDBACK_LETTER[f]}，但 {combo_str(g)} 却留在支持集里")
    return problems


# --------------------------------------------------------------------------
# 报告渲染
# --------------------------------------------------------------------------
def _combo_list(combos: Sequence, limit: Optional[int] = None) -> str:
    items = [combo_str(c) for c in combos]
    if limit is not None and len(items) > limit:
        items = items[:limit] + ["…"]
    return " ".join(items)


def _support_line(snap: Dict) -> str:
    return (
        f"支持集已确认 **{len(snap['inList'])}/10** 个成员，"
        f"已排除 **{len(snap['outList'])}** 个，剩余 **{snap['unknownCount']}** 个未判定"
    )


def _diff_line(before: Dict, after: Dict) -> str:
    pairs = []
    for i in range(len(before["sizes"])):
        b, a = before["sizes"][i], after["sizes"][i]
        if b != a:
            pairs.append(f"{i + 1}:{b}→{a}")
    total_b, total_a = sum(before["sizes"]), sum(after["sizes"])
    return (
        f"候选集总规模 {total_b} → {total_a}（收缩 {total_b - total_a}）；"
        + ("位置变化：" + "，".join(pairs) if pairs else "各位置候选数不变")
    )


def render_report(record: Dict, rounds: List[Dict], seed_check: List[Tuple[str, List[bool]]]) -> str:
    label = record.get("label") or record["gameId"]
    lines: List[str] = []
    add = lines.append

    add(f"# 对局复盘 · {label}")
    add("")
    add("> 本报告由 `experiments/analyze_game.py` 生成：用存档里的策略与参数**回放**整局，")
    add("> 打开策略的解释钩子逐轮导出决策依据，并对回放过程做一致性自检。")
    add("")
    add("## 0. 对局概况")
    add("")
    add("| 项 | 值 |")
    add("| --- | --- |")
    add(f"| 对局 ID | `{record['gameId']}` |")
    add(f"| 标签 | {label} |")
    add(f"| 模式 | {record.get('mode')}（秘密未知，反馈由人工录入） |")
    add(f"| 策略 | `{record.get('strategy')}` |")
    add(f"| 参数 | `{json.dumps(record.get('params') or {}, ensure_ascii=False)}` |")
    add(f"| 结果 | **{'全部 CORRECT，已解出' if record.get('solved') else '未解出'}**，共 {record.get('rounds')} 轮 |")
    add(f"| 一致性警告 | {sum(len(r.get('warnings') or []) for r in record.get('trace') or [])} 条 |")
    support = record.get("support") or {}
    add(f"| 最终支持集 | 属于 {support.get('knownIn')} 个 · 不属于 {support.get('knownOut')} 个 · 已定位 {support.get('placed')} 位 |")
    add("")

    add("## 1. 回放校验：解释是不是“猜的”")
    add("")
    add("| 种子 | " + " | ".join(f"第{r['index']}轮" for r in rounds if r["index"] <= len(record["trace"])) + " | 完全复现 |")
    add("| --- | " + " | ".join("---" for r in rounds if r["index"] <= len(record["trace"])) + " | --- |")
    for name, flags in seed_check:
        add(f"| `{name}` | " + " | ".join("✅" if f else "❌" for f in flags) + f" | {'是' if all(flags) else '否'} |")
    add("")
    if seed_check:
        n = len(seed_check[0][1])
        stable = [i + 1 for i in range(n) if all(flags[i] for _, flags in seed_check)]
        unstable = [i + 1 for i in range(n) if i + 1 not in stable]
        add(f"* **必然轮次**（对所有种子都复现）：{'、'.join(f'第 {i} 轮' for i in stable) or '无'}"
            " —— 这些选择完全由规则约束唯一确定。")
        add(f"* **含随机平局的轮次**：{'、'.join(f'第 {i} 轮' for i in unstable) or '无'}"
            " —— 候选里存在多个同样满足条件的组合，用随机数决定取哪个（不影响正确性，只影响步数）。")
    add("")

    for r in rounds:
        if r["decision"] is None:
            add("## 终局")
            add("")
            add(f"全部 10 个位置均为 CORRECT，对局结束（{record.get('rounds')} 轮）。")
            if r.get("before"):
                add(f"结束时支持集：{_support_line(r['before'])}。")
            add("")
            break

        d = r["decision"]
        add(f"## 第 {r['index']} 轮 · 阶段 {d['phase']}")
        add("")
        add(f"* {_support_line(r['before'])}")
        if d["phase"] == "A":
            batch = d.get("batch") or {}
            add(
                f"* 阶段 A 已进行 {d['phaseARounds']} 轮；本轮从 **{batch.get('unknownTotal', 0)} 个未判定组合**中"
                f"挑出 **{len(batch.get('chosen') or [])} 个**做支持集分类：{_combo_list(batch.get('chosen') or [], 10)}"
            )
            top = batch.get("rankedTop") or []
            if top:
                add(
                    "* 挑选依据（未判定集合按“仍可能出现的位置数”降序、再按用过次数升序）："
                    + "，".join(f"{combo_str(t['combo'])}（{t['coverage']} 个位置可能 / 用过 {t['used']} 次）" for t in top[:5])
                    + " …"
                )
        else:
            add("* 阶段 B（置换求解）：支持集已确定，只需把 10 个成员安置到 10 个位置。")
            budget = d.get("budget") or {}
            before_rem = budget.get("remainingBefore") or {}
            after_rem = budget.get("remainingOfSupport") or {}
            if before_rem:
                add(
                    "* 各支持集成员尚未安置的副本数（本轮开始 → 本轮结束）："
                    + "，".join(
                        f"{k}：{before_rem.get(k, 0)}→{after_rem.get(k, 0)}" for k in list(before_rem)[:10]
                    )
                )
                add(
                    "* 副本数 0 表示该组合已被 CORRECT 确认落位、没有余量；"
                    "仍为 1 表示还没收到过 CORRECT 确认（可能已经由消去法锁定在某个位置）。"
                )
        add("")
        add("**本轮猜测与每一位的依据**")
        add("")
        add("| # | 组合 | 该位置候选集 | 选择依据 |")
        add("| --- | --- | --- | --- |")
        probes: List[int] = []
        for cell in d["candidates"]:
            i = cell["position"] - 1
            guess_combo = r["recordedGuess"][i] if i < len(r["recordedGuess"]) else None
            size_txt = f"{cell['size']} 个"
            if cell["size"] <= 6:
                size_txt += f"：{_combo_list(cell['topCandidates'], 6)}"
            reason = (cell.get("reason") or "—").replace("|", "/")
            combo_txt = combo_str(guess_combo) if guess_combo else "—"
            # “改派批次里的 …”= 批次组合不在本位置候选集内：纯探测，本轮不可能 CORRECT
            if "改派批次里的" in reason:
                probes.append(cell["position"])
                combo_txt += " ⚠"
            add(f"| {cell['position']} | {combo_txt} | {size_txt} | {reason} |")
        add("")
        if probes:
            add(
                f"注：⚠ 标记的第 {'、'.join(str(p) for p in probes)} 位，本轮填的是“批次里的待分类组合”，"
                "它不在该位置的候选集内，所以这一位本轮必然是 PARTIAL/WRONG，而不是 CORRECT——"
                "这是为了让该组合得到分类（阶段 A 的取舍：用部分的“白拿 CORRECT”换对一个组合的判定）。"
            )
            add("")
        mark = "✅ 与存档一致" if r["match"] else "⚠️ 与存档不同（该轮存在随机平局）"
        add(f"回放结果：{mark}。")
        add("")
        add(f"**本轮反馈**：`{r['letters']}`")
        add("")
        fb_counts = {k: list(r["feedback"]).count(k) for k in (CORRECT, MISPLACED, PARTIAL, WRONG)}
        detail = "，".join(f"{FEEDBACK_LETTER[k]}×{v}" for k, v in fb_counts.items() if v)
        if d["phase"] == "A":
            add(
                f"判定明细：{detail} —— 由此新确认 **{fb_counts[CORRECT] + fb_counts[MISPLACED]}** 个组合属于支持集，"
                f"排除 **{fb_counts[PARTIAL] + fb_counts[WRONG]}** 个。"
            )
        else:
            add(
                f"判定明细：{detail} —— 阶段 B 已没有未知组合需要分类；"
                "CORRECT 把该位置锁死，MISPLACED 说明该组合属于支持集但不在本位置，于是把它从本位置的候选中剔除。"
            )
        if r["warnings"]:
            add("")
            add("一致性警告：" + "；".join(r["warnings"]))
        add("")

    # 反馈推理的效果（用相邻快照的差）
    add("## 2. 各轮反馈对信念的实际收缩")
    add("")
    add("| 轮次 | 反馈 | 候选集变化 |")
    add("| --- | --- | --- |")
    for idx, r in enumerate(rounds):
        if r["decision"] is None or idx + 1 >= len(rounds):
            continue
        after = rounds[idx + 1]["before"]
        add(f"| {r['index']} | `{r['letters']}` | {_diff_line(r['before'], after)} |")
    add("")

    add("## 3. 决策构成统计")
    add("")
    kinds = {"已锁定": 0, "批次分类": 0, "填位": 0, "指派": 0, "退化": 0, "防退化": 0}
    for r in rounds:
        if r["decision"] is None:
            continue
        for cell in r["decision"]["candidates"]:
            reason = cell.get("reason") or ""
            if reason.startswith("已锁定"):
                kinds["已锁定"] += 1
            elif reason.startswith("批次分类"):
                kinds["批次分类"] += 1
            elif reason.startswith("填位"):
                kinds["填位"] += 1
            elif reason.startswith("指派"):
                kinds["指派"] += 1
            elif reason.startswith("防退化"):
                kinds["防退化"] += 1
            else:
                kinds["退化"] += 1
    total = sum(kinds.values())
    add("| 类型 | 位置次数 | 占比 |")
    add("| --- | --- | --- |")
    for k, v in kinds.items():
        if v:
            add(f"| {k} | {v} | {v / total * 100:.1f}% |")
    add(f"| **合计** | {total} | 100% |")
    add("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="逐步猜测对局复盘分析")
    parser.add_argument("target", help="gameId 或 runtime/games/*.json 的路径")
    parser.add_argument("--out", help="输出 Markdown 路径（默认 Doc/11_对局复盘_<标签>.md）")
    parser.add_argument("--print", dest="to_stdout", action="store_true", help="只打印，不写文件")
    parser.add_argument("--seeds", default="None,1,7,42", help="用于确定性检验的随机种子列表")
    args = parser.parse_args()

    record = load_record(args.target)
    seed_names = [s.strip() for s in args.seeds.split(",") if s.strip()]

    seed_check: List[Tuple[str, List[bool]]] = []
    for name in seed_names:
        seed = None if name.lower() == "none" else int(name)
        rounds, _ = replay(record, seed, explain=False)
        flags = [r["match"] for r in rounds if r["match"] is not None]
        seed_check.append((name, flags))

    rounds, strategy = replay(record, 1 if len(seed_check) > 1 else None, explain=True)

    # 一致性自检
    problems: List[str] = []
    for idx, r in enumerate(rounds):
        if r["decision"] is None or idx + 1 >= len(rounds):
            continue
        problems += check_after(rounds[idx + 1]["before"], r["recordedGuess"], r["feedback"])

    report = render_report(record, rounds, seed_check)
    if problems:
        report += "\n## 一致性自检\n\n发现 **%d** 处问题：\n\n" % len(problems)
        report += "\n".join(f"* {p}" for p in problems) + "\n"
    else:
        report += "\n## 一致性自检\n\n✅ 全部通过：每一轮的反馈推理（CORRECT 锁定 / MISPLACED 排除 / PARTIAL 保留共享元素 / WRONG 保留不共享元素）都在候选集上正确生效，支持集归属也一致。\n"

    if args.to_stdout:
        print(report)
        return
    label = record.get("label") or record["gameId"]
    out = Path(args.out) if args.out else (config.DOC_DIR / f"11_对局复盘_{label}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"报告已写入：{out}")
    print(f"回放复现：{'全部一致' if all(r['match'] is not False for r in rounds) else '存在差异（见报告第 1 节）'}")
    print(f"一致性自检：{'通过' if not problems else f'{len(problems)} 处问题'}")


if __name__ == "__main__":
    main()
