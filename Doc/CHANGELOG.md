# CHANGELOG

本项目所有值得记录的改动。格式参考 Keep a Changelog，日期为本地日期。

## [1.3.0] — 2026-09-21

### 新增：对局复盘分析器（回答“策略为什么这么下”）

* `experiments/analyze_game.py` —— 对 `runtime/games/<gameId>.json` 做**可验证的复盘**：
  1. 用存档里的策略与参数回放每一轮，检查能否复现记录中的猜测；
     再用多个随机种子各跑一遍，区分**由约束逼出的必然选择**与**依赖平局随机的选择**；
  2. 打开策略解释钩子，逐轮导出所处阶段、支持集状态、每个位置的候选集大小与选择理由；
  3. 对回放过程做一致性自检（反馈推理是否被正确应用），并输出
     “各轮反馈对信念的实际收缩”“决策构成统计”两张表。
* 用法：`python experiments/analyze_game.py cf2d34c94be5`（默认写入 `Doc/11_对局复盘_<标签>.md`，
  `--out` 指定路径、`--print` 只打印、`--seeds` 改确定性检验用的种子列表）。
* 策略解释钩子 `Strategy.explain`（目前由 `two_phase` 实现）：`explain = True` 时每轮在
  `strategy.last_decision` 留下 `{round, phase, phaseARounds, support, candidates[…], batch, budget}`，
  每个位置的 `reason` 是一句中文依据（已锁定 / 批次分类 / 填位 / 指派 / 退化）。
  **该钩子纯只读**：开关解释不改变任何一步选择（已固化为测试）。
* 新文档 `Doc/11_对局复盘_260920-00.md`（自动生成）与 `tests/test_analyze_game.py`（10 例）。

### 修复

* `PositionBelief.count_lower` 重复计数：同一位置在后续轮次再次被 `CORRECT` 确认时，
  旧实现会把“已落位副本数”累加（1→2→…），使 `_remaining_counts()` 算出**负数剩余副本数**。
  现按**位置去重**统计（同一位置重复命中不重复计数）。
  规则内行为不变（该值只用于 `> 0` 判断，实测 200 局仍为 6.125 轮），
  但阶段 B 报告里的“剩余副本数”不再出现负数；规则外 `count_policy="exact"` 路径也因此更准确。
* 分析器最初的信念快照比决策时刻“慢一轮”（在 `next_guess` 内部 `_sync` 之前抓取），
  导致一致性自检在真实对局上报出 50 处**假**问题。现先同步历史再抓快照，同一局自检 0 问题。
* 报告里阶段 A 的位置若被“批次组合”占用（该位置本轮不可能 CORRECT），现在用 ⚠ 标注并给出解释，
  避免把探测误读成策略失误。

### 变更

* `two_phase` 阶段 B 的解释补充 `remainingBefore`（本轮开始时的剩余副本数），
  与 `remainingOfSupport`（本轮结束时）区分开；“退化”理由区分“有余量的组合已被本轮占用”与
  “候选集里没有还有余量的组合”。

## [1.2.0] — 2026-09-20

### 新增：逐步猜测模式（秘密未知，人工录入反馈）

* 新页面 `/human`：策略出招 → 你逐位录入反馈 → 策略给下一轮，全部 CORRECT 后落盘。
* `core/sequence_game.py: HumanFeedbackGame` —— 秘密未知的对局状态机，
  与 `SequenceGame` 共享 `history` 约定，**策略层零改动**；新增 `rounds_payload()` 公共序列化。
* `core/feedback.py` —— 反馈解析（字母串 / 列表 / 下标映射）与 `FeedbackChecker`：
  用四条与秘密无关的必然规律校验人工录入（同轮重复组合自洽性、支持集归属跨轮一致性、
  已定位组合唯一性、终局合法性），支持“只警告”与“严格拒绝”两种模式。
* `core/journal.py` —— `archive_game()` / `list_archived_games()` / `read_archived_game()` /
  `record_human_game()`：落盘到 `runtime/games/<gid>.json`，并追加到 `Doc/08` 与 `_data/events.jsonl`。
* `web/api.py` —— 9 个 `/api/human/*` 接口（新建 / 查询 / 出招 / 录反馈 / 撤销 / 落盘 / 删除 / 存档列表 / 存档详情）。
* 新页面模板与脚本 `templates/human.html`、`static/js/human.js`；导航栏新增「逐步猜测」。
* 新文档 `Doc/10_逐步猜测模式.md`。
* 新测试 `tests/test_human_mode.py`（30 例），单元测试总数 31 → **61**。

### 修复

* **一致性校验误报**：重复组合的位置若反馈全是 `MISPLACED`，会被旧实现误判为“与 P/W 混用”。
  实测中真实反馈被误报，已修正为只有“同时出现 M 与 P/W”才报错，
  并把“真实反馈零误报”固化成测试用例。（开发过程中写诊断脚本定位，见 `Doc/07` 第 13 轮）
