# 电商运营助手（Ecommerce Operation Assistant）

多平台店铺运营工作台：店铺 / 商品 / SKU / 库存、竞品监控、AI 创意与素材、投放实验、Agent 任务与审批、模型用量与费用估算、四层数据中台。

## 技术栈

- 前端：Next.js 16（App Router，服务端组件）+ Tailwind CSS
- 后端：FastAPI + SQLAlchemy + Alembic
- 异步：Celery（Redis broker）+ LangGraph Agent（Postgres checkpointer）
- 存储：MySQL 8（业务）、Redis 7（队列/缓存）、Postgres 16（Agent checkpoints）、DuckDB（分析仓，单文件）

## 目录结构

```
api/                 FastAPI 后端（app/、alembic/、scripts/、tests/）
api/app/dataplatform/ 数据中台：抽取 / 四层建模 / 指标口径 / 质量校验 / 查询护栏
api/warehouse/        DuckDB 仓文件（按 workspace 一文件，git 忽略）
web/                 Next.js 前端（src/app、src/components、src/lib）
docs/adr/            架构决策记录
_evidence/           可复现的验证证据索引
docker-compose.yml   全栈编排：mysql / redis / agent-checkpoints / api / worker / web
.env.example         compose 变量模板（复制为根目录 .env）
web/.env.local       前端本机配置（默认指向 http://127.0.0.1:8001）
```

## 端口

| 服务 | 本地开发 | Docker 全栈 |
|---|---|---|
| Web | 3000 | 3000（`WEB_PORT`） |
| API | **8001** | 8000（`API_PORT`） |
| MySQL | 3306 | 3306（`MYSQL_PORT`） |
| Redis | 6379 | 6379（`REDIS_PORT`） |
| Postgres | 5433 | 5433（`AGENT_POSTGRES_PORT`） |

> 两种模式的 API 端口不同：本地开发用 **8001**（`web/.env.local` 的 `NEXT_PUBLIC_API_BASE_URL`），Docker 全栈用 **8000**（容器内 `http://api:8000`）。不要混用。

## 快速开始

### 方式 A：本地开发（推荐，改代码即时生效）

前置：Python 3.12、Node 20+、Docker Desktop。

```powershell
# 1. 准备配置
copy .env.example .env            # 填 MySQL / Redis / Postgres 口令与 AUTH_SECRET_KEY
copy api/.env.example api/.env    # 填 DATABASE_URL 与 LLM_* 等
# web/.env.local 已指向 http://127.0.0.1:8001

# 2. 一键起全栈（infra → API → worker → web）
powershell -ExecutionPolicy Bypass -File start-dev.ps1
```

`start-dev.ps1` 会依次：起 infra 容器 → 起 API（8001）→ 起 worker → 起 web（3000）。两个子脚本都是幂等的，已在运行则只校正 PID，不重复启动。

手动分步：

```powershell
docker compose up -d mysql redis agent-checkpoints
powershell -ExecutionPolicy Bypass -File api/scripts/start_backend.ps1
powershell -ExecutionPolicy Bypass -File api/scripts/start_worker.ps1
cd web; npm install; npm run dev
```

停止：

```powershell
powershell -ExecutionPolicy Bypass -File stop-dev.ps1   # 停 API + worker
# 加 -Infra 可一并 docker compose down
```

### 方式 B：Docker 全栈

```powershell
copy .env.example .env    # 填口令
docker compose up -d --build
# Web http://127.0.0.1:3000   API http://127.0.0.1:8000/health
```

### 建账号：仓库里没有默认口令

起完服务第一屏是登录页，但仓库里**没有任何明文口令**——`seed_demo_data.py` 只从环境变量读，
读不到就直接退出（脚本里内置口令等于连口令一起提交上去）。所以首次要自己造一个：

```powershell
$env:DEMO_OWNER_PASSWORD = "自己定一个"        # 只对当前终端会话有效
api\.venv\Scripts\python.exe api\scripts\seed_demo_data.py    # 建 owner + 演示工作区（幂等）
```

