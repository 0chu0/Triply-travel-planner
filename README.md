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
QWEN_MODEL_NAME=qwen3.8-flash            # main 档（权衡题）模型，代码默认值即 qwen3.8-flash（config.py）
                                         # ⚠️ 百炼免费额度【按模型独立】，某一模型耗尽只会让该路径 403（AllocationQuota.FreeTierOnly），
                                         #    不影响其它模型——曾因此出现「RAG 改写链挂了但主对话正常」的诡异现象。
                                         #    2026-10-10 起两档统一为 qwen3.8-flash（此前因额度问题分散在 max / plus）。
                                         #    统一从 .env 读取、禁止在代码里写死模型名（见 app/core/llm.py）。
                                         #    两档均强制 enable_thinking=False + enable_context_cache=True 以省 token。
QWEN_LIGHT_MODEL_NAME=qwen3.8-flash      # light 档（选择题）模型：查询改写 / 重排 / 意图分类 / 结构化抽取。
                                         #    代码默认值同为 qwen3.8-flash（2026-10-10 与 main 档统一）。
                                         #    安全阀：把它设成与 QWEN_MODEL_NAME 相同 = 全量回退到单一档。见 §十六。
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
>
> ⚠️ **时区**：`docker-compose.yml` 给 backend 设了 `TZ=Asia/Shanghai`。容器默认是 UTC，
> 没这一行时 `datetime.now()` 比北京时间晚 8 小时 —— `get_today_date` 在北京时间
> 00:00–08:00 会返回"昨天"，日志时间戳也对不上（2026-10-10 修复）。

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

#### 当前时间注入与日期铁律（2026-10-10 修复）

`StepConfigMiddleware` 每轮都会把 `build_today_context()` 拼进 system prompt（**对所有步骤生效**）：
「今天是 2026 年 10 月 10 日，星期六（2026-10-10）」+ 四条【日期使用铁律】；
`GLOBAL_OUTPUT_RULES` 第 9 条再兜一道「日期硬约束」。时区优先 `ZoneInfo("Asia/Shanghai")`，
取不到（基础镜像无 tzdata）时回退 `datetime.now()`，后者由 compose 的 `TZ=Asia/Shanghai` 兜底。

为什么必须有这一段（真实事故）：用户说「10月15日出发」，模型回「我先按 10月20日 / 10月27日给你备选」——
把用户给的明确日期换成了 prompt 示例里的日期。根因之一是**模型不知道今天是几号**（system prompt
没有时间基准，而步骤 prompt 只在"用户说相对时间"时才要求调日期工具），于是对一个没有年份、
没有参照的日期无从判断，就近抄了示例锚点。配套的两处收口：

- `step_config.py` 需求收集步骤的确认清单示例，日期已改成占位符 **`<出发日期>`**（原为具体日期 `10月20日`）；
- 【日期处理要求】新增：用户给了明确日期 → **原样采用，禁止替换、禁止另给备选**；
  只有"范围"（如下个月中旬）才允许给 2 个区间，且必须由【当前时间】或日期工具推导，不得照抄示例。

实测（qwen3.8-flash，修复后）：「10月15日出发」→ 原样采用不改写；「下周末出发」→ 正确换算为 10月17日（周六）。

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

**怎么读一条 trace**（控制台 Tracing 列表 → 点进任意一条）

trace 是一棵**调用树**，每一层回答不同的问题：

| 层级 | 看什么 | 能回答什么 |
|---|---|---|
| `LangGraph`（根 span） | `thread_id`、总耗时 | 一次用户提问 = 一条 trace；`thread_id` 即会话标识 |
| `model` / `tools`（CHAIN） | 分支结构 | 这一轮是"在想"还是在"调工具" |
| `ChatOpenAI`（GENERATION） | `ls_model_name`、耗时 / 首字延迟 | **用哪个模型、花了多久、真实花钱的地方** |
| 具体工具（TOOL，如 `searchHotels`） | 耗时、入参、返回 | 外部 API 调用是否成为瓶颈 |

> 实测：模型调用首字延迟 0.6~1.2s，而 `searchHotels` 单次 5.5~7.9s —— **端到端延迟的大头
> 在外部 API，不在模型**。想"增效"应先优化工具调用，而不是换更快的模型。

**已知限制**

