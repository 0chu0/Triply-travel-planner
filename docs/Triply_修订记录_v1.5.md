# Triply 需求规格 · 修订记录归档

## 1.1 修订记录

表2：修订记录表

记录每次文档更新的时间、作者与修订内容，便于追溯历史变动。

| 日期 | 版本 | 修改人 | 说明 |
| --- | --- | --- | --- |
| 2026-09-25 | v1.0 | 初禹萱 | 初稿，基于线上 v1.0 真实能力 |
| 2026-09-30 | v1.4 | 初禹萱 | 上线后迭代：RAG 召回错配修复；新增 48 个热门旅游地知识库；天气 MCP 修复；LLM 预算口径收缩 |
| 2026-10-01 | v1.5 | 初禹萱 | MCP 接入层重构 + 线上回归修复：① 工具筛选由「工具名子串匹配」改为「capability 能力标签聚合」，日期能力从航班服务解耦为本地 `get_today_date`，地图 POI 从酒店剥离为独立 `map_poi`（提交 `1b345fd`，v1.6 收口三子 Agent 子串匹配残留）；② 磁盘满致 PostgreSQL 崩溃恢复死循环，`docker system prune` 清 13.5GB 缓存 + `restart postgres` 恢复；③ `client.py` 误透传 `capability` 致 5 个 MCP 服务加载 0 工具，剥离后恢复共加载 29 工具；④ `travel_agent.py` 漏登记 `get_today_date` 致聊天 `Unknown tools`，补登记修复；⑤ 确认首步 `requirement_collection` 天气门控为设计原意，维持不变 |

> 注：你原表 v1.5 写作 2026-09-30，实际重构落地 2026-10-01、回归热修 2026-10-02，此处按真实变更日填写；如需回退为 09-30 告知即可。

---

## v1.5 详细变更：MCP 接入层重构（并入原 `MCP接入层重构_修订记录_v1.5.md`）

### 范围
把「按工具名子串匹配筛选 MCP 工具」改为「按能力标签（capability）聚合」，并把日期能力从航班服务解耦到本地工具。
改动文件：`app/mcp_core/client.py`、`app/tools/mcp_tools.py`、新增 `app/tools/date_tools.py`、`app/agents/handoffs/step_config.py`。提交 `1b345fd`。

### 问题 1：工具筛选靠工具名子串匹配，外部服务一改名就静默失效
旧 `mcp_tools.py` 用 `any(keyword in tool.name.lower() ...)` 捞目标工具，关键词写死；外部服务改名/换供应商会静默变空列表，不报错但模型那步拿不到工具（"天气查不到/日期算不出"）。
**解决**：① `SERVER_CONFIGS` 给每服务加 `capability` 标签（`weather:[weather]`、`search:[search]`、`amap:[map_poi]`、`VariFlight-Aviation:[flight]`、`aigohotel-mcp:[hotel]`）；② `initialize()` 缓存 `_server_tools`；③ 新增 `get_tools_by_capability(capability)` 按标签聚合，不再子串匹配；④ `get_weather/search/hotel_tools` 改调 `_by_capability`。
**收益**：外部改名只要标签不变即照常聚合，能力清单集中一处。

### 问题 2：日期能力寄生在航班服务（单点依赖）
旧 `get_date_tools()` 用 `gettodaydate` 子串从 VariFlight 捞 `getTodayDate` 当"今天几号"；航班挂了/换名/Key 失效即丢日期。
**解决**：① 新增 `date_tools.py` 用 `datetime` 实现本地 `get_today_date()` 返回 `YYYY-MM-DD（周X）`；② `get_date_tools()` 返回 `[get_today_date]`（零外部依赖）；③ VariFlight `capability` 由 `[flight,date]` 收敛为 `[flight]`。
**收益**：日期彻底摆脱航班单点，航班挂了"今天几号"仍可用。

### 问题 3：MCP 配置硬编码在 Python，增减服务须改代码 + 重建镜像
5 个服务连接信息写死 `SERVER_CONFIGS`。本次仅把 `capability` 一并收进 `SERVER_CONFIGS`；**后续建议（未做）**：外置为 `config/mcp_servers.json` + volume 挂载，实现不改镜像即可增删服务。

### 问题 4：地图与天气边界模糊、周边 POI 混进"酒店"
旧 `get_hotel_tools()` 关键词塞了 `maps_around_search`（高德周边 POI），且 amap 的 `maps_weather` 与自建 `weather_server` 重叠。
**解决**：① 新增 `get_map_poi_tools()`（`map_poi`）把周边 POI 从酒店剥离；② `step_config` 步骤 2/4 显式补 `*map_poi_tools`；③ amap `capability` 只挂 `map_poi`、**不挂 `weather`**（避免高德 15 工具整体误并入天气步骤致 token 暴涨）。天气仍由自建 `weather_server` 单一提供。

### v1.6 补充：三子 Agent 子串匹配残留收口
`flight_agent.py` / `transport_coordinator.py` / `driving_agent.py` 仍各自用工具名子串匹配，v1.6 统一迁移到 `get_tools_by_capability` + 本地 `get_today_date`；全量扫描确认无 `getTodayDate` / `get-current-date` / `getfutureweather` / `keyword in tool.name` 残留（`flight_agent` 仅保留一处对旧 `gettodaydate` 的精确单名排除，有意设计）。

### 验收 / 部署
部署后 `docker compose logs backend` 应见 `📅 日期工具(本地): ['get_today_date']`、`🌤️ 天气工具: ['get_weather_forecast']`、`🔧 能力[map_poi]工具` 非空；对话实测问天气先调本地日期再换算，置空 `VARIFLIGHT_API_KEY` 日期仍正常。部署：`git push origin master` → 服务器 `git pull` → `docker compose up -d --force-recreate --build backend`。