忘了口令不用重新播种，直接重置：

```powershell
$env:DEMO_OWNER_PASSWORD = "新口令"
api\.venv\Scripts\python.exe api\scripts\reset_demo_password.py
```

两个脚本都会自己切到 `api/` 目录，从仓库根目录执行即可。然后用 `demo.owner@example.com`
加你刚设的口令登录。

登不进去时先看状态码，这两个码分工很明确（实测）：

| 情况 | 状态码 | 说明 |
|---|---|---|
| 口令错 | **401** `Invalid email or password` | 先校口令，此时账号停没停用还看不出来 |
| 账号停用 + 口令对 | **403** `User is disabled` | 走到这里说明口令是对的，问题在账号状态 |

### 快捷登录（演示用，可不配）

自己留一份口令、演示时不想当众敲，可以让服务端替你补这一步。前置是在两个 env 文件里各写一行：

```ini
# web/.env.local —— 决定「登录页要不要出这个入口」
DEMO_LOGIN_PASSWORD=<和你播种时设的一样>

# api/.env.local —— 需要时后端自己也认这个口令
DEMO_OWNER_PASSWORD=<同上>
```

配好重启 `npm run dev`，登录页表单下方多出一块「演示快捷登录」，点一下就进工作台，显示的就是 `demo.owner@example.com`。

几个刻意的设计：

- **口令不下发浏览器**。按钮提交的是 `POST /login/demo`，**不带任何参数**；route handler 从 `process.env` 取口令，再走一遍真实的 `/api/v1/auth/login`。所以登录流水、失败锁定、会话、数据权限都和手输完全一致——它不是绕过认证的后门，只是替你把口令填进那个框。
- **没配 `DEMO_LOGIN_PASSWORD` 就整块不渲染**，页面和正常仓库长得一模一样。想临时关掉，把这一行删掉重启即可，不用改代码。
- **token 照旧写 httpOnly cookie**，`document.cookie` 读不到，跟前台手输没有区别。

生产环境不要配这两个变量。演示账号本身也不该有超出演示范围的数据权限——这一点在 P05 合同系统里是按角色拆开的（管理员看全量、普通员工只看本人）。

## 常用命令

```powershell
# 后端
cd api
.venv\Scripts\python.exe -m pytest -q                 # 测试
.venv\Scripts\python.exe -m alembic upgrade head      # 迁移
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001

# 前端
cd web
npm run dev
npx tsc --noEmit          # 类型检查
npm run lint
```

## 功能概览

- **工作台**：店铺/商品统计、7 日趋势、平台分布、待办横幅
- **待办中心**（`/alerts`）：低库存、竞品变化、失败 Agent 任务、待审批聚合
- **店铺**：库存汇总、低库存、库存流水、补货建议
- **商品**：SKU/库存、竞品监控、创意方案、主图/视频生成、投放实验、复盘、诊断
- **Agent**：任务 / 审批 / 记忆 / 多工作区、SSE 实时事件
- **模型配置**：文本/图片/视频模型、累计用量（token + 生成次数）、费用估算、14 天趋势
- **导出**：商品 / 绩效 / 用量 CSV（UTF-8 BOM，Excel 打开中文不乱码）
- **用户管理**：添加、停用 / 恢复、删除（有审计记录时拒绝物理删除并提示改用停用）
- **数据中台**（`/data-platform`）：ODS/DWD/DWS/ADS 四层建模、指标口径单一真源、表级血缘、9 条质量校验、只读查询框
- **问数**（`/ask`）：中文提问 → 模型写一条 SELECT → 过与手敲 SQL 相同的护栏 → 回填 SQL、口径说明与结果表
- **知识库**（`/rag`）：本仓自建的切片与混合检索，每条命中都能指回原文的字符区间
- **销售考核**（`/qa`）：导入客服会话 → 五条规则评分 + 红线一票否决 → 坐席排行榜与单会话报告
- **AI 客服**（`/cs`）：转人工 / FAQ / 检索三轨道分流，命中不足即拒答，工单与会话落库
- **合同审批**（`/contracts`）：上传即切条款，规则分与模型分两段记，高风险通过必须写理由
- **直播切片**（`/live`）：48 句转写挑出 8 条候选，给时间码与命中理由，不出成片
- **模型微调**（`/finetune`）：样本回流 → 去重分层 → JSONL 导出 → 同一批题对照评测

