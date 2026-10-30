# 验证证据索引

每条证据都是**可复现的命令 + 可核对的输出**。命令在仓库根目录或注明的工作目录下执行；输出是 2026-10-30 在本机实测的原样摘录，数字变了就是有回归。

## 环境

| 项 | 值 |
|---|---|
| OS | Windows 10 |
| Python | 3.12（`api/.venv`） |
| Node | 20+（`web/`） |
| 业务库 | MySQL 8 @ 127.0.0.1:3306（workspace 1：1 家店 / 1 个商品 / 7 条 Agent 任务 / **无经营数据**） |
| 分析仓 | DuckDB 2.0.0，`api/warehouse/workspace_1.duckdb` |
| 模型 | qwen-plus（DashScope OpenAI 兼容端点） |

## E01 后端全量测试

```powershell
cd api
.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp
```

```
459 passed in 57.00s
```

`--basetemp` 必须给：默认 tmp 目录在 Windows 上会被沙箱拦截清理，报 `PermissionError: WinError 5`，与代码无关。

## E02 新增模块测试（53 项）

```powershell
cd api
.venv\Scripts\python.exe -m pytest tests/test_data_platform_pipeline.py tests/test_data_platform_query_guard.py tests/test_data_platform_api.py tests/test_nl2sql.py -q --basetemp=.pytest-tmp
```

| 文件 | 数量 | 覆盖什么 |
|---|---|---|
| `test_data_platform_pipeline.py` | 12 | 四层建表齐备；重复导入按主键去重且保留最新；比率先聚合再相除；窗口右端取仓内 `max(date)`；重跑幂等；租户分文件；超卖计为低库存；质量校验能抓到异常行；8 个口径全部可执行；血缘覆盖每张非 ODS 表；未跑批时 `is_ready()` 为假；`store_count` 数维度不数 KPI 行 |
| `test_data_platform_query_guard.py` | 16 | 10 条危险/越权 SQL 被拒（`DROP`/`ATTACH`/`read_csv`/`pragma`/跨租户表/多语句…）；`WITH` + 注释合法放行；`SELECT 1` 放行；超限截断并置 `truncated`；`read_only=True` 连接直接拒绝写入；仓不存在抛 `FileNotFoundError` |
| `test_data_platform_api.py` | 6 | 未跑批 `200` 且 `ready=false`；跑批后 7 个读端点全通；未知指标 `404`；未跑批查询 `409`；危险 SQL `400` / 合法 `200` / 语法错 `400`；viewer 读 `200` 写 `403` |
| `test_nl2sql.py` | 19 | 目录含 21 张业务表且排除 `meta_*`；字段释义进目录；模型 SQL 执行成功；护栏拒绝后带原因重试一次；执行报错后重试；两次都失败抛 `NL2SQLRejected` 且带两条原因；空 SQL 重试后失败；未跑批抛 `WarehouseNotReady`；提问长度上限；SQL 归一化 4 例；接口 200 / 409 / 422 / examples / viewer 403 |

## E03 对真实 MySQL 跑批（不是 SQLite 夹具）

```powershell
cd api
.venv\Scripts\python.exe -c "from app.db.session import SessionLocal; from app.dataplatform.pipeline import run_pipeline; from app.dataplatform import service; db=SessionLocal(); r=run_pipeline(db,1); s=service.overview(1).summary; print(r.duration_ms, r.ods_rows, r.tables_built, r.checks_passed, r.checks_failed); print(s.store_count, s.product_count, s.gmv_7d)"
```

```
242 13 21 9 0
1 1 0.0
```

层行数：ODS 13、DWD 8、DWS 4、ADS 2。`21 = 8 ODS + 5 DWD + 4 DWS + 4 ADS`。

`gmv_7d = 0.0` 是对的：这个 workspace 里一条经营数据都没有。空数据不报错、不显示假数字，看板写「暂无经营数据」，这正是要验证的降级路径。

## E04 指标口径（夹具库，含真实成交数据）

真实库里没有经营数据，口径正确性用夹具验：1 家店 / 1 个商品 / 3 天经营记录（`revenue` 合计 1700、`spend` 合计 300、30 单）。

