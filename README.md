# Triply · 多 Agent 旅行规划助手

一个基于 **Python + LangGraph** 的多 Agent 旅行规划系统。用户输入出行需求，系统通过
「主 Agent 编排 → 子 Agent（机票 / 自驾 / 目的地路由）→ 工具层（RAG 知识库、天气、搜索、MCP 服务）→ FastAPI 流式返回」
完成个性化行程规划。支持流式 SSE 输出、多轮对话记忆、Token 配额与开放注册。

> 当前版本：**v1.6**（2026-10-08）

---

## 一、功能特性

- **多 Agent 协作**：LangGraph 状态图 + handoff 编排，主 Agent 按步骤调度子 Agent。
- **多轮记忆**：PostgreSQL + pgvector 作为 Checkpointer / Store，按会话持久化上下文。
- **RAG 知识库**：BM25 + Dense 向量 + RRF 倒数排名融合，叠加 LLM 重排与长上下文重排；内置 48 份热门目的地知识文档。
- **MCP 工具接入**：**5 个 MCP 服务 / 29 个工具** —— 高德地图（POI / 路线）、自建天气服务、Tavily 搜索、AIGOHOTEL 酒店、VariFlight 航班（详见第十节）。
- **流式对话**：SSE（Server-Sent Events）逐字返回，附带工具调用事件。
- **用户系统 + 配额**：注册 / 登录 / JWT，按账号 Token 配额限流，超额提示申请提额。
- **管理员额度看板**：管理员在「消息通知」中可查看每位游客的 token 用量、对话轮数与申请次数（仅显示邮箱，按用量降序），并一键通过提额。
- **前后端一体**：FastAPI 直接托管前端，访问 `/app/` 即可使用。

## 二、技术栈

| 层 | 选型 |
|---|---|
| 语言 / 框架 | Python 3.13, FastAPI, LangGraph, LangChain |
| 大模型 | 阿里云百炼 Qwen（DashScope 兼容 OpenAI 接口）；按环节分 `main` / `light` 两档，见 §十六 |
| 存储 | PostgreSQL（16+，生产为 17）+ pgvector，Chroma（本地向量库） |
| 可观测 | Langfuse（链路追踪 Tracing）；本地评测 CLI（`scripts/run_eval.py`，规则型 Golden Case 回归） |
| 部署 | Docker Compose，阿里云轻量应用服务器 |

## 三、目录结构