### 数据中台（P06）

业务库只负责下单与录入，分析查询落在独立的 DuckDB 仓，物理分离——分析慢查询不会拖住业务事务。

```
MySQL ──抽取──► ODS（原样落地）──► DWD（清洗/去重/维度对齐）──► DWS（主题汇总）──► ADS（直接出指标）
```

| 接口 | 作用 |
|---|---|
| `GET /api/v1/data-platform/overview` | 仓是否就绪、上次跑批、四层行数、7 日总览 |
| `GET /api/v1/data-platform/tables` | 各层表清单与行数 |
| `GET /api/v1/data-platform/metrics` | 指标口径目录（定义 + 公式 + 取数表） |
| `GET /api/v1/data-platform/metrics/{key}?days=14` | 单指标日序列（`days` 夹在 1..90） |
| `GET /api/v1/data-platform/lineage` | 表级血缘节点与边 |
| `GET /api/v1/data-platform/quality` | 质量校验结果 |
| `GET /api/v1/data-platform/runs` | 跑批历史 |
| `POST /api/v1/data-platform/runs` | 同步跑一次全量重建 |
| `POST /api/v1/data-platform/query` | 只读 SQL 查询（受双护栏约束，最多 500 行） |

设计取舍见 `docs/adr/`：0001 物理分离、0002 口径单一真源、0003 血缘来自声明、0004 只读双护栏、0005 租户按文件隔离、0006 全量重算。

### 问数 / NL2SQL（P03）

在数据中台之上加一层「用中文问」：模型只负责把问题翻译成一条 SELECT，**执行前必须过和数据中台查询框完全相同的护栏**。

```
问题 ──► 模型 ──► SQL ──┬──► 护栏（单条 SELECT / 表白名单 / 500 行）──► 只读连接 ──► 结果 + SQL + 口径说明
手敲 SQL ──────────────┘
```

| 接口 | 作用 |
|---|---|
| `GET /api/v1/nl2sql/examples` | 6 个示例问题，分别指向 ADS / DWS / DWD，教用户什么时候该下探 |
| `POST /api/v1/nl2sql/ask` | `{"question": "..."}` → SQL、说明、假设、置信度、结果表 |

出口约定：仓未就绪 `409`；模型两次都生成不出安全 SQL `422`（`detail` 里带每轮被拒的原因）；模型未配置 `400`；上游返回非 JSON `502`；当日模型配额用尽 `429`。

两条关键设计：

1. **护栏只有一份**（ADR 0007）。模型是"会写 SQL 的陌生人"，不享受内部通道；换模型不需要重新做安全评估。
2. **字段释义只登记名字会骗人的列**（ADR 0008）。`_COLUMN_NOTES` 只有 7 条，因为实测中 `success_rate AS 失败率` 这类错误不会报错——只能靠释义提前挡住。

设计取舍见 `docs/adr/` 0007、0008。

### 知识库 / 自建 RAG（P04）

`/knowledge` 是把文档交给外部智库服务做解析与索引；`/rag` 是**落在本仓**的一套：切片、向量、检索、引用全部可查，只要数据库就能跑（ADR 0010 说明为什么并存）。

```
文档 ──► 按句切片（char_start/char_end）──► BM25 ──┐
                     └──► 向量（可选）─────────┴──► RRF 融合 ──► 命中 + 引用区间
```

| 接口 | 作用 |
|---|---|
| `GET /api/v1/rag/stats` | 文档数、切片数、向量覆盖率、当前 embedding 模型 |
| `GET /api/v1/rag/documents` | 文档清单（含切片数、已向量化片数） |
| `POST /api/v1/rag/documents` | 入库：`{"title","content","source_kind"}` → 201 |
| `DELETE /api/v1/rag/documents/{id}` | 删文档并级联删切片 → 204（不存在 404） |
| `POST /api/v1/rag/search` | `{"query","top_k"}` → 命中、`char_start/char_end`、两路名次 |