* **两阶段策略会挪动已确定的位置**：阶段 A 组装批次时会把候选集已收缩为单元素的位置
  也用于试探。现默认沿用其唯一候选，并通过新参数 `lock_known_positions` 保留原行为。
* 严格模式拒绝矛盾反馈时，原先会连带丢掉待反馈的猜测；现在退回该轮后
  **保留同一个猜测继续等待反馈**（`HumanFeedbackGame.reject_last_round()`）。
* 撤销（undo）现在也把被撤销那轮的猜测恢复为待反馈状态，方便修正录入错误。

### 变更

* `two_phase` 新增参数 `lock_known_positions`（默认 `True`）。
  实测（200 局，种子 20260920，规则内）：默认 **6.125 轮**，关闭后 **5.765 轮**（Δ=−0.360，p<0.0001）。
  因此文档中的最优数值更新为 6.125（默认配置），并同时报告 5.765 的变体。
* 实验预设 `two_phase_vs_hybrid` 加入两种 `lock_known_positions` 变体。

## [1.1.0] — 2026-09-20

### 规则变更（破坏性）

* **秘密序列中的组合不再允许重复**：秘密现在由 10 个互不相同的组合构成。
  实现为 `core.game.SECRET_DISTINCT = True`，并新增 `sample_secret()` / `validate_secret()` /
  `is_distinct_sequence()`；`SequenceGame` 默认按新规则生成与校验秘密。
* **猜测序列仍然允许重复**（规则只约束秘密序列），`validate_sequence()` 行为不变。
* 猜测/模拟的默认值随之调整：秘密生成方式默认 `distinct`，
  `--max-rounds` / `HS_MAX_ROUNDS` 默认从 120 降到 40（新规则下对局短得多）。

### 新增

* `PositionBelief(unique_values=...)` 与 `propagate_unique()`：
  把“已安置组合”从所有未定位置的候选中删除并迭代到不动点（依据：重数恒为 1）。
* `SupportTracker(unique_values=...)`：互异规则下 `mark_in` 直接得到重数 1，无需常数探针。
* `two_phase` 新增 `count_policy="auto_exact"`（默认）：规则内直接取重数 1，
  规则外才退化为常数序列探针；阶段 A 新增“已证实 10 个支持集成员即提前结束”的条件；
  阶段 B 新增“轮内不重复取值”的指派约束。
* 五个策略新增 `assume_distinct` 开关（默认 = `SECRET_DISTINCT`），用于规则外对照实验。
* `engine.simulator.SECRET_MODES`：显式区分规则内（`distinct`）与规则外
  （`repeated`、`constant`）三种秘密生成方式；`play_one()` 会按秘密分布自动注入
  `assume_distinct`，避免策略在规则外场景套用不成立的假设。
* `SequenceGame(distinct=...)`：允许构造规则外的对局，并在 `state()` 里回报实际规则。
* 新测试 `TestSecretDistinctRule`（8 个用例）：互异采样、重复秘密被拒、猜测可重复、
  “无 P/W ⇒ 即排列”、“PARTIAL ⇒ 不在支持集”。

### 变更

* `/api/meta` 新增 `secretDistinct` 与 `secretModes`（含 `label` / `inRule` / `help`）。
* `/api/games` 与 `/api/quickplay` 对显式传入的 `secret` 按新规则校验；
  猜测不受限制。
* 实验预设重排：新增 `out_of_rule`（规则外压力测试）与 `repeated_rule`（旧规则对照），
  原 `worst_case` 合并入 `out_of_rule`；各预设的 `secretMode` 改为 `distinct`。
* 前端下拉改为“互异组合（规则内）/ 允许重复（规则外对照）/ 常数序列（规则外压力测试）”。
* 默认参数按新规则实测重调：`adaptive_hybrid.explore_threshold` 10 → 1；
  `particle_entropy.particles` 96 → 64、`refill_attempts` 12 → 4。

### 实测结论（新规则，200 局，种子 20260920）

* **两阶段策略反超成为最优：6.125 轮**（默认配置；把 `lock_known_positions` 关掉可到 5.765 轮，
  该开关在 1.2.0 引入）；逐位置熵贪心 6.260、自适应混合 6.550、
  朴素贪心 6.775、粒子滤波 7.045；固定/随机基线仍为 0% 解出率。
* `two_phase` 的 `exact` / `skip` / `auto_exact` 三种取值结果**逐局完全相同**（6.125 轮，p = 1.0000）——
  互异规则下重数探测彻底失去意义。
* `global_prune` 的边际价值从旧规则的 −2.35 轮降到 −0.30 轮，
  因为“已安置值全局传播”接管了主要剪枝职责。
* 同一套代码在旧规则下的最优是逐位置贪心（6.460），两阶段最差（12.785）：
  **性能排名高度依赖规则，必须连规则一起报告。**

## [1.0.0] — 2026-09-20

### 新增

**规则层 `core/`**
* `core/game.py`：组合空间（55 全组合 / 10 删除 / 45 候选）、`evaluate()` 四级优先级反馈判定、
  组合解析与序列校验、`shares()`、序列序列化工具。模块级 `assert` 锁定基数。