```
travel-planner/
├── app/                          # 后端主包
│   ├── main.py                   # FastAPI 入口（托管前端 /app/、注册路由）
│   ├── run.py                    # 本地启动入口（uvicorn）
│   ├── config.py                 # 全局配置（读取 .env，含配额/模型/密钥）
│   ├── core/                     # 核心：状态定义、检查点、记忆存储、链路追踪
│   │   ├── state.py              # LangGraph 全局状态 AgentState
│   │   ├── checkpointer.py       # PostgreSQL Checkpointer（多轮记忆）
│   │   ├── store.py              # PostgreSQL Store（长期记忆）
│   │   ├── memory_models.py      # 记忆数据模型
│   │   ├── middleware.py         # 请求中间件
│   │   └── tracing.py            # Langfuse 追踪封装
│   ├── agents/                   # Agent 编排
│   │   ├── handoffs/             # 主 Agent + 步骤配置
│   │   │   ├── step_config.py    # 流程话术 / 步骤定义
│   │   │   └── travel_agent.py   # 主 Agent 构建（create_travel_agent）
│   │   ├── routers/              # 路由 Agent
│   │   │   └── destination_router.py
│   │   └── subagents/            # 子 Agent
│   │       ├── driving_agent.py  # 自驾
│   │       ├── flight_agent.py   # 机票
│   │       └── transport_coordinator.py
│   ├── api/                      # API 层
│   │   ├── dependencies.py       # 依赖注入（鉴权 / DB 会话）
│   │   └── v1/                   # 路由：chat / conversations / users
│   ├── models/                   # SQLAlchemy 模型
│   │   ├── base.py, user.py, conversation.py, message.py, usage.py
│   ├── tools/                    # 工具层
│   │   ├── state_transition.py   # 状态机跳转 / 回退
│   │   ├── mcp_tools.py          # MCP 工具封装
│   │   ├── memory_tools.py       # 记忆读写工具
│   │   ├── rag_tools.py          # RAG 检索工具（运行时自建向量库）
│   │   ├── date_tools.py         # 本地日期工具 get_today_date（v1.5 起不再依赖航班服务）
│   │   ├── router_query.py       # 路由查询
│   │   └── transport_query.py    # 交通查询
│   ├── rag/                      # RAG 管线
│   │   ├── pipeline.py           # 检索流程串联（混合检索→重排→长上下文重排）
│   │   ├── vectorstore.py        # Chroma 向量库管理
│   │   ├── retriever.py          # BM25+Dense+RRF 混合检索器
│   │   ├── document_loader.py    # 文档加载（data/documents/）
│   │   ├── text_splitter.py      # 父子文档切分
│   │   ├── query_optimizer.py    # 查询改写
│   │   ├── reranker.py           # LLM 重排 + LongContextReorder
│   ├── mcp_core/                 # MCP 客户端与本地 Server
│   │   ├── client.py
│   │   └── servers/              # search_server / weather_server
│   ├── schemas/                  # Pydantic 请求/响应模型
│   └── utils/                    # logger / quota / security
├── data/
│   ├── documents/destinations/   # RAG 知识库源文档（.md，保留，勿忽略）
│   └── vectorstore/              # Chroma 索引（运行时生成，已 gitignore）
├── frontend/                     # 前端（index.html + 背景图）
├── scripts/                      # 运维脚本
│   ├── init_db.py                # 初始化数据库表（create_all）
│   ├── ingest.py                 # 知识库一键重建（重建 Chroma 向量索引）
│   ├── test_llm.py               # LLM 连通性自测 + Langfuse 追踪冒烟
│   ├── run_eval.py               # 评测 CLI（本地跑一遍 Golden Case，判断「改动有没有把别处改坏」）
│   ├── eval_lib/                 # 评测库：dataset / collector / metrics / report（纯规则，不调模型）
│   └── eval_data/core_v1.json    # Golden Case 数据集（12 条，覆盖 8 个规划步骤 + 边界）
├── docs/                         # 文档（架构核查报告 / 修订记录 / 配额申请说明）
├── docker-compose.yml            # 本地/服务器部署编排
├── Dockerfile                    # 后端镜像
├── pyproject.toml                # 项目依赖（uv）
├── requirements.txt              # pip 一键依赖（与 pyproject 同步）
├── tests/                        # 测试（RAG / MCP / Agent 流程 / API）
├── deploy.ps1 / deploy.bat       # 本地一键部署脚本（Windows）
├── deploy_db.sh                  # 服务器一键起 PG(pgvector) 容器
├── .env.example                  # 环境变量模板（纯占位符，可安全入库）
└── .env                          # 环境变量（真实密钥，**已 gitignore，切勿提交**）
```


## 四、环境要求

- **Python 3.13**
- **Docker + Docker Compose**（本地或服务器部署）
- **PostgreSQL（16+，生产为 17）+ pgvector**（本地可用 `deploy_db.sh` 或 Docker 起，也可用远程）

> **关于 Redis**：本项目**不使用 Redis**。依赖里虽声明了 `redis==5.0.0`，但代码中没有任何 Redis 客户端调用
> （无 `import redis`、无 `Redis(...)`、`settings.redis_url` 也无人使用）。持久化与复用全部由 PostgreSQL 承担：
> `AsyncPostgresSaver`（多轮记忆）+ `AsyncPostgresStore`（长期记忆）+ `psycopg_pool`（连接池），
> 进程内复用仅用 `functools.lru_cache`（配置单例、Langfuse 客户端与 handler）。
> 因此无需准备 Redis 服务，`.env` 里也没有 `REDIS_*` 变量。

## 五、本地快速开始

### 方式 A：uv（与项目一致，推荐）

```bash
uv sync                         # 按 pyproject.toml 创建虚拟环境并装依赖
cp .env.example .env            # 复制模板后填入自己的密钥（字段说明见第六节）
uv run python scripts/init_db.py        # 建表
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 方式 B：venv + pip（用 requirements.txt）

```bash
python -m venv .venv
.\.venv\Scripts\activate        # Windows；Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_db.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> **RAG 向量库无需手动初始化**：首次调用 RAG 工具时，`app/tools/rag_tools.py` 会自动读取
> `data/documents/destinations/*.md` 构建 Chroma 索引并落盘到 `data/vectorstore/`。

启动后访问：

- 接口文档（Swagger）：<http://localhost:8000/docs>
- 前端页面：<http://localhost:8000/app/>

## 六、配置 `.env`

复制示例并填入自己的密钥（`.env` 已被 `.gitignore` 忽略，**切勿提交**）：