四条关键设计（ADR 0009）：

1. **target=320 是软上限，不是切点**。剩余内容一屏装得下就整段收尾——否则会出现 `[0,284]` + `[181,329]` 这种重复 103 字的近重复片，两片互相抢名次。
2. **向量存 JSON 数组，不引入向量库**。千级切片引入独立服务的成本远大于收益；`MAX_SCAN_CHUNKS = 5000` 是明确的天花板。
3. **RRF 只用名次不用分数**。BM25 分数无界、余弦在 `[-1,1]`，归一化参数会随语料漂移；`k=60` 不需要调参。
4. **向量是增强项，失败必须降级且说清**。没配 `EMBEDDING_MODEL` 就是纯 BM25；端点抖动则连接 3 秒、读 8 秒后放弃，并在 30 秒冷却窗口内不再尝试（实测一次抖动曾让检索卡 21 秒）。三种状态都不需要改配置或重启。

### 销售考核（P09）

导入客服会话 → 规则引擎逐条打分 → 坐席排行榜 + 单会话报告 + 低分样本导出。

```
会话 ──► 五条规则（完整性/首响/需求覆盖/推进/礼貌）──► 总分 + 等级
              └──► 红线（绝对化承诺/私下交易/失礼）──► 一票否决归零
```

| 接口 | 作用 |
|---|---|
| `GET /api/v1/qa/summary?days=30` | 周期会话数、均分、等级分布、红线数 |
| `GET /api/v1/qa/sessions?limit=20` | 会话清单（含分数与等级） |
| `GET /api/v1/qa/sessions/{id}` | 单会话报告：逐项得分 + 证据原句 + 最该改的一项 |
| `POST /api/v1/qa/import` | 导入会话并即时评分（幂等键 `workspace + external_id`） |
| `POST /api/v1/qa/import/agent-conversations` | 从本仓 `agent_conversations` 拉（`user→customer`、`assistant→agent`） |
| `POST /api/v1/qa/rescore` | 按当前规则重算（唯一能改历史分数的入口） |
| `GET /api/v1/qa/leaderboard?days=30` | 坐席排行榜 |
| `GET /api/v1/qa/export/low-score?threshold=60` | 低分样本导出（带原文与未通过项） |

四条关键设计（ADR 0011、0012）：

1. **规则引擎，不是模型评分**。考核结果要用来谈话、发奖金，必须可复现、可解释；同一句话换个说法分数就变，这种分数不能用。
2. **每个扣分都带证据**。第几句、原话片段、为什么引用它——报告里的每句话都能对回原文。
3. **红线一票否决，不参与加权**。做成「扣 30 分」会让它挤进 B 级，而这三类事的性质是「不该发生」。
4. **评分只发生在导入与显式重算**。所有读接口都不顺手改数据——打开一次报告就刷新分数，是最难查的「分数自己变了」。

### AI 电商客服（P01）

提问先判**该不该答**，再判**答不答得出来**。四条出口，顺序不能换：

```
提问 ──► 转人工？（投诉/要人工/情绪负面/要求升级）──► 建工单
      ──► FAQ 命中？（确定性知识，精确关键词）────► 直答 + 引用条目
      ──► 检索够格？（混合检索 + 拒答线 0.36）───► 答 + 文档/片段/相似度
      ──► 都不是 ───────────────────────────► 拒答，并说明最接近的相似度
```

| 接口 | 作用 |
|---|---|
| `GET /api/v1/cs/summary` | 会话/消息/工单数、四轨道分流分布、FAQ 条目数 |
| `GET /api/v1/cs/faq` | 内置 FAQ 清单（分类、问题、答案、关键词） |
| `POST /api/v1/cs/chat` | `{"message","conversation_id"?}` → 轨道、回复、引用、工单 |
| `POST /api/v1/cs/handoff` | 显式转人工（201，返回工单） |
| `GET /api/v1/cs/tickets?status=open` | 工单列表（`status` 只接受 open/claimed/closed，其他 400） |
| `POST /api/v1/cs/tickets/{id}/close` | 关单 |