1. 同一轮对话会平铺成**多条并列记录**（1 条 `LangGraph` CHAIN + 若干条 `ChatOpenAI` / `model`
   GENERATION），因为 LangGraph 子图/节点级 LLM run 的父子链路未完整挂载。
   时间戳相同即说明它们来自同一次请求，不是重复上报。若需按 `conversation_id` 归组成一条 trace，
   需要给 handler 配置 `session_id`（尚未实施）。
2. ⚠️ **Usage / Cost 列目前恒为空**（2026-10-10 用 SDK 直接确认：trace 级与 GENERATION 级的
   `usage`、`usage_details` 均为 `None`）。**不是模型没返回**——实测 `usage_metadata` 里
   `input_tokens` / `output_tokens` / `cache_read` 都有值，是上报链路没接住（Langfuse 4.x 走
   OTel handler，疑似未把 `usage_metadata` 映射到 span 属性）。
   因此**缓存命中率暂时看不了 Langfuse**，改用 `python scripts/probe_cache.py` 直接读（见 §十六）。

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
| quick | `req_weather_gated_in_step1` | 需求收集 | 首步不给天气工具（设计如此），要能承诺"定好目的地后帮你查"，且不得说"查不到" |
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
> 运行时默认 `temperature=0`（生产默认 0.7），`--temperature prod` 可与生产逐字一致。
>
> ⚠️ 这个覆盖曾长期**失效**：旧实现只替换 `travel_agent.get_llm`，会被 middleware 每轮的
> `request.override(model=get_main_llm())` 盖掉——报告写着 0，实际却跑在 0.7 上。
> 已改为在 `app/core/llm.py` 工厂层加评测开关，全链路生效（详见本节末尾）。

**评测抓到并已修复的生产缺陷**（2026-10-10）：目的地 Router 的 `explore` 子 Agent 也是 `create_agent` 图、
节点名同为 `model`，其内部攻略（带 `###` / `*` 的景点清单）会**穿透 `chat.py` 的正文白名单**混进用户可见正文，
单条用例最多混入 **633 条**子图片段；这些文本还会作为 assistant 消息**写进数据库**，下一轮又被当历史重放，
既污染体验也推高输入成本。

- **根因**：`destination_router.py` 的 `_explore_agent.ainvoke(...)` 未透传 `config`，子图流式事件一路冒泡到
  外层 `astream_events`；而白名单当时只看 `langgraph_node`，子图节点名恰好也是 `model`，于是被误判放行。
- **修复**：`chat.py::is_answer_stream_event` 增加**命名空间层级判据**。主图 `checkpoint_ns` 只有一层
  （`model:63d0…`），子图是多层（`tools:…|explore:…|model:…`）——**含 `|` 即为子图，不予放行**。
- 没有选择"给子图传 config 切断冒泡"那条路：它依赖 LangChain 的 callback 传播细节，需反复试错；
  而改白名单只需 3 行、且评测复用同一函数（改完跑一遍即可验证）。误杀风险由已有的
  `recover_final_answer` 兜底覆盖。
- **验证**（`--tier all`，与修复前同数据集对比）：

  | 指标 | 修复前 | 修复后 |
  |---|---|---|
  | `subgraph_leak` | 0.667 | **1.0**（稳定） |
  | `format_clean` | 0.583 | 1.0（**不稳定**，见下方说明） |
  | 平均 `total_tokens` / 用例 | 54,716 | **37,505**（−31%） |
  | 平均 `latency_ms` / 用例 | 48,304 | **31,226**（−35%） |

  token 与耗时同步下降，正是因为那些内部攻略不再被塞进正文、也就不会再写进历史被反复重放。
- 这也是评测最有价值的一次产出：**人肉聊几句话根本发现不了**。

  ⚠️ **`format_clean` 的 1.0 曾不可稳定复现**：次日复跑回落至 0.75（3 条超 8 行——
  需求确认清单 7 项、酒店推荐 3 家带细节）。经 A/B 验证与本次改动无关：
  同一条 `req_full_info_advances` 改动前单跑 3/3 通过、改动后 2/2 通过，全量跑时才偶发失败。
  根因是**输出随机性**，加上"清单类内容天然就长"（7 项需求确认 = 至少 9 行）
  与 8 行门禁之间的结构性张力——**这个结构性张力仍在**，只是随机波动已被消除（见下）。
  → 读报告时**不要拿单次 `format_clean` 当结论**，要看趋势。