```ini
# ===== LLM（阿里云百炼 / DashScope）=====
DASHSCOPE_API_KEY=sk-xxx
QWEN_MODEL_NAME=qwen3.8-max              # main 档（权衡题）模型。代码默认值为 qwen3.7-flash（config.py），生产 .env 显式覆盖
                                         # ⚠️ 百炼免费额度【按模型独立】，某一模型耗尽只会让该路径 403（AllocationQuota.FreeTierOnly），
                                         #    不影响其它模型——曾因此出现「RAG 改写链挂了但主对话正常」的诡异现象。
                                         #    2026-10-09 实测：qwen3.8-flash 与 qwen3.8-omni-flash 免费额度均已耗尽，改用 qwen3.8-max。
                                         #    统一从 .env 读取、禁止在代码里写死模型名（见 app/core/llm.py）。
                                         #    两档均强制 enable_thinking=False + enable_context_cache=True 以省 token。
QWEN_LIGHT_MODEL_NAME=qwen3.7-flash      # light 档（选择题）模型：查询改写 / 重排 / 意图分类 / 结构化抽取 / 简单问答。
                                         #    输入单价 0.2 元/百万，比 qwen3.8-max（12 元）便宜 60 倍；这些环节答案有明确对错，
                                         #    旗舰档带来的边际收益≈0。安全阀：把它设成与 QWEN_MODEL_NAME 相同 = 全量回退旗舰档。
                                         #    分档清单与成本口径见 §十六。
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MAX_TOKENS=2500                   # 单轮最大输出 token（防长回复，配合 GLOBAL_OUTPUT_RULES 的 8 行硬约束）

# ===== 可观测 Langfuse（可选；留空/占位则自动降级为不追踪，不影响对话）=====
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_PROJECT=zhixing-travel-planer-dev   # 仅标签字段：tracing.py 不读取它，实际项目由 public_key 决定

# ===== 数据库（PostgreSQL + pgvector）=====
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=travel_planner_db
POSTGRES_USER=travel_user
POSTGRES_PASSWORD=travel123456
# DATABASE_URL 无需配置：连接串由上面 POSTGRES_* 自动拼装（见 config.py 的 database_url 属性）

# ===== MCP / 第三方 API =====
AMAP_API_KEY=xxx                      # 高德（地图 POI/路线；天气由自建 weather_server 走高德天气 API）
TAVILY_API_KEY=tvly-xxx               # Tavily 搜索
VARIFLIGHT_API_KEY=sk-xxxx            # VariFlight 航班（由 app/mcp_core/client.py 直接 os.getenv 读取，不在 config.Settings 中）
AIGOHOTEL_MCP_API=mcp_xxx             # AIGOHOTEL 酒店（同上，用作 HTTP header 的 Bearer token）

# ===== 应用 =====
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true

# ===== 访问控制 =====
ALLOW_OPEN_REGISTRATION=true          # 开放注册（配合配额防白嫖）
BOOTSTRAP_ADMIN_USERNAME=admin        # 首次启动自动创建的管理员账号（部署实例用手机号作为用户名；此处仅为示例）
BOOTSTRAP_ADMIN_PASSWORD=travel2026

# ===== Token 配额（应用层限额）=====
DEFAULT_USER_TOKEN_QUOTA=3000         # 每账号默认 token 上限。注意：代码默认值为 200000（config.py:29），生产 .env 显式覆盖为 3000；实测单轮约 0.4~1.2 万 token（关闭思考 + max_tokens=2500 后约 0.4 万），故 3000 约够 0~1 轮，靠管理员提额兜底（默认在其已用量上追加 2 万）
QUOTA_REQUEST_CONTACT=                # 超额时页面展示的联系方式（邮箱/微信），会同时出现在侧边栏和弹窗里
```

> 说明：本文件里的「密钥」均为**第三方 API Token / 数据库密码 / 应用密钥**（如阿里云百炼
> LLM Token、高德、Tavily、Langfuse、Postgres 密码），**不是云服务器的登录密钥**。
> 云服务器（阿里云轻量）通过独立的 SSH 私钥登录，那把密钥不在这个仓库里。

> ⚠️ **Docker 构建会把 `.env` 烤进镜像**：`Dockerfile` 末段有 `COPY .env /app/.env`（因为
> `config.py` 直接读 `/app/.env`）。**切勿把构建出的 `travel-planner-backend` 镜像推送到公共仓库或分发给他人**，
> 镜像层里含有你填入的真实密钥。若需对外分发，请改用运行时挂载 / secrets 注入的方式。