```
gmv_7d=1700  spend_7d=300  roas=5.67  ctr=0.1  cvr=0.1  aov=56.67
```

手算校验：`1700 / 300 = 5.67`（先聚合再相除）；`aov = 1700 / 30 单 = 56.67`。

## E05 只读护栏的两层各自独立生效

```powershell
cd api
.venv\Scripts\python.exe -m pytest tests/test_data_platform_query_guard.py -q -k "readonly or forbidden or rejects" --basetemp=.pytest-tmp
```

- 文本护栏层：`guard_select("select * from secrets")` → 抛 `QueryRejected`，命中的是表白名单规则。
- 连接层：绕过文本护栏直接 `connect(ws, read_only=True).execute("create table x(a int)")` → DuckDB 自己报只读错误。

## E06 前端类型与构建

```powershell
cd web
npx tsc --noEmit      # 无输出，exit 0
npx next build        # ✓ Compiled successfully；/data-platform、/ask、/ask/query 均标为 ƒ (Dynamic)
```

## E07 数据中台页面渲染完整性（防「只出骨架」回归）

```powershell
# 1. 起生产构建（务必带 NO_PROXY，否则 SSR 回访 127.0.0.1 会被代理吞掉鉴权头）
$env:API_INTERNAL_BASE_URL="http://127.0.0.1:8001"
$env:NO_PROXY="127.0.0.1,localhost,::1"
npx next start -p 3113

# 2. 带会话 Cookie 取页面
curl.exe -s --noproxy "*" -H "Cookie: eo_auth_token=<token>; eo_workspace_id=1" `
  http://127.0.0.1:3113/data-platform -o dp.html
```

判定：`dp.html` 约 **120 KB**，以下标记全部命中。

```
近 7 天成交额 / 低库存 SKU 占比 / Agent 任务成功率 / 指标口径 / 成交额趋势
质量校验 / 表级血缘 / 先聚合再相除 / 上游 / ods_performance
```

**回归信号**：如果只有约 70 KB、且上面只命中前 3 条，说明 `Card` 又把 children 吞了——去查 `web/src/components/ui/card.tsx` 里的 `Card` / `CardHeader` 是否漏写 `{...props}`。这个 bug 不报错、不触发 error boundary，RSC payload 里表现为 `["$","div",null,{"data-slot":"card",...}]` 没有 `children`。

## E08 问数页面渲染完整性

```powershell
curl.exe -s --noproxy "*" -H "Cookie: eo_auth_token=<token>; eo_workspace_id=1" `
  http://127.0.0.1:3114/ask -o ask.html
```

判定：约 **61 KB**，命中 `问数` / `仓已就绪` / `直接用中文问` / `模型只负责写 SQL` / `问题 → SQL` / `先过护栏，再执行` / `说得清怎么算的` / `ads_ops_daily` / 示例问题原文 / `两次都生成不出`。

## E09 真实模型问数（不是 mock）

```powershell
cd api
.venv\Scripts\python.exe -c "from app.nl2sql import service; r=service.ask(1,'Agent 任务失败最多的那天是几号，失败率多少'); print(r.sql); print(r.rows)"
```

```
SELECT date AS 日期, failed AS 失败数, success_rate AS 成功率, (1 - success_rate) AS 失败率
FROM dws_agent_daily ORDER BY failed DESC LIMIT 1

[(datetime.date(2026,10,23), 3, 0.0, 1.0)]
```

「近 14 天成交额」那条也实测过（4.0 s，返回 0 行，因为该 workspace 没有经营数据），SQL 正确遵守了「窗口右端取 `max(date)`」的规则：

```sql
SELECT date AS 日期, gmv AS 成交额, spend AS 消耗, roas AS ROAS FROM ads_ops_daily
WHERE date > (SELECT coalesce(max(date), DATE '1970-01-01') FROM ads_ops_daily) - INTERVAL '14 days'
ORDER BY date
```

## E10 字段释义带来的可测量改进