**已解决：评测温度覆盖失效（报告标注 0，实际跑 0.7）**

评测报告一直标注 `temperature=0`，但**实际跑的是生产默认的 0.7**。
`install_temperature_override` 原先只替换 `travel_agent.get_llm`，
而 middleware 每轮都会 `request.override(model=get_main_llm())` 把模型换成工厂新产出的实例，
替换被完全盖掉。模型分档改造后这条路径成了主路径，问题随之暴露：
**"评测可复现"这个保证是假的**，`format_clean` 时好时坏正源于此。

修复：在 `app/core/llm.py` 工厂层加评测开关 `set_eval_temperature()`，
由 `install_temperature_override` 设置；`build_chat_model` 统一取该值，
于是主对话、子 Agent、RAG 改写与重排**全部覆盖**。
生产永不调用该开关（恒为 `None`），行为完全不变。
验证：设置后 `build_chat_model("main"/"light")` 的温度均为 0，且**档位模型名不变**
（两档均为 `qwen3.8-flash`）；不设置时回落 0.7。

**已解决：首步天气门控命中禁用话术"查不到"**

该用例曾长期失败：首步按设计不提供天气工具，模型被问到天气时无话可依，只能说"暂时查不到实时数据，
建议你看下手机天气App"——后半句（转回收需求）是对的，前半句却踩了"不得否认已接入能力"的红线。
曾有两个候选改法，均已否决：

| 候选 | 结论 | 理由 |
|---|---|---|
| 首步放开天气工具 | ❌ 否决 | 用例问的是「北京今天」= 出发地 + 当天，而下游步骤要的是「目的地 + 行程日期」的天气，<br>城市与日期都不同 → 首步查到的结果对下游 **100% 无用**。且天气走高德**按次计费**，<br>需求收集是所有会话必经、轮次最多的步骤，挂上去就是最宽的付费入口（服务公网暴露） |
| 豁免"查不到"禁用词 | ❌ 否决 | 该门禁的作用是防"否认已接入能力"。一旦开口，目的地步骤真该给天气时说"查不到"也躲过检查，<br>门禁直接失效。且这条失败暴露的是**真问题**不是误报——误报才豁免，真问题该修 |

**真正的根因是提示词缺口**：`requirement_collection` 的 prompt 里早已写好「提前问机票怎么办」
「提前问酒店怎么办」两节话术模板，**唯独天气没有**。模型不是跑偏，是没有话可套才硬编。

**修复**：补上第三节「用户提前问天气怎么办」——禁止否认能力 + 给出话术
（"等目的地和日期定下来，我按你出发那几天查真实天气并给穿衣建议"）+ 明确与旅行无关的天气
一句话带过后转回收需求。修复后同一提问的回复：

> 北京今天的天气我这边暂时没法直接查，不过等咱们定好目的地和出发日期，我会按你那几天的行程查实时天气并给穿衣建议。
> 现在先帮你把旅行需求理清楚，后面查天气、机票、酒店都能更精准。你这次打算从哪个城市出发？大概想玩几天呢？

不再否认能力（明确承诺何时能查），且立刻回到收需求正题。

### 已解决：需求收集卡在首步不推进（`dest_overseas_no_fabricate`）

**症状**：4/4 稳定复现（单跑 3 次 + 全量 1 次），终态恒为 `requirement_collection`，
`query_destination_info` **从未被调用**——这条用例真正想测的"海外天气不编造"**一次都没被测到**。

**病根不是模型跑偏，是 prompt 里两条指令打架**：

| 位置 | 指令 | 实际优先级 |
|---|---|---|
| 【确认与记录】 | 「原则上请用户确认无误后再记录」 | 先出现 → 被当成默认规则 |
| 【必须及时推进流程】 | 5 项齐全且用户认可/追问后续环节 → 必须先 record | 后出现 → 被当例外 |

真实触发点是**预算歧义**：用户说"预算2万"，模型自译为"每人2万"，自己都不确定，
于是不敢落库、转成追问。而"及时推进"条款只豁免了"用户认可"和
"追问后续环节（机票/住哪/吃啥）"——**既没把天气算进"追问后续环节"，也没豁免"存在假设"**。

**改动**（`step_config.py::requirement_collection`，三处）：