## 七、初始化数据库

```bash
python scripts/init_db.py
```

`init_db.py` 用 SQLAlchemy `create_all` 建表，包含 `users`、`conversations`、`messages`、
`token_usage`、`quota_request` 等。新表会自动创建；若需给已有表加字段，需手写迁移。

## 八、Docker 本地部署

> **前置条件**：`docker-compose.yml` **只编排 backend 一个服务**（`network_mode: host`），
> **不包含 PostgreSQL / Redis**。请先确保 `localhost:5432` 上的 PostgreSQL(pgvector) 已就绪
> （可用 `bash deploy_db.sh`，或改用远程库并同步改 `.env` 里的 `POSTGRES_*`），再执行建表与启动。

```bash
docker compose up -d --build
```

- 后端：<http://localhost:8000>
- 前端：<http://localhost:8000/app/>
- 接口文档：<http://localhost:8000/docs>

## 九、阿里云轻量服务器部署

适用：阿里云轻量应用服务器（2C2G，Alibaba Cloud Linux 3），需先在防火墙放通 **8000** 端口。

**1. 服务器安装 Docker**

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
# 可选：配置镜像加速器（国内拉取更快），如 glec0rcy.mirror.aliyuncs.com
```

**2. 上传代码**（二选一）

```bash
# 方式一：git
git clone <your-repo> /root/travel-planner && cd /root/travel-planner