同一句问题、同一个模型，只加了 `catalog._COLUMN_NOTES` 里的一行释义（`success_rate` 是成功率、失败率 = 1 - success_rate）：

| | 生成的 SQL 关键片段 | 结果 |
|---|---|---|
| 加释义前 | `success_rate AS 失败率` | 列名对、口径错，**不报错** |
| 加释义后 | `success_rate AS 成功率, (1 - success_rate) AS 失败率` | 口径正确 |

这是 P03 里唯一一处「模型判断」被真正约束住的地方，也是加字段释义而非全量数据字典的理由：只登记名字会骗人的列。

## E11 自建 RAG 的真实链路（入库 → 检索 → 引用）

不 mock 向量端点，用真实 `text-embedding-v4` 跑一遍：

```bash
# 入库：两篇运营资料
POST /api/v1/rag/documents   → 201
{"document":{"id":2,"title":"低糖燕麦饼干 10 月运营复盘","char_count":329,
             "chunk_count":1,"embedded_chunks":1,"embedding_model":"text-embedding-v4"}}

GET /api/v1/rag/stats
{"documents":2,"chunks":2,"embedded_chunks":2,"embedding_coverage":1.0,
 "embedding_configured":true,"embedding_model":"text-embedding-v4"}
```

三组检索，名次全部命中正确的文档：

| 问题 | 首条命中 | 词法命中 | 向量命中 | 耗时 |
|---|---|---|---|---|
| 素材哪一版更好 | 低糖燕麦饼干 10 月运营复盘 | 1 | 2 | 884 ms |
| 竞品价格怎么应对 | 竞品价格监控周报 | 2 | 2 | 686 ms |
| 缺货要不要紧 | 低糖燕麦饼干 10 月运营复盘 | **0** | 2 | 685 ms |

第三条是向量通路真实生效的证据：BM25 一条都没召回（原文没有"缺货"二字，只有"可用量只剩 18 件""低于预警阈值""补货"），全靠语义召回。

引用可逐字核对——`char_start` / `char_end` 是原文切片位置：

```json
{"citation":{"document_id":2,"document_title":"低糖燕麦饼干 10 月运营复盘",
             "ordinal":0,"char_start":0,"char_end":329}}
```

前端生产模式（`next start`）抓取 `/rag`：HTTP 200、66,506 字节，`知识库` / `自建切片与混合检索` / `两路召回` / `字符区间` / 两篇文档标题 / `text-embedding-v4` 全部出现，且**没有** `Switched to client rendering because the server rendering errored`——服务端渲染完整。

代理链路一个来回：`POST /rag/documents` → **201**（id=4，36 字，1 片，已向量化）→ `DELETE /rag/documents?id=4` → **204** → 同 id 再删 → **404** → `stats` 回落为 `documents:2, chunks:2`（切片随文档级联删除，无孤儿）。

## E12 切片去重的回归对照

同一篇 329 字的复盘，改切片收尾逻辑前后的差别：

| | 切片区间 | 后果 |
|---|---|---|
| 改前 | `[0,284]`、`[181,329]` | 重复 103 字；两片互抢名次，问「素材哪一版更好」时把库存片段排到第一 |
| 改后 | `[0,329]` | 一篇一片，三个问题的首条命中全部正确 |

原因：`target=320` 被当成了硬切点。它是软上限——剩余内容一屏装得下就整段收尾。测试 `test_tail_is_not_split_into_near_duplicate_chunk` 钉住这个行为。

## E13 向量端点抖动：从 21 秒到 0.9 秒

真机实测到的一次网络抖动（连接阶段超时），改前改后的差别：

| | 单次检索耗时 | 用户体验 |
|---|---|---|
| 改前（`timeout=30s`，无熔断） | **21,451 ms**，返回 `warnings: ["向量端点不可用：ConnectTimeout"]` | 结果正确，但等了 21 秒——等于不可用 |
| 改后（连接 3s / 读 8s + 30s 熔断） | **686–884 ms**（正常时） | 抖动最坏多花 3 秒后降级为词法 |

