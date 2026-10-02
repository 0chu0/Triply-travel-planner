# Triply · MCP 接入层重构修订记录（v1.5）

> 范围：把「按工具名子串匹配筛选 MCP 工具」改为「按能力标签（capability）聚合」，并把日期能力从航班服务解耦到本地工具。
> 改动文件：`app/mcp_core/client.py`、`app/tools/mcp_tools.py`、新增 `app/tools/date_tools.py`、`app/agents/handoffs/step_config.py`
> 提交：`1b345fd`（本地已提交，待推送 + 服务器 rebuild）
> 对上层的透明性：`get_weather_tools / get_search_tools / get_hotel_tools / get_date_tools` 函数名与返回值结构不变，`step_config.py` 里 `*weather_tools / *search_tools / *hotel_tools / *date_tools` 引用**一行未动**；仅新增 `get_map_poi_tools` 并补进步骤 2、4。

---

## 问题 1：工具筛选靠「工具名子串匹配」，外部服务一改名就静默失效

**现象 / 根因**
旧 `mcp_tools.py` 用 `any(keyword in tool.name.lower() for keyword in [...])` 从全量 MCP 工具里捞目标工具（如 `'searchhotels'`、`'gettodaydate'`、`'maps_around_search'`）。
痛点：这些关键词是「写死」的。一旦某个外部 MCP 服务改了工具名（或换了供应商、工具数变了），筛选结果会**静默变成空列表**——不报错，但模型那一步就「物理上拿不到工具」，表现就是「天气查不到 / 日期算不出」这类偶发故障，极难定位。历史上「天气工具为空」就属于同构问题。

**解决**
1. 在 `client.py` 的 `SERVER_CONFIGS` 给每个服务加 `capability` 标签，声明「我能提供什么能力」：
   - `weather:[weather]`、`search:[search]`、`amap:[map_poi]`、`VariFlight-Aviation:[flight]`、`aigohotel-mcp:[hotel]`
2. `initialize()` 在逐服务加载工具时，额外缓存 `self._server_tools = {服务名: 工具列表}`（per-server 缓存）。
3. 新增同步方法 `get_tools_by_capability(capability)`：直接遍历 `SERVER_CONFIGS` 的 `capability` 标签聚合，**不再对工具名做任何子串匹配**。
4. `get_weather_tools / get_search_tools / get_hotel_tools` 全部改为 `await _by_capability("weather"/"search"/"hotel")`。

**收益**：外部服务改名/换供应商，只要 `capability` 标签不变，工具照常被聚合；能力清单集中在 `SERVER_CONFIGS` 一处可见，不再散落各筛选函数。

---

## 问题 2：日期能力寄生在航班服务上（单点依赖）

**现象 / 根因**
旧 `get_date_tools()` 用 `'gettodaydate'` 子串从 VariFlight 航班服务捞 `getTodayDate` 当作「今天几号」。
痛点：「当前日期」是确定性逻辑（本机 `datetime` 即可算），却绕一圈去依赖航班服务。航班服务一旦挂掉、或 `VARIFLIGHT_API_KEY` 失效、或该工具改名，多步流程（需求收集等）依赖的日期能力就跟着丢失——而航班挂了本来不该影响「今天几号」。

**解决**
1. 新增 `app/tools/date_tools.py`，用 `datetime` 实现本地工具 `get_today_date()`，返回 `YYYY-MM-DD（周X）`，并写明「相对/模糊时间必须先调本工具」。
2. `get_date_tools()` 改为直接返回 `[get_today_date]`（本地，零外部依赖）。
3. 把 `VariFlight-Aviation` 的 `capability` 从 `[flight, date]` 收敛为 `[flight]`（日期标签移除）。

**收益**：日期能力彻底摆脱航班服务单点依赖；航班挂了，「今天几号」依然可用。

---

## 问题 3：MCP 配置硬编码在 Python 里，增减服务须改代码 + 重建镜像

**现象 / 根因**
5 个 MCP 服务的连接信息（url / command / key）全部写死在 `client.py` 的 `SERVER_CONFIGS`，没有 `mcp.json`。要新增/调整一个 MCP 服务，必须改 `app/` 代码并 `docker compose up -d --force-recreate --build backend`，不能热生效。

**解决（本次部分落地）**
- 借本次重构，把 `capability` 标签一并收进 `SERVER_CONFIGS`，让「服务清单」与「能力声明」同处一文件、结构更清晰。
- **后续建议（未做）**：把 `SERVER_CONFIGS` 抽成 `config/mcp_servers.json` 并通过 volume 挂载，实现不改镜像即可增删 MCP 服务。（属配置外置优化，不在本次 2/3/4 步内。）

---

## 问题 4：地图与天气能力边界模糊、周边 POI 被混进「酒店」