# 方式二：scp（本地 PowerShell）
scp -r E:\travel-planner root@<公网IP>:/root/travel-planner
```

**3. 配置 `.env`**

在服务器 `/root/travel-planner/.env` 填入真实密钥（DashScope / 高德 / Tavily / Langfuse 等）。

> ⚠️ **`docker-compose.yml` 只编排 backend 一个服务，不提供数据库**。PostgreSQL(pgvector) 需另行在
> 同机以**独立容器**运行（生产用 `pgvector/pgvector:pg17`，把 5432 映射出来），backend 通过
> `network_mode: host` 连 `localhost:5432`。若用远程库，同步改 `.env` 里的 `POSTGRES_*`
> （连接串由它们自动拼装，无需配 `DATABASE_URL`）。Redis 不需要——本项目未使用。

**4. 启动**

```bash
cd /root/travel-planner
docker compose up -d --build
```

**5. 访问**

- 前端：<http://<公网IP>:8000/app/>
- 接口文档：<http://<公网IP>:8000/docs>
- 健康检查：`GET /`

**6. 三种改动的生效方式（关键）**

| 改动类型 | 命令 | 说明 |
|---|---|---|
| 仅改前端（`frontend/`） | `docker compose up -d --force-recreate backend` | `docker-compose.yml` 已挂 `./frontend:/app/frontend`，**无需重建镜像** |
| 改 `.env` | `docker compose up -d --force-recreate backend` | `restart` 不会重读 `env_file`，必须 force-recreate |
| 改后端代码 | `docker compose up -d --force-recreate --build backend` | 需重建镜像（指定服务 + 强制重建，避免旧容器残留导致"改了没生效"） |

**7. 小内存（2C2G）优化**

- 建议加 swap（如 2GB），避免 Chroma / 模型加载 OOM。
- 使用国内镜像加速器加速镜像拉取。

## 十、MCP 服务接入

集中配置在 `.env` 与 `app/config.py`、`app/mcp_core/`：

运行时共加载 **5 个 MCP 服务、29 个工具**（生产启动日志：`✅ 共加载 29 个 MCP 工具（来自 5 个服务）`）：

| 服务 | 形态 | 工具数 | capability 标签 | 说明 |
|---|---|---|---|---|
| `weather`（自建） | stdio（FastMCP） | 1 | `weather` | 天气查询，内部走高德天气 API，带国内城市 adcode 速查表 + 海外城市友好兜底 |
| `search`（自建） | stdio（FastMCP） | 1 | `search` | Tavily 联网搜索 |
| `amap` | HTTP（`mcp.amap.com`） | 15 | `map_poi` | 高德官方全量工具（POI / 路线 / 天气），统一归为地图能力 |
| `VariFlight-Aviation` | streamable_http | 9 | `flight` | 航班查询（URL 不含尾斜杠，避免 307→HTTP 降级） |
| `aigohotel-mcp` | streamable_http | 3 | `hotel` | 酒店查询 |

工具不是"全量挂给每个步骤"，而是按 `capability` 标签聚合后，由 `app/agents/handoffs/step_config.py`
**按流程步骤注入**（例：需求收集阶段刻意不挂天气工具，专注收需求）。
此外，**日期能力已本地化**：`app/tools/date_tools.py::get_today_date`，不再依赖航班服务的 `getTodayDate`。

密钥配置位置：

- **高德**：`AMAP_API_KEY`（地图；天气服务也复用它）
- **Tavily**：`TAVILY_API_KEY`（联网搜索）
- **AIGOHOTEL**：`AIGOHOTEL_MCP_API`（酒店查询，streamable_http）
- **VariFlight**：`VARIFLIGHT_API_KEY`（航班，已验证可用，streamable_http）

> 后两个变量**不在 `config.Settings` 中**，由 `app/mcp_core/client.py` 直接 `os.getenv` 读取
> （一个拼进 URL、一个放进 HTTP header）。
## 十一、API 说明

- **Swagger 自描述契约**：`/docs`
- **流式对话**：`POST /api/v1/chat/stream/{conversation_id}`（SSE）
  - 每帧为一个 JSON 对象，字段 **`type`** 取值：`token`（正文增量）、`tool_call`（调用工具）、`usage`（本轮额度快照）、`error`、`done`
  - 超出配额返回 **402**，前端提示「申请更多额度」
- **路由前缀 `/api/v1`**：
  - 用户：`POST /register`、`POST /login`、`GET /me`、`GET /usage`、`POST /quota-request`
  - 管理员（用户名等于 `BOOTSTRAP_ADMIN_USERNAME`，否则 403）：
    `GET /users/quota-requests`（提额申请列表，**只回邮箱、不回用户名**）、
    `POST /users/quota-requests/{request_id}/approve?quota_tokens=N`（批准加量）、
    `POST /users/quota-requests/{request_id}/reject`（忽略）、
    `POST /users/quota/grant?username=xxx&quota_tokens=N`（按用户名直接设置绝对额度）
    `GET /users/admin/quota-dashboard`（管理员额度看板：每位游客用量 / 对话轮数 / 申请次数 / 最近申请状态，仅显示邮箱，按用量降序）
  - 会话：`POST ""` / `GET ""` / `GET /{id}` / `PATCH /{id}` / `DELETE /{id}`（conversations）
  - 对话：`POST /chat/stream/{conversation_id}`（SSE）、`GET /chat/history/{conversation_id}`

## 十二、Token 配额机制

- `DEFAULT_USER_TOKEN_QUOTA` 控制每账号默认额度；`token_usage.quota_tokens=0` 表示跟随全局默认。
- 额度在**每轮对话开始前**校验，超额对话被 402 暂停。
- **超额时的用户侧体验**：前端侧边栏额度条 + 独立弹窗都会展示 `QUOTA_REQUEST_CONTACT`（点击可复制），
  并提供「申请更多额度」自助提交入口；弹窗内另有「刷新额度」按钮（强制跳过浏览器缓存重新拉取 `/usage`），
  管理员审批通过后游客点此即可立即恢复对话，无需刷新页面。
- **管理员侧**：登录管理员账号后侧边栏出现「消息通知」入口（带未处理角标），
  点开可见所有提额申请——**为保护隐私只显示电子邮箱**，可一键「通过」（默认在已用量基础上追加 2 万）或「忽略」。
  前端通过 `GET /me` 返回的 `is_admin` 字段决定是否显示该入口。
- 单独提额（admin）：`POST /api/v1/users/quota/grant?username=xxx&quota_tokens=N`（需知道用户名）；
  走通知列表则用 `POST /api/v1/users/quota-requests/{request_id}/approve?quota_tokens=N`（只需 request_id，前端拿不到用户名）。
  > 语义区别：`approve` 为「追加式」——新额度 = 当前已用量 + N，确保游客永远拿到 N 个全新可用 token（即使已用量已超旧额度也生效）；
  > `grant` 为「绝对值式」——直接把额度设为 N（传 0 则跟随全局默认）。
- `ALLOW_OPEN_REGISTRATION=true` 放开注册，配合配额防止被白嫖。

## 十三、运维命令速查

```bash
# 本地开发
uv run uvicorn app.main:app --reload --port 8000

# 建表
python scripts/init_db.py

# Docker
docker compose up -d --force-recreate --build backend  # 改后端代码后
docker compose up -d --force-recreate backend # 改 .env 或前端后
docker compose logs -f backend                # 看日志
docker compose down                           # 停止

