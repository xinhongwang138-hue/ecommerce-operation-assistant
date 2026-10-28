# 电商运营助手（Ecommerce Operation Assistant）

多平台店铺运营工作台：店铺 / 商品 / SKU / 库存、竞品监控、AI 创意与素材、投放实验、Agent 任务与审批、模型用量与费用估算。

## 技术栈

- 前端：Next.js 16（App Router，服务端组件）+ Tailwind CSS
- 后端：FastAPI + SQLAlchemy + Alembic
- 异步：Celery（Redis broker）+ LangGraph Agent（Postgres checkpointer）
- 存储：MySQL 8（业务）、Redis 7（队列/缓存）、Postgres 16（Agent checkpoints）

## 目录结构

```
api/                 FastAPI 后端（app/、alembic/、scripts/、tests/）
web/                 Next.js 前端（src/app、src/components、src/lib）
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

## 项目约定与坑（改代码前先看）

1. **列表接口默认分页 `limit=20`**：前端读商品/店铺必须走 `web/src/lib/api.ts` 的 `fetchAllPages`，否则第 21 条之后的数据会静默丢失。
2. **`.venv` 的 `python.exe` 是 uv trampoline**：会派生子进程（启动器 → 真实进程）。停止时要按命令行匹配全部命中项并 `taskkill /T /F` 结束进程树；树杀后对已退出子进程再 taskkill 会报“找不到进程”，需临时把 `$ErrorActionPreference` 设为 `Continue`。
3. **`.ps1` 含中文必须 UTF-8 BOM**：否则 Windows PowerShell 5.1 按 ANSI 解码会乱码。
4. **本机 `HTTP_PROXY` 会毒化 localhost 调用**：起服务要设 `NO_PROXY=127.0.0.1,localhost,::1`（大小写两份）。
5. **Windows 上 Celery 不能用 `-B`**：本地用 `--pool=solo`；beat 只在 Docker worker 中启用。缺 beat 时新建任务仍可跑通（API 内联投递 outbox），但周期性租约回收不会执行。
6. **用户「删除」是物理删除但受审计外键保护**：`agent_tasks` / `agent_runs` / `agent_run_reviews` / `agent_approvals` / `agent_conversations` / `agent_memories` 任一有记录时返回 **409**，请改用「停用」。

## 排障

- **页面主区域空白**，HTML 里出现 `Switched to client rendering because the server rendering errored`：检查 `NO_PROXY` 是否包含 `127.0.0.1`。
- **`401 Missing auth token`**：登录已过期，重新登录即可；`/api/v1/alerts/summary`、`/api/v1/model-settings/usage` 等只读接口同样需要登录。
- **Agent 任务一直 `queued`**：确认 worker 在跑，`celery inspect ping` 应返回 `pong`（可用 `start-dev.ps1` 或 `start_worker.ps1` 拉起）。
- **`docker compose` 报缺少变量**：根目录 `.env` 未创建或口令仍是占位符。
- **`next build` 在受限沙箱中断**：属于环境对 `.next` 清理的拦截，不是代码错误；类型校验请用 `npx tsc --noEmit`。