两条约束都写进了代码而不是文档：`CONNECT_TIMEOUT_SECONDS = 3.0` 必须单独收紧（抖动卡在握手阶段时默认值会拖成黑洞），失败后 `BREAKER_COOLDOWN_SECONDS = 30.0` 内不再建连接（抖动成串出现，逐次重试会让整段时间里所有请求都变慢）。测试 `test_embedding_failure_opens_breaker_and_skips_the_next_attempt` 断言第二次调用**根本不建连接**，`test_embedding_success_clears_breaker` 保证恢复后自动重试、不会因一次失败永久关掉向量。

## E14 销售考核（P09）的真实一轮

导入 5 条客服会话（`POST /api/v1/qa/import`），全部即时评分：

```json
{"imported": 5, "updated": 0, "skipped": 0, "scored": 5, "warnings": []}
```

汇总与排行榜：

```json
GET /qa/summary?days=30
{"sessions":5,"scored":5,"average":58.0,"agents":2,"redline_sessions":1,
 "grade_distribution":{"A":2,"B":1,"C":0,"D":2},"rule_version":"1.0.0"}

王倩: avg=91.7 sessions=3 A=2 redline=0 best=100.0 worst=75.0
李强: avg=7.5  sessions=2 A=0 redline=1 best=15.0  worst=0.0
```

同样 5 条会话重复导入 → `{"imported":0,"updated":5,"skipped":0}`，会话总数仍是 5（幂等）。

红线会话（`dy-20261029-004`）的报告：

```json
GET /qa/sessions/4
{"score":{"total":0.0,"grade":"D","redline":true},
 "redline_evidence":[{"key":"absolute_promise","title":"绝对化承诺","seq":1,
                      "excerpt":"百分百没添加剂，绝对安全，放心吃","phrase":"百分百"}],
 "suggestions":["绝对化承诺是红线：不要承诺效果（「百分百」「保证」「绝对」），改成描述可核实的事实",
                "答完问题要带一句下一步，否则客户问完就走了",
                "开头一句问候、结尾一句「还有其他可以帮您吗」，成本很低"]}
```

逐项明细仍然保留（响应 30/30、首响 20/20、需求 25/25、推进 0/15、礼貌 0/10）——**总分归零但要说清本来多少分**。

低分样本导出（`GET /qa/export?threshold=60`，4,149 字节，带 `Content-Disposition: attachment`）：

```
dy-20261029-004  0.0  D  redline=True   msgs=2 issues=[next_action, courtesy]      evidence=[百分百]
dy-20261029-003 15.0  D  redline=False  msgs=3 issues=[completeness, first_response, requirement_hit, next_action, courtesy]
```

导出带原文与未通过项，可直接做复盘素材或微调数据集（对齐 P09→P10 的"低分回流"）。

从本仓会话表拉取（当时还没有 P01，所以没有会话）：

```json
POST /qa/import/agent-conversations
{"imported":0,"skipped":0,"scanned":0,
 "reason":"本仓没有客服会话，无法拉取（不是导入失败，是没有数据）"}
```

> P01 上线后这条链路有数据了：同一接口回 `{"imported":11,"scanned":11,"skipped":0}`，见 E16。

前端生产模式抓取 `/qa`：HTTP 200、88,087 字节，`销售考核` / `坐席排行榜` / `王倩` / `李强` / `等级分布` / `规则版本 1.0.0` / `会话与个人报告` / `拉取仓内会话` / `导出低分样本` 全部出现，且**没有** `Switched to client rendering`。三个代理路由实测：`/qa/report/4` 回 total=0 且带红线证据；`/qa/action` 重算回 `{"rescored":5}`；`/qa/action` 未知动作回 **400**。

> 规则升到 1.0.1 之后同一页面是 102,322 字节，`规则版本 1.0.1` 与 `AI 助手 85.5` 出现，同样无客户端降级（见 E18）。

测试：`tests/test_qa.py` **35 项**（规则边界 11、服务与接口 24）。

## E15 「需求覆盖」的返工：从字面交集到问题类型

同一条会话，只改判据：