1. 「必须及时推进流程」把**天气**并入"已在追问后续环节"的推进触发条件。
2. 新增「带假设先记录」：表述有歧义时**不要停下来反复追问**，按最合理解读先记录，
   并在回复里把假设说清楚请用户纠正——**记错了可以改，但为此卡住不给方案，用户什么都拿不到**。
3. 修掉天气模板里「此时目的地和日期都还没定」这句**绝对表述**：用户若已给出目的地和日期，
   这句话就是假的（本用例第 1 轮就给了巴黎 + 10月20日）。改为条件式——
   已明确则直接承诺"进入下一步就按 <目的地> <日期> 查"并**立刻记录推进**；未明确才用原话术。

**用例也改了**（`scripts/eval_data/core_v1.json`）：第 2 轮由「巴黎天气怎么样」
改为「没问题，就按这个安排。另外巴黎天气怎么样？」。原写法把"能否推进"与"测天气"耦合在一起，
模型一卡住就永远走不到目的地步骤、天气工具也就永远调不到——**用例等于空跑**。

**修复后实测**（第 1 轮即带假设记录；第 2 轮一轮内连调
`record_requirement_tool` → `query_destination_info` → `get_weather_forecast`，终态成功推进）：

> 关于2万预算我先按每人2万记录（含机票酒店），如果是两人总共请告诉我马上改。
> 巴黎的实时天气我这边覆盖不到，建议出发前用天气App自行确认当地情况。

用"覆盖不到"而非"查不到"，未踩禁用话术。

⚠️ **仍需知道的边界**：它仍会给出「10月下旬巴黎通常 8-16℃」这类**气候常识**（非实时数据），
但已明确标注"通常"并建议自行确认，符合用例 note 的要求。若要彻底禁止，需另加断言。

**顺带修掉的一处结构性冲突**：需求确认清单原本被要求"逐项列出 8 个字段"，
而全局门禁是"正文 ≤8 行"——**prompt 自己要求的格式必然突破门禁**。
温度固定后这条稳定复现（第 1 轮 10 行）。已把格式要求改为
「压缩成 1-3 行、字段间用 · 分隔」，示例：`北京出发 · 目的地巴黎 · 10月20日起5天 · 2成人 · 预算2万/人 · 文化探索`。

**全量验证**（`--tier all`）：12/12 成功、0 异常，门禁全 1（含 `format_clean` 1.0）、
`step_path_score` 通过率 1.0、`subgraph_leak` 1.0——**本项目首次全量判定 passed**。

## 十六、模型分档与成本（降本）

一个模型跑全链路是最省事的写法，但**不省钱**。本项目实测 token 结构是 **输入占 96%**
（quick 档 8 条：平均输入 13,749 / 输出 560），成本几乎全部来自"每轮把上下文重放一遍"，
于是"哪些环节配得上旗舰价"就成了最大的成本阀门。

**实现**：`app/core/llm.py` 是全项目**唯一**定义"档位 → 模型名"的地方，杜绝某处硬编码模型名后、
该模型免费额度用尽导致局部 403 却极难排查（历史上真实发生过）。

| 档位 | 环境变量 | 代码默认值 | 承担什么活 |
|---|---|---|---|
| `main`（权衡题） | `QWEN_MODEL_NAME` | `qwen3.8-flash` | 跨约束推理 / 编排 / 计算 |
| `light`（选择题） | `QWEN_LIGHT_MODEL_NAME` | `qwen3.8-flash` | 改写、重排、分类、结构化抽取、格式化、简单问答 |

> 2026-10-10 起两档**统一为 `qwen3.8-flash`**（等价于安全阀常开：单一模型跑全链路）。
> 分档机制保留，需要重新拉开档位时只改 `.env` 两个变量即可，不动代码。
>
> ⚠️ **代码默认值可被 `.env` 覆盖，而生产值会被额度状况推着走**。百炼免费额度**按模型独立**，
> 某个模型额度耗尽只让走它的那档 403，另一档照常——这是本项目最难排查的故障模式之一。
> 排查口诀：**先看是哪一档在报错，再查那一档模型的额度**，不要一上来就怀疑代码。

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
| 订单生成 | main ⚠️ | 见下方说明：曾判为 light，**已回退** |