四条关键设计（ADR 0013、0014）：

1. **拒答是正常结果，不是故障**。检索永远会返回 top-k，拿条件直接当答案就等于系统永远不承认自己不知道。阈值 0.36 是实测出来的：两篇真实运营资料，相关最低 0.3882、无关最高 0.3378，中间只有 0.05 的空隙——所以那张分数表与阈值写在同一个文件里，改阈值的人应该先重跑一遍表。
2. **FAQ 排在检索之前**（ADR 0014）。发货 48 小时、满 49 包邮这些是运营定死的承诺，不该被检索排序影响，也不该因为向量端点熔断 30 秒就答不出来。有测试锁住：把检索层换成抛异常，`包邮吗？` 仍然正常回答。
3. **每条回复都能指出依据**。FAQ 命中就说是哪一条，检索命中给文档与片段，转人工说清为什么。用户问「你凭什么这么说」时系统要答得出来。
4. **会话必须落库**。P01 写的会话，P09 直接拿去质检——同一份数据被两个模块消费。这是「回复完就丢」的对话做不了的。

### 合同审批（P05）

上传 `.txt/.md/.docx/.pdf` → 切条款 → 规则打分 → （可选）模型复核 → 人签字。产物是**风险清单**，不是判决。

```
上传 ──► 切条款（第X条 / 一、两种编号都认）──► 十条规则打分
     ──► 语义复核（模型逐条给 quote，quote 必须逐字出现在原文）
     ──► 人写意见：通过 / 驳回 / 要求修改，每次留快照
```

| 接口 | 作用 |
|---|---|
| `GET /api/v1/contracts/summary` | 合同数、已复核数、高危数、审批记录数、按风险项分布 |
| `GET /api/v1/contracts/scoring` | 规则、权重、阈值（前端展示口径，不另写一份） |
| `POST /api/v1/contracts/upload` | multipart 上传（201），同步返回条款与规则风险 |
| `GET /api/v1/contracts/{id}` | 条款、风险（带原句）、审批链 |
| `POST /api/v1/contracts/{id}/review` | 跑一遍语义复核（真实模型约 17 秒） |
| `POST /api/v1/contracts/{id}/approvals` | 写一条审批（201） |

三条关键设计（ADR 0015、0016）：

1. **每条风险都必须指回原句**。规则的 `excerpt` 从条款原文切出来，模型的 `quote` 要过逐字校验——`_normalize_for_match(quote) in haystack` 不成立就丢掉并计数（`dropped`）。让模型凭印象概括条款，是这类系统最常见的失真来源。
2. **规则分与语义分分开记**。`rule_score` 回答"踩了我们登记过的哪几条线"，`semantic_score` 回答"模型读出了哪几条原文里的单向约定"，两个分母不一样，加起来没有意义。实测一份干净合同规则 0 分、语义 92 分——只看总分会被误判成低风险。
3. **不做自动通过/驳回**。系统不知道自己不知道（独家经销权这类没登记的条款规则分是 0）。高风险合同点「通过」必须写理由，且审批记录只追加不改写，快照记的是当时看到的那几条风险。

### 直播切片（P08）

吃进带秒级时间码的直播转写，挑出值得单独发的片段。**产出是时间码 + 理由，不是视频文件。**

| 接口 | 作用 |
|---|---|
| `GET /api/v1/live/summary` | 场次、转写句数、候选数、通过/驳回数、均分 |
| `GET /api/v1/live/sessions` | 场次列表 |
| `POST /api/v1/live/sessions` | 收一场转写（201），同步算完候选（48 句不到 50 毫秒） |
| `GET /api/v1/live/sessions/{id}` | 场次详情 + 全部候选 |
| `POST /api/v1/live/sessions/{id}/recut` | 按当前规则重挑，保留已定稿的 |
| `POST /api/v1/live/clips/{id}/decide` | 定稿：`approved` / `rejected` |