* `core/sequence_game.py`：`SequenceGame` 状态机（提交、历史、胜负、序列化）与 `play_auto()`。
* `core/journal.py`：按功能把运行期信息写入 `Doc/08_对局与模拟日志.md` 与 `Doc/_data/events.jsonl`。

**策略层 `strategies/`**
* `base.py`：`Strategy` 接口（`next_guess(history)`）、`ParamSpec` 参数声明、`_sync()` 增量吸收。
* `belief.py`：`PositionBelief`（逐位置候选集合、四条反馈推理、`global_prune`、位掩码缓存）、
  `SupportTracker`（支持集状态与精确重数、常数序列探针识别）、`NEIGHBOR_MASK`、`shannon()`。
* 7 个策略：`random`、`fixed_cycle`、`naive_position`、`two_phase`、
  `position_entropy`、`adaptive_hybrid`、`particle_entropy`。
* `__init__.py`：策略注册表 + `catalog()` / `create()` + `DEFAULT_PLAN_KEYS`。

**实验层 `engine/`**
* `simulator.py`：配对模拟（`make_secrets` / `play_one` / `run_benchmark` / `replay_transcript`）、
  `assign_plan_ids()` 支持同策略多参数变体、可中断。
* `metrics.py`：均值/中位数/P90/P99/最坏/标准差/直方图/耗时、截尾口径、精确双侧符号检验与两两配对比较。
* `jobs.py`：线程 + 信号量后台任务，进度、取消、结果缓存与回收。

**Web 层 `web/` + `templates/` + `static/`**
* `web/api.py`：元信息、策略目录、实验预设、对局 CRUD/step/play、quickplay、
  模拟任务提交/查询/取消/导出（CSV 含 `per_game`/`aggregate`/`pairwise` 三段）、历史运行。
* `web/pages.py`：`/`（沙盒）、`/strategies`（策略库）、`/lab`（模拟实验台）。
* 前端：原生 JS + 手写 SVG 图表，无 CDN 依赖；策略参数控件由后端 `params_spec` 自动生成。

**测试与脚本**
* `tests/test_game.py`（14 例，规则与反馈语义）、`tests/test_strategies.py`（9 例，接口契约与性能冒烟）。
* `experiments/benchmark.py`：命令行基准，支持 `--preset`、`--all-presets`、
  `--plan "key:param=value"` 语法。

**文档 `Doc/`**
* `README.md`（索引）、01 项目总览、02 游戏规则与反馈定义、03 策略库说明、
  04 模拟引擎与评估指标、05 Web 界面与 API、06 实验结论与复现指南、07 交互与开发记录、
  09 常见问题与排错、CHANGELOG。

### 修复 / 优化（开发过程中）

* **规则用例修正**：`test_partial_requires_unique_value` 最初把 `(1,3)` 误判为应为 `MISPLACED`；
  正确语义是“`M` 取决于该组合**是否属于支持集**”，已改用正确构造的用例。
* **结果键冲突**：同一策略的不同参数变体会在报告中互相覆盖 → 新增 `assign_plan_ids()`
  与前端“+ 参数变体”行模型。
* **轮内重复组合**：`position_entropy` / `adaptive_hybrid` 原先在 10 个位置全部选完后才统一计数，
  导致一条猜测里可能重复使用同一组合，浪费支持集分类机会 → 改为轮内维护 `used` 本地副本并支持 `avoid`。
* **新增 `split` 取值准则**：优先选能把候选集合二等分的未测试组合，`position_entropy` 7.53 → 6.46 轮。
* **参数默认值按实测调整**：`adaptive_hybrid.explore_threshold` 6 → 10；
  `particle_entropy.particles` 160 → 96、`candidate_pool` 48 → 32（步数无显著变化，耗时约减半）。
* **前端缺陷**：`index.html` 缺少 `#strategyParams` 容器导致沙盒页脚本报
  `Cannot set properties of null` → 补齐容器与 `.params-inline` 样式。
* **模板缓存**：非 debug 模式下 Jinja 缓存模板导致改动不生效 → `app.py` 开启
  `TEMPLATES_AUTO_RELOAD` 并关闭静态缓存。
* **CSV 导出**：`per_game` 段的耗时列原先为空 → 报告中新增 `timesMs` 并导出真实毫秒值。
* **实验台可读性**：汇总表与柱状图按平均步数升序排列，最优值绿色高亮。

### 实测结论（详见 Doc/06）

* 最优策略 `position_entropy[pick_mode=split, global_prune=True]`：**平均 6.46 轮 / 1.70 ms 每局**（200 局，种子 20260920）。
* 全局剪枝是单项收益最大的启发式：−2.35 轮。
* 严格两阶段（含精确重数探测）12.79 轮；去掉重数探测后 6.99 轮。
* `particle_entropy` 8.16 轮 / 65.7 ms，四种评分目标差异均不显著，粒子数增加无收益。
* 常数序列秘密下排名反转：两阶段策略反而最优（2.78 轮）。