# 服务器改完前端/环境变量后的最小操作
ssh root@<公网IP> "cd /root/travel-planner && docker compose up -d --force-recreate backend"
```

## 十四、可观测性（Langfuse）

Langfuse 用于**链路追踪**（Tracing；评测 / 数据集能力未接入）。生产链路已接线，不是"只配了环境变量"：

| 环节 | 位置 | 说明 |
|---|---|---|
| handler 注入 | `app/api/v1/chat.py:166-169` | 生产 SSE 对话在 `agent.astream_events()` 的 `config` 里注入 `callbacks=[langfuse_handler]`（handler 为 None 时自动跳过） |
| 上报刷新 | `app/api/v1/chat.py:231` | 流结束后调用 `flush_langfuse()`，`try/except` 包裹，失败不影响对话 |
| 客户端封装 | `app/core/tracing.py` | 必须先显式构造 `Langfuse(...)` 完成"注册"，再建 `CallbackHandler`，否则 SDK 会退化成 fake 客户端静默丢 trace |
| 降级保护 | `config.py::langfuse_enabled` | 未配置密钥 / 密钥仍是占位值（`REPLACE_ME`、`请替换`、`your-`）/ `LANGFUSE_TRACING_ENABLED=false` 时，`get_langfuse_handler()` 返回 `None`，主流程完全不受影响 |

**如何验证**

```bash
# 1) 本地冒烟：确认密钥与项目匹配
python scripts/test_llm.py     # 打印「📊 Langfuse 追踪已启用」且控制台出现一条测试 trace

# 2) 线上：发一条真实对话后，到 Langfuse 控制台 Tracing 页刷新，应新增记录
```

**已知限制**：同一轮对话会在 Langfuse 里平铺成**多条并列记录**（1 条 `LangGraph` CHAIN + 若干条
`ChatOpenAI` / `model` GENERATION），因为 LangGraph 子图/节点级 LLM run 的父子链路未完整挂载。
时间戳相同即说明它们来自同一次请求，不是重复上报。若需按 `conversation_id` 归组成一条 trace，
需要给 handler 配置 `session_id`（尚未实施）。

## 十五、评测（本地回归）

定位：**改完 prompt / 步骤配置 / 模型后，回答"这次改动有没有把别的地方改坏"**。
刻意做成**本地 CLI 工具**，不建库表、不做管理后台、不进生产链路——取舍理由见 `docs/评测功能落地方案_v1.6.md`。

| 组成 | 位置 | 说明 |
|---|---|---|
| 数据集 | `scripts/eval_data/core_v1.json` | 12 条 Golden Case（`quick` 8 条 / `deep` 4 条），分档位可选用例，含 `expected` 断言 |
| 采集器 | `scripts/eval_lib/collector.py` | 直接复用 `app.api.v1.chat.is_answer_stream_event` 判据驱动 Agent，**采集口径与线上一致** |
| 指标 | `scripts/eval_lib/metrics.py` | 全部为**规则型确定性指标**，零模型调用、可复现（见下表） |
| 报告 | `scripts/eval_lib/report.py` | 落 `scripts/eval_out/{run_id}.json\|.md` + `latest.*`，未入库、已 gitignore |

**指标**：门禁类 `answer_non_empty` / `format_clean`（禁 Markdown + 单轮正文 ≤8 行）/ `forbidden_phrase_clean` / `tools_forbidden_clean`；
核心类 `step_path_score`（LCS 归一化）/ `tool_recall` / `tools_any_hit` / `final_step_match` / `requirement_fill_rate` / `subgraph_leak`；
观察类 `latency_ms` / `total_tokens` / `tool_error_rate` / `turns_used`。

**数据集：12 条具体是什么**（`scripts/eval_data/core_v1.json`，每条 = 一段脚本化的用户话术 + 一组断言）

| 档位 | 用例 id | 分类 | 断言的是什么 |
|---|---|---|---|
| quick | `req_collect_no_dump` | 需求收集 | 需求模糊时"先反问"，不直接甩攻略 |
| quick | `req_relative_date_uses_date_tool` | 需求收集 | 用户说"下个月/下周"必须先调日期工具拿真实日期 |
| quick | `req_full_info_advances` | 需求收集 | 信息齐全后能主动推进，不会卡在第一步 |
| quick | `req_weather_gated_in_step1` | 需求收集 | 首步不给天气工具（设计如此），要能承诺"下一步帮你查" |
| quick | `format_no_markdown` | 输出规范 | 正文不含 `**`/`##`/`*`，且 ≤8 行 |
| quick | `dest_weather_shown` | 目的地推荐 | 必须展示逐日实时天气 |
| quick | `dest_overseas_no_fabricate` | 边界 | 海外城市天气：既不编造温度，也不说"没接天气" |
| quick | `memory_pref_saved` | 记忆 | 用户说"海鲜过敏"要真的写进长期记忆 |
| deep | `transport_rail_12306` | 交通规划 | 高铁：记录推进，但绝不承诺"帮你查车次" |
| deep | `rollback_to_destination` | 回退 | 用户改主意时能回退到目的地步骤 |
| deep | `itinerary_daily_structure` | 行程生成 | 能走到行程步骤并生成按天结构 |
| deep | `budget_scope_disclosure` | 预算汇总 | 必须说清预算是否含往返大交通 |

