# 组合猜序列 · 启发式搜索沙盒

用 **Flask + 原生前端** 搭的可扩展沙盒，用来对比不同启发式搜索策略在“组合猜序列”游戏上的性能。

* 对象集合 $U=\{1..10\}$，组合 $(a,b)$ 允许重复、不计顺序 → 全组合 $\binom{11}{2}=55$
* 预先删除 10 个组合 → 候选集合 $|S| = 45$
* 秘密序列与猜测序列长度均为 10，元素取自 $S$
* **秘密序列的 10 个组合两两互不相同**（规则开关 `core.game.SECRET_DISTINCT`）；
  猜测序列则允许重复——规则只约束秘密序列
* 逐位反馈：`CORRECT` > `MISPLACED` > `PARTIAL` > `WRONG`（高优先级覆盖低优先级）
* 10 个位置全部 `CORRECT` 即结束

“秘密组合互异”带来两条很强的可利用信息：**支持集恰有 10 个元素且重数恒为 1**、
**已定位的组合可以从所有其它位置的候选中删除**。它们直接决定了哪个策略最强。

## 快速开始

```powershell
conda activate flask_env      # Python 3.10 / Flask 3.1.3 / numpy 2.2.6
cd E:\Project\Interest\HeuristicSearch
python app.py                 # http://127.0.0.1:5000
```

页面：

| 路径 | 用途 |
| --- | --- |
| `/` | 沙盒：手动试玩 + 策略自动对局 + 逐轮反馈可视化 |
| `/human` | **逐步猜测**：秘密未知，策略出招、你逐位录入反馈，直到全部 CORRECT 后落盘 |
| `/strategies` | 策略库：查看/调参/一键试玩所有策略 |
| `/lab` | 模拟实验台：批量模拟 → 指标表 + 图表 + CSV/JSON 导出 |

## 常用命令

```powershell
python -m unittest discover -s tests           # 61 个单元测试
python experiments/benchmark.py --games 200     # 默认对比（5 个策略，规则内）
python experiments/benchmark.py --all-presets   # 跑完全部预置实验（对应 4 个核心问题）
python experiments/benchmark.py --plan "position_entropy:pick_mode=split;explore_floor=2,two_phase:count_policy=skip"
python experiments/benchmark.py --secret-mode repeated --games 200   # 旧规则对照
```

## 内置策略

| key | 说明 | 平均步数¹ |
| --- | --- | --- |
| `naive_position` | 朴素位置独立贪心 | 6.775 |
| `two_phase` | 两阶段：先定支持集再定顺序 | **6.125** |
| `position_entropy` | 逐位置信息增益贪心（`split` 探测 + 全局剪枝） | 6.260 |
| `adaptive_hybrid` | 自适应混合（探测/定位交织） | 6.550 |
| `particle_entropy` | 粒子滤波 + 全局最大熵 | 7.045 |

¹ 200 局 / 种子 20260920 / `--secret-mode distinct`（当前规则）。
`two_phase` 把 `lock_known_positions` 关掉可到 **5.765 轮**。
同一套代码在旧规则（`--secret-mode repeated`）下排名完全不同，完整数据与结论见
`Doc/06_实验结论与复现指南.md`。

> 所有依赖“秘密组合互异”的策略都有一个 `assume_distinct` 开关；
> 模拟器会根据 `secret_mode` 自动注入，做规则外对照实验时无需手改。

## 策略接口

```python
class Strategy:
    def next_guess(self, history) -> List[Tuple[int, int]]:
        """history = [(guess, feedback), ...]，返回长度 10 的合法组合列表。"""
```

新增策略：在 `strategies/` 新建模块并在 `strategies/__init__.py` 的 `CLASSES` 里注册即可，
REST 接口、策略库页、实验台、命令行实验会自动识别。

## 目录

```
core/          纯规则层（组合空间、反馈判定、单局状态机、Doc 日志）
strategies/    策略层（接口 + 共享信念工具 + 解释框架 + 5 个策略）
engine/        实验层（配对模拟、指标与检验、后台任务）
web/           路由与 REST API
templates/     页面（Jinja2）
static/        CSS + 原生 JS（含手写 SVG 图表）
tests/         单元测试
experiments/   命令行实验脚本
runtime/runs/  每次模拟的 JSON 结果
Doc/           项目文档（索引见 Doc/README.md）
```

## 文档

从 [`Doc/README.md`](Doc/README.md) 开始。重点两篇：

* [`Doc/02_游戏规则与反馈定义.md`](Doc/02_游戏规则与反馈定义.md) —— 规则与 `M`/`P` 的语义陷阱
* [`Doc/06_实验结论与复现指南.md`](Doc/06_实验结论与复现指南.md) —— 实测数据与四个核心问题的答案

## 一句话结论

* **当前规则下，两阶段范式（先定支持集、再定顺序）是最优的**：平均 **6.125 轮**（默认）／**5.765 轮**（关闭 `lock_known_positions`）；
  逐位置熵贪心 6.260、自适应混合 6.550、朴素贪心 6.775、粒子滤波 7.045。
* 关键原因：互异规则直接给出“重数恒为 1”，使**常数序列探针彻底失去意义**，
  两阶段的分阶段成本降到最低；同时“已安置组合 ⇒ 全局删除”成为最强剪枝，
  所以 `global_prune` 的边际价值从旧规则的 **−2.35 轮**降到 **−0.30 轮**。
* 规则改了，结论就反转：在旧规则（`--secret-mode repeated`）下两阶段是**最差**的
  （12.785 轮），逐位置贪心才是最优（6.460 轮）。**报告策略排名时必须连规则一起给出。**
* 粒子滤波 + 全局最大熵依旧最弱且最贵（7.045 轮 / 75 ms），
  粒子数 32→512 收益为零、耗时线性增长（71 → 469 ms）。