> ⚠️ **两次"降级翻车"的教训 —— 哪个环节能降级不能靠直觉，必须跑评测**
>
> 1. **需求收集**：它是最像"简单问答"的一步，最初按"选择题"下沉到了 `qwen3.7-flash`，但评测
>    `req_weather_gated_in_step1` **稳定复现（2/2）**一次参数缺失的无效工具调用
>    （`get_weather_forecast` 未传 `city_adcode` → `ToolException` → 该轮完全没有回复）；
>    换回 `qwen3.8-max` 后同一条用例不再报错。它是**所有会话必经过**的步骤，一次空回复的代价
>    远大于省下的 token。
> 2. **订单生成**：当初判它是"格式化输出订单号 + 写出行记录"就给了 light，判轻了。Langfuse 生产
>    trace 显示这一步实际输出的是**最终交付给用户的整份行程 + 预算汇总**（实测 11 行正文），
>    是用户整段对话看到的最后一屏，不是内部格式化工序。用最便宜的档收尾，省不下多少 token，
>    却押上了最终观感。2026-10-10 回退为 main。

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
| qwen3.7-plus | 高于 flash、低于 max（未核对） | — | — |

> 思考 token 按输出价计费，所以两档都强制 `enable_thinking=False`。
>
> ⚠️ **单价不是唯一约束，额度才是**。百炼免费额度**按模型独立**，模型一旦额度耗尽就是 403，
> 单价再便宜也用不了。2026-10-09 `qwen3.8-flash`/`qwen3.8-omni-flash` 耗尽 → main 切 `qwen3.8-max`；
> 2026-10-10 `qwen3.7-flash` 耗尽 → light 切 `qwen3.7-plus`（代价：端到端 44s → 68s/用例）。
> 2026-10-10 晚：额度问题解决后**两档统一回 `qwen3.8-flash`**（代码默认值亦同步改掉），
> 既省单价又恢复速度；若该模型再次 403，唯一动作就是改 `.env` 两个变量换档。

**安全阀**：把 `QWEN_LIGHT_MODEL_NAME` 设成与 `QWEN_MODEL_NAME` 同一个模型（当前两档都已写 `qwen3.8-flash`），
等价于"全量回退到单一档"，**不需要改任何代码**。

### 上下文缓存：实测命中多少 —— 和 Redis 无关

这里说的是**百炼（模型服务商）侧的上下文缓存 Context Cache**，是服务端的前缀 KV 缓存；
不是本项目的应用层缓存，也**不需要 Redis**（本项目确认未使用 Redis，见 §二）。

- **未命中（原价）**：输入 token 按原价计费（如 max 的 12 元 / 百万）。
- **命中（约 1.25 折）**：前缀未变的部分按"缓存命中价"计费（max 为 1.5 元 / 百万）。

我们在 `app/core/llm.py` 里传 `extra_body={"enable_context_cache": True}` 开启它。

**实测数据**（2026-10-10，`python scripts/probe_cache.py`，读 `usage_metadata.input_token_details.cache_read`）

| 场景 | 输入 tokens | `cache_read` | 命中率 |
|---|---|---|---|
| 短前缀，重复调用 | 981 | 0 | **0%** |
| 长前缀，首次调用 | 3261 | 0 | **0%** |
| 长前缀，第 2 次起 | 3261 | 3072 | **94.2%** |

三点结论：

1. **缓存确实生效**，且前缀稳定时命中率可达 **94%**，远高于早期估算里假设的 60%。
2. **存在最小可缓存粒度门槛**：3072 = 1024 × 3，说明按 1024 tokens 分块，不足 1024 的
   前缀**永远不会命中**（短前缀那一行为 0 就是这个原因，不是缓存没开）。
   3261 − 3072 = 189 的尾巴对不齐块，所以是 94.2% 而不是 100%。
3. **真实链路的命中率会低于 94%**。开关只表示"允许命中"，实际命中取决于前缀是否稳定；
   目前中间件每轮 `request.override(tools=...)` 会按步骤换工具集，schema 一变就打断前缀。
   **量化这个损失是下一步最值得做的降本调研**。

> 探针脚本 `scripts/probe_cache.py` 会分别用短前缀 / 长前缀、非流式 / 流式各调若干次并打印
> `cache_read`。之所以需要它，是因为 **Langfuse 当前上报不到 usage**（详见 §十四），
> 控制台上看不到这个数。

## 十七、许可证

自己学习，许可证另行约定。
