# 05 Web 界面与 API 说明

> 实现位置：`web/api.py`（REST）、`web/pages.py` + `templates/` + `static/`（页面）

## 1. 页面

| 路径 | 页面 | 用途 |
| --- | --- | --- |
| `/` | 沙盒 | 手动试玩 + 策略自动对局 + 逐轮反馈可视化 |
| `/human` | **逐步猜测** | 秘密未知，策略出招、**你逐位录入反馈**，直到全部 CORRECT 后落盘（详见 [10 文档](10_逐步猜测模式.md)） |
| `/strategies` | 策略库 | 查看所有策略、参数说明，一键“快速试玩” |
| `/lab` | 模拟实验台 | 配置 → 后台批量模拟 → 指标表 + 图表 + 导出 |

### 1.1 沙盒 `/`

* **左侧候选矩阵**：55 个组合排成 **10×10 表格**（行 = 前一位 `a`，列 = 后一位 `b`）。
  组合是无序对，所以「`a-b`」与「`b-a`」是**同一个组合**、两格状态永远一致，
  点选任意一格即选中规范形式 `(min,max)`（点「6-3」选的是 `(3,6)`）。
  被规则删除的 10 个组合在表里各占 2 格，共 **20 格灰色**（虚线 + 删除线 + 不可点）。
  可用格点选后把该组合填入**下一个空槽**；本轮猜测用到的组合加蓝色描边。
  每轮提交后，按逐位反馈给对应组合着色（详见 [12 文档](12_候选矩阵与状态着色.md)）。
* **中间槽位**：10 个位置，点击槽位可清空；提交后按反馈着色（绿/蓝/紫/红 + C/M/P/W 字母）。
* **右侧历史**：每轮一行，逐位展示 `组合 → 字母`，鼠标悬停有完整说明。
* **工具条**：
  * 模式：`手动试玩` / `策略自动` / `多策略对比`（后者见 [13 文档](13_多策略并排与策略接管.md)，
    同一条秘密下并排跑多个策略）；
  * `策略自动` 会显示策略下拉与**参数输入控件**（控件由后端 `params_spec` 自动生成）；
  * `手动试玩` 会显示「把当前历史交给策略」：选策略后点「让策略预测下一步」，
    该策略基于同样的历史给出猜测并附上逐位理由（`POST /api/advise`）；
  * 秘密种子：留空即随机；填数字可复现；
  * 轮次上限；“显示秘密”开关（勾选后重新拉取带 `reveal=1` 的状态）；
  * 按钮：开新局 / 提交猜测（自动模式为“走一步”，多策略为“所有策略各走一步”）/ 自动跑完 / 清空槽位；
  * 会话内按 `Enter` 等价于“提交猜测”。
* 两种单策略模式每轮的决策解释会写进 `history[i].decision`，界面上可逐轮展开
  （见 [14 文档](14_决策解释与逐步理由.md)）。
* 手动模式会**逐轮**写入 `Doc/08_对局与模拟日志.md`（可用 `HS_DOC_LOG_ROUNDS=0` 关闭）。

### 1.1.1 候选矩阵的状态着色

| 反馈 | 颜色 | 含义 |
| --- | --- | --- |
| `CORRECT` | 绿 | 该位置就是它（组合已就位） |
| `MISPLACED` | 蓝 | 组合属于支持集，但不在此位置 |
| `PARTIAL` | 紫 | 组合不属于支持集（与本位至少共享一个对象） |
| `WRONG` | 红 | 组合不属于支持集 |

同一组合被多轮标记时按 `CORRECT > MISPLACED > PARTIAL > WRONG` 取最强的一个显示，
全部记录写在 tooltip 里。**颜色只是备忘，不影响点选**：
红色/紫色组合依然可以选（工具不强制剪枝）。

### 1.2 策略库 `/strategies`

7 张策略卡片，展示 key / 标签 / 说明 / 参数控件 / 建议轮次上限 / 实现位置；
每张卡片可“快速试玩”一局（走 `/api/quickplay`），并在下方渲染完整轨迹。

### 1.3 模拟实验台 `/lab`

1. **实验配置**：每策略局数、随机种子、轮次上限、秘密生成方式；
   策略行支持勾选、改参数、**“+ 参数变体”**（同一策略用不同参数再跑一遍）、移除；
2. **开始/取消** + 进度条（轮询任务状态）；
3. **汇总指标表**（按平均步数升序，最优值绿色高亮）；
4. **步数对比图**（横向柱 = 平均步数，刻度 = 中位数/P90/P99/最坏）与**步数分布直方图**；
5. **两两配对比较表**（含 p 值与显著性标记）；
6. **导出 CSV / JSON**、**历史运行**列表。

顶部“实验预设”下拉直接对应四个核心问题，点“载入预设”即可一键配置。

## 2. REST API

所有响应均为 UTF-8 JSON；错误统一返回 `{"error": "..."}` 加合适的状态码。

### 2.1 元信息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/meta` | 对象集合、55 组合、10 删除组合、45 候选、反馈取值与含义、当前规则（`secretDistinct`）、可选秘密生成方式（`secretModes`）、默认上限 |
| GET | `/api/strategies` | 策略目录（key/name/description/tags/params/source）+ 默认计划 |
| GET | `/api/experiments/presets` | 预置实验方案（对应四个核心问题） |
| GET | `/api/health` | 健康检查 |