| | 客户发言 | 坐席回复 | 需求覆盖得分 |
|---|---|---|---|
| 改前（bigram 交集） | 这个低糖饼干还有货吗，多少钱 | 您好，有的，29.9 元一盒，可以帮您留货… | **0 / 25**（回复没重复商品名） |
| 改后（问题类型覆盖） | 同上 | 同上 | **25 / 25**（问了价格+库存，两样都答到） |

改前整条会话得 75 分（A 掉到 B），人看着明显是误判。这条对照说明：判据要落在语义类别上，不能落在字面重合上——和 ADR 0008 里"字段释义只登记名字会骗人的列"是同一个教训的两面。

## E16 AI 电商客服（P01）的四条出口，逐条走一遍

问题先判「该不该答」，再判「答不答得出来」。四条出口顺序固定：转人工 → FAQ → 检索 → 拒答。

同一轮里问 5 句，落在四个轨道：

```json
POST /api/v1/cs/chat   {"message": "..."}
```

| 提问 | 轨道 | 落点 |
|---|---|---|
| 多久发货？ | `faq` | FAQ「发货 / 多久发货？」（关键词：多久发货） |
| 我要投诉，你们发的是假货 | `handoff` | 工单 `complaint` / `high` |
| 缺货要不要紧 | `retrieval` | 「低糖燕麦饼干 10 月运营复盘」第 1 片，相似度 0.4338 |
| 你们公司什么时候上市 | `refuse` | 最接近相似度 0.27 < 0.36 |
| 包邮吗？ | `faq` | FAQ「运费 / 包邮吗？运费多少？」（关键词：包邮） |

汇总（同期累计）：

```json
GET /api/v1/cs/summary
{"conversations":11,"messages":22,"handoff_tickets":3,"open_tickets":1,
 "tracks":[{"track":"handoff","count":3},{"track":"faq","count":3},
           {"track":"retrieval","count":3},{"track":"refuse","count":2}],
 "faq_items":10}
```

工单幂等：同一会话重复触发转人工只建一张。关单实测：

```
POST /api/v1/cs/tickets/3/close
created_at= 2026-10-30T20:21:25
closed_at = 2026-10-30T20:21:25
delta_sec = 0.0
```

（一开始不是 0。`created_at` 是 `server_default=func.now()` 走数据库时钟（本地 +8），
`closed_at` 用 Python 的 `datetime.now(UTC)`，同一行里两个字段差了 8 小时，
`closed_at - created_at` 是 **-28800 秒**。改成 `func.now()` 后归零，并加了断言锁住。）

前端生产模式抓取 `/cs`：HTTP 200、98,254 字节，`AI 电商客服` / `三轨道` / `宁可拒答` /
`转人工工单` / `内置 FAQ 清单` / `多久发货？` 全部出现，且**没有** `Switched to client rendering`。
两个代理路由实测：`/cs/chat` 分别回 `faq`（引用 1 条）、`retrieval`（相似度 0.4338）、
`refuse`（0.27 < 0.36）、`handoff`（工单 #2）；`/cs/tickets/2/close` 回 **200**、`status=closed`。

## E17 拒答线 0.36 的来源：分数分布实测

不是估的。两篇真实运营资料入库后，问 4 组相关问题、4 组无关问题，看 top1 原始分数：

| 问题 | 词法分 | 向量相似度 | 该不该答 |
|---|---|---|---|
| 竞品价格怎么应对 | 2.1995 | 0.6044 | 该 |
| 低糖饼干卖给谁 | 0.8979 | 0.7030 | 该 |
| 缺货要不要紧 | — | 0.4338 | 该 |
| 素材哪一版更好 | 1.9199 | 0.3882 | 该 |
| 快递几天能到 | — | 0.3378 | 不该（FAQ 里答） |
| 我要退货怎么操作 | — | 0.3234 | 不该（FAQ 里答） |
| 你是谁开发的 | — | 0.2590 | 不该 |
| 今天天气怎么样 | — | 0.2152 | 不该 |

相关最低 **0.3882**、无关最高 **0.3378**，空隙只有 0.05，阈值取中点 **0.36**。