**现象 / 根因**
旧 `get_hotel_tools()` 的关键词里塞了 `'maps_around_search'`（高德周边 POI），导致「酒店工具」实际混入了地图能力；同时 amap 自带 `maps_weather` 与自建 `weather_server` 天气能力重叠却互不知情。

**解决**
1. 新增 `get_map_poi_tools()`（`capability="map_poi"`，来自 amap），把周边 POI 能力从「酒店」里干净剥离。
2. 在 `step_config.py` 的步骤 2（目的地推荐）、步骤 4（住宿规划）工具列表显式补 `*map_poi_tools`，还原原先 `maps_around_search` 的可用面（之前它藏在 `hotel_tools` 里）。
3. amap 的 `capability` 只挂 `map_poi`、**不挂 `weather`**：避免把高德 15 个工具（含路线/天气）整体误并入「天气工具」列表，导致天气步骤被无关工具干扰、token 暴涨。天气仍由自建 `weather_server` 单一、稳定提供（其 geo 限流问题已在前期修复）。

> 说明：此点与最初方案草稿 `amap:[map_poi, weather]` 有出入——经实测，服务器级 capability 会把该服务全部工具归入该标签，挂 `weather` 会把 15 个高德工具全塞进天气步骤，弊大于利，故收敛为仅 `map_poi`。若日后想要「高德天气」作天气冗余备份，可单独为 `maps_weather` 打 weather 标签或新建专用函数，不影响本次架构。

---

## 未改动（已知 follow-up，超出本次 2/3/4 步范围）

- ✅ **已在 v1.6 收口**（见文末 v1.6 节）：`flight_agent.py` / `transport_coordinator.py` / `driving_agent.py` 三处子串匹配已全部迁移到 `capability` 聚合 + 本地 `get_today_date`，残留脆弱性消除。
- 未做「配置外置为 json」（见问题 3 后续建议）。

---

## 验收方式（部署后）

1. `docker compose logs backend 2>&1 | grep -E "能力\[|日期工具|天气工具|酒店工具"` 应看到：
   - `📅 日期工具(本地): ['get_today_date']`（不再依赖 VariFlight）
   - `🌤️ 天气工具: ['get_weather_forecast']`
   - `🔧 能力[map_poi]工具: ['maps_around_search', 'maps_geo', ...]`
2. 对话实测：问「下周去三亚天气怎么样」→ 模型先调本地 `get_today_date` 拿到真实日期，再换算；天气走自建服务。即使临时把 `VARIFLIGHT_API_KEY` 置空，日期换算仍正常（无航班也不影响「今天几号」）。

---

## 部署步骤（你来执行）

```bash
git push origin master
```
```bash
cd /root/travel-planner
git pull
docker compose up -d --force-recreate --build backend
```


---

## v1.6 补充：子 Agent 工具筛选残留收口（follow-up 闭环）

**触发**：v1.5 把 `mcp_tools.py` 等主链路切到 capability 聚合，但三个子 Agent（`flight_agent` / `transport_coordinator` / `driving_agent`）仍各自用工具名子串匹配，属未清理的残留脆弱点。本次用户确认后统一收口。

**改法**
- `flight_agent.py` → `_get_aviation_tools()`：
  - 由 `any(keyword in tool.name ... ['flight','aviation','searchflights','gettodaydate'])` 改为 `manager.get_tools_by_capability("flight")`；
  - 精确剔除 VariFlight 可能暴露的旧 `getTodayDate`（`t.name.lower() != "gettodaydate"`，属显式单名排除，非子串扫描），改用本地 `get_today_date`；
  - system prompt 两处 `getTodayDate` → `get_today_date`。
- `transport_coordinator.py` → `_get_auxiliary_tools()`：
  - 由子串 `['getfutureweather','get-current-date','maps_around_search']` 改为 `await get_map_poi_tools() + await get_date_tools()`（复用统一层）；
  - system prompt 校正过期引用：`get-current-date` → `get_today_date`；删除并不存在的 `getFutureWeatherByAirport` 一行；补 `maps_around_search` 真实说明。
- `driving_agent.py` → `_get_amap_tools()`：由子串 `['maps_direction_driving','maps_geo']` 改为 `await get_map_poi_tools()`（复用统一层）。

**验证**：三文件 `py_compile` 通过；全量扫描确认无 `getTodayDate` / `get-current-date` / `getfutureweather` / `keyword in tool.name` 残留。`flight_agent` 中仅保留一处对旧 `gettodaydate` 的精确排除（有意设计，防 VariFlight 旧日期工具混入）。

**部署**：随 v1.5 一起 `git push` → 服务器 `git pull` → `docker compose up -d --force-recreate --build backend`。