### 2.1.1 对局与会话

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/games` | 开新局。`mode`：`manual` / `strategy` / `multi`；
  `strategy` + `params`（单策略）；`multi` 时用 `plan: [{key, params}]`（多策略并排） |
| GET | `/api/games/<gid>` | 取状态（`?reveal=1` 带秘密）；多策略会话返回 `entries[]` |
| POST | `/api/games/<gid>/step` | 推进一步（多策略：空 body = 全部，`{"only": i}` = 单个） |
| POST | `/api/games/<gid>/play` | 自动跑完 |
| DELETE | `/api/games/<gid>` | 删除会话 |
| POST | `/api/advise` | **把一段对局历史交给策略**，返回它下一步会怎么猜 + 逐位理由（见 [13](13_多策略并排与策略接管.md)） |
| POST | `/api/quickplay` | 一次性对局，不建会话 |

### 2.2 对局

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/games` | 新建对局 |
| GET | `/api/games/<gid>` | 查询状态（`?reveal=1` 返回秘密序列） |
| POST | `/api/games/<gid>/step` | 推进一轮 |
| POST | `/api/games/<gid>/play` | 自动跑到解出或到上限，返回完整轨迹 |
| DELETE | `/api/games/<gid>` | 删除会话 |
| POST | `/api/quickplay` | 一次性对局（无会话），用于卡片试玩 |

请求示例：

```bash
# 新建：策略自动模式
curl -X POST http://127.0.0.1:5000/api/games -H "Content-Type: application/json" \
  -d '{"mode":"strategy","strategy":"position_entropy","params":{"pick_mode":"split"},"seed":12345,"reveal":true}'

# 手动提交一轮（10 个 [a,b]）
curl -X POST http://127.0.0.1:5000/api/games/<gid>/step -H "Content-Type: application/json" \
  -d '{"guess":[[1,1],[1,2],[1,3],[1,4],[1,6],[1,7],[1,8],[1,9],[1,10],[2,2]]}'

# 策略走一步（不传 guess）
curl -X POST http://127.0.0.1:5000/api/games/<gid>/step -d '{}' -H "Content-Type: application/json"
```

`step` 返回 `{"round": {index, guess, feedback, letters, solved, rounds}, "state": {...}}`；
非法输入（长度不对、组合不在 $S$ 中、秘密含重复组合、已结束、超上限）返回 400 并给出中文原因。

> **秘密校验**：`POST /api/games` 与 `/api/quickplay` 里显式传入的 `secret`
> 会按当前规则校验（**10 个组合必须互不相同**）；猜测 `guess` 不受此限制（允许重复）。
> `/api/games` 不传 `secret` 时按当前规则随机生成互异秘密。

### 2.3 批量模拟

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/simulations` | 提交任务，返回 202 + `{job}` |
| GET | `/api/jobs` | 任务列表 |
| GET | `/api/jobs/<jid>` | 任务详情（`?result=1` 带完整报告） |
| POST | `/api/jobs/<jid>/cancel` | 请求取消 |
| GET | `/api/jobs/<jid>/export?format=csv\|json` | 导出 |
| GET | `/api/runs` | 历史运行列表（`runtime/runs/*.json`） |
| GET | `/api/runs/<rid>` | 某次运行的完整报告 |

任务请求体：

```json
{
  "plan": [
    {"key": "two_phase", "params": {"count_policy": "auto_exact"}},
    {"key": "position_entropy", "params": {"pick_mode": "split"}}
  ],
  "games": 200,
  "seed": 20260920,
  "maxRounds": 40,
  "secretMode": "distinct"
}
```

`secretMode` 可选 `distinct`（默认，规则内）/ `repeated` / `constant`；
后两者属于规则外对照，引擎会自动把策略的 `assume_distinct` 置为 False。

限制：`games ≤ HS_MAX_GAMES`（默认 5000）、`maxRounds ≤ HS_MAX_ROUNDS_LIMIT`（默认 2000）、
计划条目 ≤ 24 个；每次运行的结果会写入 `Doc/08_对局与模拟日志.md` 与 `runtime/runs/`。

## 3. 前端实现说明

* 原生 JS（无构建步骤、无 CDN 依赖，离线可用）：
  `static/js/api.js`（请求与通用渲染）、`combogrid.js`（10×10 候选矩阵，沙盒与逐步猜测共用）、
  `sandbox.js`、`human.js`、`strategies.js`、`lab.js`。
* 图表是手写 SVG（`svgBarChart` / `svgHistogram`），不引入图表库，便于离线与主题统一。
* 反馈配色（全局统一，与候选矩阵一致）：`CORRECT=绿`、`MISPLACED=蓝`、`PARTIAL=紫`、`WRONG=红`，
  在 `static/css/app.css` 里以 `--c/--m/--p/--w` 变量定义；改色请同步 [12 文档](12_候选矩阵与状态着色.md)
  与 `tests/test_ui_matrix.py`。
* 服务端已开启 `TEMPLATES_AUTO_RELOAD` 且禁用静态缓存，改模板/JS 后刷新即可生效。

## 4. 文档日志（Doc/）

`core/journal.py` 负责把运行期信息落到 `Doc/`：

| 文件 | 写入者 | 内容 |
| --- | --- | --- |
| `07_交互与开发记录.md` | 人工/助手 | 提示词与每轮处理反馈 |
| `08_对局与模拟日志.md` | 程序自动追加 | 每轮猜测与逐位反馈、模拟汇总表 |
| `_data/events.jsonl` | 程序自动追加 | 机器可读事件流（`kind`: interaction/game/manual_round/simulation） |

开关：`HS_DOC_LOG`（总开关）、`HS_DOC_LOG_ROUNDS`（是否写逐轮明细）、
`HS_DOC_LOG_ROUND_LIMIT`（单次最多写多少轮）。