真实拒答回复（原文，含相似度）：

```
这个问题我查不到可靠的依据，先不猜答案。知识库里没有足够相关的内容
（最接近的一条相似度 0.27，低于 0.36）。

您可以把具体情况说得再细一点，或者我直接帮您转人工客服。
```

两条判据刻意不对称：词法命中只看 `matched_terms` 非空（BM25 分数量纲随语料变，跨资料不可比），
纯向量命中才比 0.36（余弦有稳定量纲）。这张表和阈值写在同一个文件里，因为必须一起维护。

## E18 红线误判：一句「主图回滚」把三条会话判成辱骂

这是 P01 的会话流入 P09 之后才暴露的——同一条数据被两个模块消费，才看得出规则的问题。

P09 导入 11 条 P01 会话后，排行里 AI 助手 `redline_count=3`、`worst=0.0`。查证据：

```json
{"key":"rudeness","title":"失礼或推诿","seq":1,"phrase":"滚",
 "excerpt":"根据我这边查到的资料，可以这样理解： 低糖燕麦饼干的目标人群是关注控糖和代餐的上…"}
```

原句里根本没有骂人的话，命中的是运营复盘原文的「第二版主图**回滚**」。`"滚" in text` 判真，
红线一票否决，整条会话归零。3 条全是同一处误判，1 条都没有真的骂人。

修法不是删关键词（那样真骂人也报不出来），而是**跳过被正常词覆盖的那次命中**：
`REDLINE_BENIGN_COMPOUNDS` 登记 `滚 → 回滚/滚动/滚轮/翻滚`，`保证 → 保证金/保证书`，
`无理由 → 天无理由`（7 天无理由是法定权利，不是绝对化承诺）。同时把裸「滚」换成
`滚开/滚蛋/滚出去/滚远点`。`RULE_VERSION` 从 `1.0.0` 升到 `1.0.1`。

重算（`POST /qa/rescore` → `{"rescored":16}`）前后对比：

| | 修复前（1.0.0） | 修复后（1.0.1） |
|---|---|---|
| AI 助手 均分 | **60.9** | **85.5** |
| AI 助手 最低分 | **0.0** | **65.0** |
| AI 助手 红线数 | **3** | **0** |
| AI 助手 A 级数 | 6 | 9 |
| 李强 均分 / 红线 | 7.5 / 1 | 7.5 / 1（真红线，不变） |
| 王倩 均分 | 91.7 | 91.7（不变） |

一次误判让 3/11 条会话作废、均分被拉低 24.6 分。红线是一票否决，**误判的代价不对称**——
正常会话被判红不只是分数错，报告里会赫然写着「失礼或推诿」，看的人第一反应是系统坏了。

回归测试 4 项：`回滚` 不判红线、真骂人仍判红线、`7 天无理由退换` 不判绝对化承诺、
客户骂人不算坐席的错（改用 `滚蛋` 后仍成立）。`tests/test_qa.py` **38 项**、
`tests/test_cs.py` **26 项**。

## E19 合同审批（P05）的真实一轮：规则分与语义分不能混着看

上传一份含典型陷阱的采购合同（`POST /contracts/upload`，1,187 字节 `.txt`），同步切条款 + 跑规则：

```json
{"id":1,"filename":"contract_sample.txt","clause_count":10,"risk_count":8,
 "high_risk_count":5,"rule_score":100,"semantic_score":0,"risk_score":100,"risk_level":"high"}
```

再跑一遍语义复核（`POST /contracts/1/review`，真实模型 17.054s）：

```json
{"added":5,"dropped":0,"semantic_score":100}
```

**`dropped=0` 是这里最该看的一个数**：模型给的每条风险都带 `quote`，而 `quote` 必须逐字出现在原文里
（`_normalize_for_match(quote) in haystack` 才算通过）。导不出来的直接丢，不让模型凭印象编条款。

复测"高风险必须写理由"：

```json
POST /contracts/2/approvals  {"decision":"approved","comment":""}
→ HTTP 422 {"detail":"这份合同有 2 条高风险，通过时必须写明理由"}
```

