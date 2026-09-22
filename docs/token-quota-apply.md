# 把「每人 500 token 配额」上线到服务器

> 目标值：`DEFAULT_USER_TOKEN_QUOTA=500`，`QUOTA_REQUEST_CONTACT=18204331623`
> 本地 `E:\travel-planner\.env` 已改好（第 61、63 行），**服务器上的 `.env` 是另一份，必须单独改**。
> 所有命令在**阿里云 Web 终端**里跑（提示符 `[root@...]#`），本地 CMD 跑 Linux 命令必报错。

---

## Step 1 同步服务器 `.env`（幂等，可重复执行）

```bash
cd /root/travel-planner && cp .env .env.bak-$(date +%m%d-%H%M) && \
sed -i 's|^DEFAULT_USER_TOKEN_QUOTA=.*|DEFAULT_USER_TOKEN_QUOTA=500|' .env && \
sed -i 's|^QUOTA_REQUEST_CONTACT=.*|QUOTA_REQUEST_CONTACT=18204331623|' .env && \
grep -q '^DEFAULT_USER_TOKEN_QUOTA=' .env || echo 'DEFAULT_USER_TOKEN_QUOTA=500' >> .env && \
grep -q '^QUOTA_REQUEST_CONTACT=' .env || echo 'QUOTA_REQUEST_CONTACT=18204331623' >> .env && \
grep -nE '^(DEFAULT_USER_TOKEN_QUOTA|QUOTA_REQUEST_CONTACT)=' .env
```

期望最后两行输出：

```
DEFAULT_USER_TOKEN_QUOTA=500
QUOTA_REQUEST_CONTACT=18204331623
```

## Step 2 让配置生效（必须重建容器，`restart` 不行）

```bash
docker compose up -d --force-recreate backend
```

> ⚠️ `docker compose restart backend` **不重读 `env_file`**：容器环境变量在创建时就固化了，
> 只重启进程不会拿到新值。`--force-recreate` 不需要 `--build`，几秒钟完成。

## Step 3 验证（唯一可信的确认方式）

```bash
docker exec travel_backend python -c "from app.config import settings; print('quota=', settings.default_user_token_quota, '| contact=', settings.quota_request_contact)"
```

期望：`quota= 500 | contact= 18204331623`

```bash
curl -s -o /dev/null -w "app_page=%{http_code}\n" http://localhost:8000/app/
```

期望：`app_page=200`

## Step 4 给自己（admin）开大额度，避免自己被卡

> ⚠️ **别用纯 SQL 直插 `token_usage`**：SQLAlchemy 的 `default=0` / `func.now()` 都是
> **Python/表达式端默认值**，数据库列上**没有真正的 DEFAULT 约束**。纯 SQL 漏掉任何 NOT NULL 列
> （`used_tokens` / `updated_at` 等）都会触发 `violates not-null constraint`，而且 INSERT 会整体回滚。
> **最稳的做法是走 ORM —— 在容器里用 Python 调 grant 接口**，所有列默认值由代码处理。

```bash
docker exec -i travel_backend python - <<'PY'
import urllib.request, json

def call(url, data=None, headers=None, method="POST"):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

tok = call("http://127.0.0.1:8000/api/v1/users/login",
           {"username": "admin", "password": "travel2026"})["access_token"]
print("token_len =", len(tok))

res = call("http://127.0.0.1:8000/api/v1/users/quota/grant?username=admin&quota_tokens=5000000",
           headers={"Authorization": "Bearer " + tok})
print("grant result:", json.dumps(res, ensure_ascii=False))
PY
```

> ⚠️ **坑**：`urllib.request` 发带 body 的请求时**默认 `Content-Type: application/x-www-form-urlencoded`**，
> 不显式加 `Content-Type: application/json`，FastAPI 会把 JSON 当表单解析 → `422 Unprocessable Content`。
> 上面 `call()` 已默认带上 JSON 头，务必保留。

期望：`token_len = <三位数>` + `grant result: {... "quota_tokens": 5000000, "exceeded": false ...}`。

验证 admin 额度是否到位：

```bash
docker exec -i postgres psql -U travel_user -d travel_planner_db -c \
"SELECT u.username, t.used_tokens, t.quota_tokens, t.request_count FROM token_usage t JOIN \"user\" u ON u.id=t.user_id WHERE u.username='admin';"
```

期望看到 `admin | 0 | 5000000 | 0`。

> 纯 SQL 兜底（若一定要直插）：必须显式写全所有 NOT NULL 列，含 `updated_at = now()`：
> `INSERT INTO token_usage (user_id, prompt_tokens, completion_tokens, used_tokens, request_count, quota_tokens, updated_at) SELECT id, 0, 0, 0, 0, 5000000, now() FROM "user" WHERE username='admin' ON CONFLICT (user_id) DO UPDATE SET quota_tokens=5000000;`

给别的账号加量同理：把 `username='admin'` 换成对方用户名、`5000000` 换成要给的额度。

## Step 5 量一下「一轮对话到底烧多少」，再决定 500 是否合适

先用任意账号发一句话，然后查库：

```bash
docker exec -i postgres psql -U travel_user -d travel_planner_db -c \
'select u.username, t.used_tokens, t.request_count from token_usage t join "user" u on u.id = t.user_id order by t.used_tokens desc limit 10;'
```

把 `used_tokens / request_count` 算出来就是**单轮真实消耗**（通常 3000~8000，因为每轮都要重发
system prompt + 20 个 MCP 工具描述 + 历史）。

| 想要的体验 | 建议 `DEFAULT_USER_TOKEN_QUOTA` | 1,000,000 免费额度能撑 |
|---|---|---|
| 只让体验 1 句，然后引导联系你 | 500（当前值） | ~1600 人次 |
| 能完整跑完一轮行程规划（3~6 轮对话） | 30000 | ~25 人 |
| 宽松体验，适合面试官慢慢试 | 100000 | ~8 人 |

改数值：重跑 Step 1（把 500 换成新值）+ Step 2 即可，无需迁移数据库。

---

## 背景：两本账别混淆

- **应用层配额**（本页在改的）：`token_usage` 表按账号累计，超限 `POST /chat/stream` 直接 402，
  页面弹「额度用尽 + 申请」。只防白嫖，不改变云厂商账单。
- **百炼账号免费额度**（你截图里的 `847,370/1,000,000`，过期 2026/11/25）：**所有账号合计**的总池子，
  开关「免费额度用完即停」打开时，池子空了整个服务就不可用。应用层配额只能延缓它被消耗的速度。