**一条用例怎么跑**：把 `turns` 里的用户话术按顺序逐轮灌进 Agent（脚本化用户，保证可复现），
每轮用 `is_answer_stream_event` 抓正文，跑完后把「实际步骤路径 vs `expected.steps_path`」
「实际调用的工具 vs `tools_expected`」「正文是否含 `must_not_contain`」等逐条比对，算出指标。

**报告怎么读**（`scripts/eval_out/latest.md`）

- 顶部「指标总览」表里，**门禁类**指标通过率必须为 1，否则判定 ❌ 未通过；
  **核心类**看通过率趋势，**观察类**只看数值不判定。
- 「用例明细」里 `未通过指标` 列直接告诉你哪条挂了；「失败详情」会贴出**完整多轮 transcript**
  和该轮实际调用的工具名——定位问题时基本不用再回看日志。
- `--baseline latest` 会额外输出一张与上一次 run 的对比表（哪些指标变差了）。

```bash
python scripts/run_eval.py --validate        # 只校验数据集（秒级，改完数据集先跑这个）
python scripts/run_eval.py --list            # 列出用例
python scripts/run_eval.py                   # 跑 quick 档（8 条）
python scripts/run_eval.py --tier all --baseline latest   # 跑全部并和上一次对比（判断是否变差）
```

> 评测进程会把当前虚拟环境的 bin 目录前置到 `PATH`（`run_eval.py` 内），否则自建 stdio MCP 子进程
> 会用到系统 python、导致 weather / search 服务加载失败——**这是评测进程内的局部处理，不动生产代码**。
> 运行时 `temperature=0`（生产为 0.7），报告中已标注该差异；`--temperature prod` 可与生产逐字一致。

**评测已发现的生产缺陷（待修）**：目的地 Router 的 `explore` 子 Agent 也是 `create_agent` 图、节点名同为
`model`，其内部攻略流式输出会**穿透 `chat.py` 的正文白名单**混进用户可见正文（指标 `subgraph_leak` 可稳定复现）。
根因是 `app/agents/routers/destination_router.py` 中 `_explore_agent.ainvoke(...)` 未透传 `config`。

## 十六、模型分档与成本（降本）

一个模型跑全链路是最省事的写法，但**不省钱**。本项目实测 token 结构是 **输入占 96%**
（quick 档 8 条：平均输入 13,749 / 输出 560），成本几乎全部来自"每轮把上下文重放一遍"，
于是"哪些环节配得上旗舰价"就成了最大的成本阀门。

**实现**：`app/core/llm.py` 是全项目**唯一**定义"档位 → 模型名"的地方，杜绝某处硬编码模型名后、
该模型免费额度用尽导致局部 403 却极难排查（历史上真实发生过）。

| 档位 | 环境变量 | 代码默认值 | 承担什么活 |
|---|---|---|---|
| `main`（权衡题） | `QWEN_MODEL_NAME` | `qwen3.7-flash`（生产 `.env` 覆盖为 `qwen3.8-max`） | 跨约束推理 / 编排 / 计算 |
| `light`（选择题） | `QWEN_LIGHT_MODEL_NAME` | `qwen3.7-flash` | 改写、重排、分类、结构化抽取、格式化、简单问答 |

**主对话按步骤切档**：`step_config.py` 里每个步骤带一个 `model_tier`，
`StepConfigMiddleware` 在每轮 `request.override(model=...)` 时按当前步骤换模型。