三项关键设计（ADR 0017）：

1. **只挑不剪**。判断"哪段值得发"比"把这段切干净"稀缺得多——剪辑师一晚上能剪十条，但要听完整场两小时才知道哪十段值得剪。裁剪交给下游工具。
2. **四维加权，逐条给理由**。卖点密度 40（价格锚点 > 稀缺 > 权益 > 产品力）/ 开场钩子 20 / 行动号召 20 / 时长合规 20。实测最高分那条不是卖点词最多的，而是卖点 + 行动号召都有的——只有卖点没有落点，观众看完不知道干什么。
3. **人工结论优先于规则版本**。重挑只清 `suggested`，已通过/已驳回的留着。人的判断成本比机器高，不能因为调了个阈值就让人重看一遍。
4. **结尾的道别句要削掉**（规则 1.0.1）。往右扩到"够长"就停，不判断那句是不是收场，实测把「我们下次见」带进了候选结尾。`_tidy` 削尾巴，但不动含卖点词的句子、削短了往左补、补不出来就整个退回。

### 模型微调（P10）

**本平台不执行训练**，只做四件事：样本回流、入库前去重与分层、JSONL 导出、台账与对照评测。

| 接口 | 作用 |
|---|---|
| `GET /api/v1/finetune/summary` | 数据集/样本/任务数，按来源、质量、状态分布 |
| `GET /api/v1/finetune/sources` | 可选数据源 |
| `POST /api/v1/finetune/datasets` | 从 P09/P01 取样建集（201），返回含丢弃原因的 stats |
| `GET /api/v1/finetune/datasets/{id}` | 数据集 + 全部样本（带 `source_ref`） |
| `GET /api/v1/finetune/datasets/{id}/export` | 导出 JSONL（OpenAI chat 格式），待修样本排除 |
| `POST /api/v1/finetune/jobs` | 登记任务（201） |
| `GET /api/v1/finetune/jobs/{id}` | 单条作业台账 |
| `POST /api/v1/finetune/jobs/{id}/status` | 人手动推进状态 |
| `POST /api/v1/finetune/jobs/{id}/callback` | **训练机回写**（HMAC 签名，不走用户令牌） |
| `POST /api/v1/finetune/jobs/{id}/eval` | 记一批同题对照（base vs tuned） |

三条关键设计（ADR 0018）：

1. **每条样本可追溯**。`source` + `source_ref` 指回原始记录（`qa_session:16`），`fingerprint` 是消息内容的 SHA-256。说不清来源的训练数据，没法回答"模型为什么会这么说"。
2. **质量分层按"该不该学"分**。`positive`（质检 A/B、客服轨道 faq/retrieval）、`boundary`（refuse/handoff，教"什么时候该停"，最稀缺也最值钱）、`needs_fix`（质检 C/D 或有红线，**不进导出**——拿去训等于教模型学错误的回答）。
3. **回调和执行器是两个缝，不是两套系统**。训练机 POST 回调（HMAC 签名，签名覆盖 `{job_id}.{原样请求体}`，密钥没配就 503）；`scripts/run_finetune_job.py` 负责"导出 JSONL → 跑 `FINETUNE_TRAIN_COMMAND` → 回调写结果"。读走库、写走回调这个不对称是有意的：执行器要是也直接改库，回调协议就永远没有真实流量压过。终态是终态，已完成被回写成 running 直接 409。
4. **快照必须写清为什么少了几条**。`stats.skipped` 记丢弃原因，`stats.duplicates_by_source` 记去重丢的是哪一侧。实测从真实数据建集：13 条样本，`by_source` 只有 qa，而 `duplicates_by_source` 是 `{cs: 11, qa: 2}`——客服侧不是没数据，是 11 条会话与质检会话逐字相同被合并了。只记 `by_source` 会让人以为客服侧没接上。

## 项目约定与坑（改代码前先看）

