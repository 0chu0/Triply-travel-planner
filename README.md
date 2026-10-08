# Triply · 多 Agent 旅行规划助手

一个基于 **Python + LangGraph** 的多 Agent 旅行规划系统。用户输入出行需求，系统通过
「主 Agent 编排 → 子 Agent（机票 / 自驾 / 目的地路由）→ 工具层（RAG 知识库、天气、搜索、MCP 服务）→ FastAPI 流式返回」
完成个性化行程规划。支持流式 SSE 输出、多轮对话记忆、Token 配额与开放注册。

> 当前版本：**v1.5**（2026-10-02）；修订记录见 `docs/Triply_修订记录_v1.5.md`。

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
| 大模型 | 阿里云百炼 Qwen（DashScope 兼容 OpenAI 接口） |
| 存储 | PostgreSQL（16+，生产为 17）+ pgvector，Chroma（本地向量库） |
| 可观测 | Langfuse（链路追踪 Tracing；评测未接入） |
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
│   └── test_llm.py               # LLM 连通性自测 + Langfuse 追踪冒烟
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
QWEN_MODEL_NAME=qwen3.8-omni-flash     # 主对话模型（推理模型，思考链也计费；已在主对话代码中关闭思考模式 + 开启上下文缓存以省 token）。注意：代码默认值为 qwen3.7-flash（config.py:35），如需 3.8 必须在 .env 显式指定
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

## 十五、许可证

自己学习，许可证另行约定。