**第二份合同（`clean.txt`）暴露了这套设计的关键分叉：**

| 合同 | 条款 | 规则分 | 语义分 | 合计 | 定级 |
|---|---|---|---|---|---|
| contract_sample.txt | 10 | **100** | **100** | 100 | high |
| clean.txt | 7 | **0** | **92** | 92 | high |

`clean.txt` 是按"没有账期过长/定金过高/单方解除/境外管辖"写的一份干净合同，规则一条都没报（`rule_score=0`），
但模型仍然挑出 4 条真问题：

```json
[{"risk_key":"penalty_only_binds_seller","quote":"乙方逾期交货……应向甲方支付违约金",
  "note":"违约金条款只约束乙方，甲方逾期付款无对应责任"},
 {"risk_key":"payment_decoupled_from_delivery","quote":"合同签订后 30 日内付清全款",
  "note":"付款节点早于交付与验收，钱先付完再收货"}]
```

如果只有一个总分，`clean.txt` 会是"规则 0 分 → 低风险"，而它其实有两条实打实的单向条款。
**所以两段分必须分开记、分开显示**：规则分回答"踩了我们登记过的哪几条线"，语义分回答"模型读出了哪几条原文里的单向约定"，
两者的分母不一样，加起来没有意义。前端在合同卡片上直接写「规则 0 + 语义 92」。

审批链留痕（同一份合同两条记录，快照各 13 条风险）：

```json
POST /contracts/1/approvals {"decision":"changes_requested","comment":"账期与定金先谈"}
→ 201 {"decision":"changes_requested","risk_snapshot":{...13 条...}}
POST /contracts/1/approvals {"decision":"approved","comment":"已谈到 45 天，先用这一版"}
→ 201 {"decision":"approved","risk_snapshot":{...13 条...}}
```

生产构建抓 `/contracts`：**123,763 字节**，`合同审批` / `条款风险与留痕审批` / `账期 90 天` /
`质量责任判定权单方归属` / `不替人做通过` 全部在首屏，无 `Switched to client rendering`。

`tests/test_contracts.py` **34 项**，含 `test_semantic_review_drops_fabricated_quotes`
（模型编一条不存在的引文 → 必须被丢掉并计数）与
`test_approving_a_high_risk_contract_requires_a_comment`。

## E20 直播切片（P08）的真实一场：48 句转写 → 8 条候选

灌一场 239 秒、48 句的带货转写（`POST /live/sessions`）：

```json
{"id":1,"title":"轻氧冰丝防晒衣 晚场直播","duration_seconds":239,
 "status":"cut","clip_count":8,"rule_version":"1.0.0"}
```

前 4 条候选（分数、时间码、命中的词都在响应里，不用再查一遍）：

| # | 时间码 | 分 | 标题 | 理由 |
|---|---|---|---|---|
| 1 | 0:34-0:49 | **81** | 前十名下单加赠一个防晒冰袖 | 卖点词 4 个（只剩/库存/赠/加赠）+ 结尾有行动号召 |
| 2 | 1:24-1:39 | 69 | 限时两小时，两小时后恢复原价 | 卖点词 4 个（原价/最后/限时/手慢无） |
| 3 | 0:19-0:34 | 61 | 原价 199，今天到手价只要 79 | 卖点词 6 个（只要/到手价/原价/立减/折/包邮） |
| 4 | 3:29-3:44 | 61 | 最后再强调一次，79 到手，包邮，送冰袖 | 卖点词 4 个 + 行动号召 |

第 1 条拿到全场最高不是因为卖点词最多（第 3 条有 6 个），而是因为它**同时有行动号召**——
"点击右下角小黄车，直接拍"落在末 80 字里。只有卖点没有落点，观众看完不知道该干什么。

定稿与重挑：

```json
POST /live/clips/1/decide {"status":"approved"}   → 200 decided_at=2026-10-30T21:33:52
POST /live/sessions/1/recut                        → 200 clip_count=8
```