1. **列表接口默认分页 `limit=20`**：前端读商品/店铺必须走 `web/src/lib/api.ts` 的 `fetchAllPages`，否则第 21 条之后的数据会静默丢失。
2. **`.venv` 的 `python.exe` 是 uv trampoline**：会派生子进程（启动器 → 真实进程）。停止时要按命令行匹配全部命中项并 `taskkill /T /F` 结束进程树；树杀后对已退出子进程再 taskkill 会报“找不到进程”，需临时把 `$ErrorActionPreference` 设为 `Continue`。
3. **`.ps1` 含中文必须 UTF-8 BOM**：否则 Windows PowerShell 5.1 按 ANSI 解码会乱码。
4. **本机 `HTTP_PROXY` 会毒化 localhost 调用**：起服务要设 `NO_PROXY=127.0.0.1,localhost,::1`（大小写两份）。
5. **Windows 上 Celery 不能用 `-B`**：本地用 `--pool=solo`；beat 只在 Docker worker 中启用。缺 beat 时新建任务仍可跑通（API 内联投递 outbox），但周期性租约回收不会执行。
6. **用户「删除」是物理删除但受审计外键保护**：`agent_tasks` / `agent_runs` / `agent_run_reviews` / `agent_approvals` / `agent_conversations` / `agent_memories` 任一有记录时返回 **409**，请改用「停用」。
7. **数据中台全量重算，不做增量**：每个 `CREATE OR REPLACE` 整层覆盖，跑完的仓一定等于源库当前状态。代价是跑批时长随数据量线性涨，十万行内仍是秒级；超过再换队列。
8. **窗口右端取仓内 `max(date)`，不用 `CURRENT_DATE`**：补录上周数据时看板不会显示成「近 7 天零消耗」。
9. **比率一律先聚合再相除**：`SUM(分子) / NULLIF(SUM(分母), 0)`，不要 `AVG(日比率)`。
10. **分析侧不能回写业务库**：`dataplatform` 只读 MySQL，写只发生在 DuckDB 仓内。
11. **问数的重试只有一轮**：第一次失败通常是"模型不认识表"，第二次还不认识就该改 prompt 或目录，加重试次数只是多花钱。
12. **`_COLUMN_NOTES` 是手工维护的短表**：新增"名字会骗人"的列（反向语义、0 值有特殊含义、行级与汇总级含义不同）要登记，否则模型会静默用错口径且不报错。
13. **向量不是前置条件**：`EMBEDDING_MODEL` 为空时 `/rag` 照常可用（纯 BM25）。不要为了"跑通"去硬编一个默认模型名——猜错的表现是"检索总是差一点"，比关掉更难查。
14. **切片超上限要换实现，不要调大常量**：`MAX_SCAN_CHUNKS = 5000` 是按当前资料量定的。超过时该换向量库，而不是把上限抬到 5 万硬撑——全表扫的延迟会先变成用户投诉，再变成事故。
15. **`rag_chunks.embedding` 是 `LONGTEXT` 里的 JSON**：不要改成 `JSON` 类型，MySQL 5.7 的 JSON 列在 `ORDER BY`/索引行为上与本项目其他表不一致；也不要假设它能被 SQL 直接做向量运算。
16. **考核分数只由 `rescore` 改**：规则调整后旧报告仍是旧分数（`rule_version` 会标出来），不要在读接口里顺手重算。
17. **`qa_sessions` 的导入键是 `(workspace_id, external_id)`**：上游换 id 规则会让同一批会话被当成新的，导入前先确认。
18. **考核判据落在语义类别上，不落在字面重合上**：`INTENTS` / `ACTION_PHRASES` 是手工短表，新增话术要登记，否则会误判成 0 分（实测过一次，见 `_evidence` E15）。
19. **同一行的时间戳必须同源**：`created_at` 是 `server_default=func.now()`，走数据库时钟（本机 MySQL 是 +8）。补「事件发生时间」字段时也要用 `func.now()`，不要掺 Python 的 `datetime.now(UTC)`——两个字段一减就差 8 小时（`cs_tickets` 实测过 -28800 秒）。
20. **红线关键词要防被正常词吞掉**：中文没有词边界，`"滚" in text` 会命中「主图回滚」。单字关键词要么换成完整短语，要么登记进 `REDLINE_BENIGN_COMPOUNDS`；红线一票否决，误判的代价不对称（见 `_evidence` E18）。
21. **模型给的引文必须逐字校验**：合同语义复核的 `quote`、RAG 的引用片段都走同一条规矩——在原文里 `in` 不成立就丢弃并计数，不要让模型概括条款（见 `_evidence` E19）。
22. **两段分不要加成一个分**：`rule_score` 与 `semantic_score` 的分母不同（一个是我们登记过的规则，一个是模型读出的单向约定）。要合并只合并展示层级，不合并数值（见 `_evidence` E19）。
23. **切片边界的错误要靠人来修**：规则从锚点扩到 12 秒就停，不判断那句是不是收场语（1.0.0 实测把「我们下次见」带进了结尾）。1.0.1 的 `_tidy` 只治"结尾恰好是道别"这一种，开头拖沓、中段跑题仍然治不了——`decide` 是必需步骤，不是可选项。
24. **数据集的快照要写清丢弃原因**：`stats.skipped` / `stats.duplicates_by_source` 不是可选字段。只有总数时，"这次为什么只有 13 条"永远查不出来（见 `_evidence` E21）。
25. **Windows 上切命令不要用 `shlex.split` 的默认模式**：POSIX 模式把反斜杠当转义，`D:\learning\...` 会被啃成 `D:learning...`，训练器一启动就报"数据集不存在"（实测退出码 2）。用 `posix=False` 再剥引号（`scripts/run_finetune_job.py` 的 `split_command`）。
26. **训练命令模板用 `str.replace` 不用 `.format`**：命令里常带 `{}`（JSON 参数、日志格式），format 会把它们当占位符直接炸掉。
27. **回调接口没配密钥就 503，不要"默认放行"**：它能改台账，等于凭据。同理签名里要带 `job_id`，否则同一份 `{"status":"succeeded"}` 能贴到任何作业上。
28. **导航只有一份**：`web/src/lib/nav.ts` 是唯一来源，桌面侧边栏与移动抽屉都读它。此前两边各写一份，移动端少了 8 条（合同审批、直播切片、模型微调都点不到）。