| 步骤（`step_config.py`） | 档位 | 理由 |
|---|---|---|
| 需求收集 | main ⚠️ | 见下方说明：曾下沉 light 档并导致空回复，**已回退** |
| 目的地推荐 | main | 季节适宜性 + 预算压力 + 风险替代，典型多约束权衡 |
| 交通规划 | main | 时间 / 预算 / 是否中转，冲突最集中的一步 |
| 住宿规划 | main | 预算等级 → 星级 / 区域 / 房型的换算 |
| 餐饮规划 | light | 忌口确认 + 三种餐饮类型选择 |
| 行程生成 | main | 动线 / 节奏 / 强度 / Plan B，旗舰能力的核心兑现点 |
| 预算汇总 | main | 要算数、要讲清假设与波动项 |
| 订单生成 | light | 格式化输出订单号 + 写出行记录 |

> ⚠️ **需求收集为什么回退到 main**：它是最像"简单问答"的一步，最初按"选择题"下沉到了
> `qwen3.7-flash`，但评测 `req_weather_gated_in_step1` **稳定复现（2/2）**一次参数缺失的无效工具调用
> （`get_weather_forecast` 未传 `city_adcode` → `ToolException` → 该轮完全没有回复）；
> 把 light 档换回 `qwen3.8-max` 后同一条用例不再报错。需求收集是**所有会话必经过**的步骤，
> 一次空回复的代价远大于省下的 token，因此保留 main 档。想再试轻档只需改这一行并跑全量评测。
> 这也说明一件事：**"哪个环节该降级"不能靠直觉，必须跑评测**。

**链路上的固定分工**（非主对话）

| 位置 | 档位 | 理由 |
|---|---|---|
| `app/rag/query_optimizer.py` 查询改写 | light | 把用户问题扩成检索变体，是选择题 |
| `app/rag/reranker.py` 重排 | light | 给候选文档打相关性分，是选择题 |
| `destination_router.py` 意图分类器 | light | explore / weather 二选一，是选择题 |
| `destination_router.py` explore 子 Agent | light | 从知识库检索并抽取攻略片段 |
| `subagents/flight_agent.py` 航班 | light | 城市三字码 / 日期参数抽取 + 结果整理 |
| `subagents/driving_agent.py` 自驾 | light | 起终点参数抽取 + 结果整理 |
| `subagents/transport_coordinator.py` 交通协调器 | main | 航班 vs 自驾 vs 高铁的多方案对比与推荐 |

**官方单价**（元 / 百万 tokens，2026-10-09 核对百炼控制台与智能路由文档两处，口径一致）

| 模型 | 输入 | 输入·缓存命中 | 输出 |
|---|---|---|---|
| qwen3.8-max | 12 | 1.5 | 36 |
| qwen3.8-flash | 0.8（限时 5 折，原价 1.6） | 0.1 | 2.7（原价 5.4） |
| qwen3.7-flash | 0.2 | 0.04 | 0.8 |

> 思考 token 按输出价计费，所以两档都强制 `enable_thinking=False`。

**安全阀**：把 `QWEN_LIGHT_MODEL_NAME` 设成与 `QWEN_MODEL_NAME` 同一个模型（例如都写 `qwen3.8-max`），
等价于"全量回退到旗舰档"，**不需要改任何代码**。

### 「未开缓存 / 缓存命中 60%」是什么 —— 和 Redis 无关

成本估算里那两个柱子指的是**百炼（模型服务商）侧的上下文缓存 Context Cache**，
不是本项目的应用层缓存，也**不需要 Redis**（本项目确认未使用 Redis，见 §二）。

- **未开缓存**：每轮请求的输入 token 全部按原价计费（如 max 的 12 元 / 百万）。
- **缓存命中 60%**：假设每轮输入里有 60% 是**前缀未变**的内容（系统提示 + 全局输出规范
  + 工具 schema + 历史前段），被服务端缓存命中后按"缓存命中价"（1.5 元 / 百万，约原价 12.5%）
  计费；剩下 40% 是本轮新增的用户输入与工具返回，仍按原价。

我们通过在 `app/core/llm.py` 里传 `extra_body={"enable_context_cache": True}` 开启它。

⚠️ 两点要注意：

1. **60% 是假设值，不是实测值**。真实命中率可以从 Langfuse trace 的 usage 明细里读
   （`cached_tokens` / `cache_read_input_tokens` vs `input_tokens`）。
2. 这个开关只表示"允许命中"，**命中率取决于前缀是否稳定**。目前中间件每轮
   `request.override(tools=...)` 会按步骤换工具集，工具 schema 一旦变化就会打断前缀、
   拉低命中率——这是下一步值得查的优化点。

## 十七、许可证

自己学习，许可证另行约定。