重挑后 clip #1（approved）与 #8（rejected）原样保留，其余 6 条 `suggested` 被清掉重算成新 id。
`GET /live/summary` → `{"sessions":1,"segments":48,"clips":8,"approved":1,"rejected":2,"average_score":60.2}`。

**这一场也暴露了一条边界错误**：候选 #13 的时间码是 3:44-3:59，转录是
「小黄车还有最后 50 件。抢完就下架。**我们下次见。**」——收场语被时间窗带进了切片结尾。
规则从锚点向后扩到 12 秒就停，不判断那句是不是收场。这就是 `decide` 必须存在的理由，
也是 ADR 0017 说"只挑不剪"的原因：边界要人来修。

修好的一处：`decide` 原先只给 `approved` 写 `decided_at`，`rejected` 留 `null`，
导致"这条什么时候被否的"答不上来。改成两种决策都写（仍取数据库时钟，与 `created_at` 同源），
新增测试 `test_rejecting_a_clip_also_stamps_the_time`。`tests/test_live.py` **21 项**。

## E21 微调平台（P10）的真实一轮：13 条样本，11 条被合并

从真实 P09/P01 数据建数据集（`POST /finetune/datasets`，sources=`["qa","cs"]`）：

```json
{"id":3,"sample_count":13,"status":"ready",
 "stats":{"by_source":{"qa":13},"by_quality":{"positive":11,"needs_fix":2},
          "duplicates":13,"duplicates_by_source":{"cs":11,"qa":2},
          "skipped":{"qa_too_short_or_unfinished":1},
          "min_chars":29,"max_chars":645,"average_chars":176.2}}
```

**`by_source` 只有 qa，而 `duplicates_by_source` 里 `cs: 11`——这两个数必须一起看。**
客服侧不是"没数据"：11 条会话全在，只是它们与质检会话**逐字相同**（同一段对话在两个模块各存了一份），
指纹级去重把它们合并了。先 qa 后 cs 的顺序决定了留 qa 那条——质检会话带分数、等级和红线，
标签比轨道硬。

这个快照字段是被实测逼出来的：第一版只记 `by_source`，接口回 `{"qa":13}`，
看的人以为客服侧根本没接上。静默丢弃是数据平台最坏的失败方式。

导出 JSONL（`GET /finetune/datasets/3/export`）：

```
HTTP 200  8,852 bytes  11 行
{"messages": [{"role": "system", "content": "你是电商店铺的客服。回答要准确、简短、可核实；没有依据时直接说查不到并给出转人工入口，不要猜。"},
              {"role": "user", "content": "包邮吗？"},
              {"role": "assistant", "content": "单笔满 49 元包邮，未满收 6 元运费；……"}]}
```

13 条样本 → 11 行导出，差的 2 条是 `needs_fix`（质检 C/D 级）。**待修样本不进训练集**：
把它们拿去训等于教模型学错误的回答。

任务台账 + 同一批题对照评测：

```json
POST /finetune/jobs            → 201 {"id":1,"status":"registered"}
POST /finetune/jobs/1/status   → 200 {"status":"succeeded","metrics":{"loss":0.87,"eval_loss":0.94}}
POST /finetune/jobs/1/eval     → 200
{"eval_summary":{"cases":4,"base_average":56.25,"tuned_average":77.0,"delta":20.75,
                 "wins":3,"losses":1,"ties":0,
                 "detail":[{"question":"包邮吗？","base_score":60,"tuned_score":85,"delta":25},
                           {"question":"能退货吗？","base_score":70,"tuned_score":68,"delta":-2}, ...]}}
```

`wins=3 / losses=1` 比 `delta=20.75` 更值得看：一条"能退货吗"退步了 2 分。
只报平均分差的话，这一条会被 20.75 盖过去。

`GET /finetune/summary` → `{"datasets":4,"samples":39,"jobs":2,...}`。
页面上没有进度条、没有 loss 曲线——本平台不执行训练，画一条假的比不画更糟。

`tests/test_finetune.py` **26 项**，含 `test_build_dataset_records_why_samples_were_skipped`
与 `test_dedupe_keeps_the_qa_copy_because_its_label_is_harder`。