## 排障

- **页面主区域空白**，HTML 里出现 `Switched to client rendering because the server rendering errored`：检查 `NO_PROXY` 是否包含 `127.0.0.1`。
- **`401 Missing auth token`**：登录已过期，重新登录即可；`/api/v1/alerts/summary`、`/api/v1/model-settings/usage` 等只读接口同样需要登录。
- **Agent 任务一直 `queued`**：确认 worker 在跑，`celery inspect ping` 应返回 `pong`（可用 `start-dev.ps1` 或 `start_worker.ps1` 拉起）。
- **`docker compose` 报缺少变量**：根目录 `.env` 未创建或口令仍是占位符。
- **`next build` 在受限沙箱中断**：属于环境对 `.next` 清理的拦截，不是代码错误；类型校验请用 `npx tsc --noEmit`。
- **页面只出骨架、卡片是空壳**：查 `.next` 里 RSC payload 是否把 `Card` 渲染成无 children 的 `div`。`ui/card.tsx` 的 `Card` / `CardHeader` 漏写 `{...props}` 时会静默吞掉 children（不报错、不报错边界），已修复并需保持。
- **`/data-platform` 接口 409**：仓里还没跑过批，先 `POST /api/v1/data-platform/runs` 或用页面上的「重新抽取并重建」。

## 许可

采用 [MIT 许可证](LICENSE)。可自由使用、修改、再分发（含商用），需保留原版权声明与许可声明；作者不提供任何担保。

依赖组件各自遵循其上游许可证（FastAPI、Celery、Next.js、SQLAlchemy 等），本仓库许可证不覆盖第三方代码。
