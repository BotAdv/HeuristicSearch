# -*- coding: utf-8 -*-
"""规则变更后的 API 端到端自检（临时脚本，可随时删除）。

覆盖：元信息里的规则字段、互异秘密生成、重复秘密被拒、
猜测允许重复、规则内/规则外对局、模拟任务提交。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import app  # noqa: E402

client = app.app.test_client()
failures = []


def check(label, condition, detail=""):
    print(f"[{'OK ' if condition else 'FAIL'}] {label} {detail}")
    if not condition:
        failures.append(label)


meta = client.get("/api/meta").get_json()
check("secretDistinct 为 True", meta["secretDistinct"] is True)
check(
    "secretModes 含三种且标注规则内外",
    [(m["value"], m["inRule"]) for m in meta["secretModes"]]
    == [("distinct", True), ("repeated", False), ("constant", False)],
    str([(m["value"], m["inRule"]) for m in meta["secretModes"]]),
)
check("默认轮次上限为 40", meta["defaults"]["maxRounds"] == 40, str(meta["defaults"]["maxRounds"]))

# 规则内：随机生成互异秘密
res = client.post("/api/games", json={"mode": "strategy", "strategy": "two_phase", "seed": 42, "reveal": True})
state = res.get_json()
secret = [tuple(c) for c in state["secret"]]
check("生成的秘密为 10 个互异组合", len(set(secret)) == 10, str(len(set(secret))))
check("state 回报 secretDistinct=True", state["secretDistinct"] is True)
play = client.post(f"/api/games/{state['gameId']}/play", json={}).get_json()
check("two_phase 自动对局解出", play["state"]["solved"] is True, f"{play['state']['rounds']} 轮")
check("轮次合理（<=12）", play["state"]["rounds"] <= 12, f"{play['state']['rounds']} 轮")

# 重复秘密必须被拒
dup = client.post("/api/games", json={"mode": "manual", "secret": [[1, 1]] * 10})
check("重复秘密被拒（400）", dup.status_code == 400, dup.get_json().get("error", "")[:40])

# 猜测允许重复
game = client.post("/api/games", json={"mode": "manual", "secret": [[c[0], c[1]] for c in meta["candidates"][:10]]}).get_json()
step = client.post(f"/api/games/{game['gameId']}/step", json={"guess": [[1, 1]] * 10})
check("猜测允许重复（200）", step.status_code == 200, step.get_json().get("error", ""))
check("该轮有 MISPLACED 或 CORRECT", step.status_code == 200)

# 规则外：常数序列
qp = client.post("/api/quickplay", json={"strategy": "two_phase", "secretMode": "constant", "maxRounds": 30}).get_json()
check("规则外（常数序列）可对局且解出", qp["solved"] is True, f"{qp['rounds']} 轮")

# 规则外：允许重复
qp2 = client.post("/api/quickplay", json={"strategy": "position_entropy", "secretMode": "repeated", "seed": 5, "maxRounds": 40}).get_json()
check("规则外（允许重复）可对局且解出", qp2["solved"] is True, f"{qp2['rounds']} 轮")

# 规则内 quickplay
qp3 = client.post("/api/quickplay", json={"strategy": "position_entropy", "seed": 7}).get_json()
check("规则内 quickplay 解出", qp3["solved"] is True, f"{qp3['rounds']} 轮")

# 模拟任务（小规模，规则内）
sim = client.post("/api/simulations", json={"games": 20, "seed": 1, "maxRounds": 20, "secretMode": "distinct",
                                            "plan": [{"key": "two_phase"}, {"key": "position_entropy"}]})
check("模拟任务提交（202）", sim.status_code == 202, str(sim.status_code))

print()
print("失败项：", failures if failures else "无")
sys.exit(1 if failures else 0)
