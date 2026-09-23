# 把「每人 3000 token 配额」上线到服务器

> 目标值：`DEFAULT_USER_TOKEN_QUOTA=3000`，`QUOTA_REQUEST_CONTACT=电话：18204331623 / 邮箱：3552955252@qq.com`
> 本地 `E:\travel-planner\.env` 已改好（第 61、63 行），**服务器上的 `.env` 是另一份，必须单独改**。
> 所有命令在**阿里云 Web 终端**里跑（提示符 `[root@...]#`），本地 CMD 跑 Linux 命令必报错。

---

## 现状核查（2026-09-23，实测数据）

```bash
docker exec postgres psql -U travel_user -d travel_planner_db -c \
'SELECT u.username, u.email, t.used_tokens, t.quota_tokens, t.request_count FROM token_usage t JOIN "user" u ON u.id=t.user_id ORDER BY t.used_tokens DESC;'
```

当时结果：

| username | used_tokens | quota_tokens | request_count |
|---|---|---|---|
| 18204331623（管理员） | 34497 | 9999999999（无限） | 2 |
| wangyuan | **11585** | 0（= 跟随全局默认 500） | **1** |
| verify_wx_*（两个测试号） | 0 | 0 | 0 |

**结论一：500 是真的生效了**，不是显示 bug。`wangyuan` 的 `quota_tokens=0`，按
`effective_quota()` 回落到全局默认 500 → 用满即 402。

**结论二：500 连一条消息都撑不过。** 该账号只发了 **1 条**消息（`request_count=1`），
就消耗了 **11585 token**（约 500 的 23 倍）。原因是每轮都要重发 system prompt +
注入的工具描述 + 历史，且模型 `qwen3.8-omni-flash` 是**推理模型**（reasoning token 也计费）。
→ **500 的正确语义是「只让体验 1 句，然后引导对方联系你」，不是 bug。**

**结论三：想让人完整跑完一次行程规划，建议 30000~100000**（见 Step 5 的对照表）。

> 注：`used_tokens >= quota_tokens` 就判超额，所以 3000 必然在第 1 轮结束前就被拦截
> （拦截发生在每轮**开始前**，第 2 条消息才会被 402 挡住）。
>
> **2026-09-23 下午更新**：全局默认已由 `500` 调为 `3000`，管理员「通过」默认加量由
> `100000` 调为 `20000`。下面 Step 1 的命令已是新值（上表 500 是当时的实测记录，保留备查）。

---

## Step 1 同步服务器 `.env`（幂等，可重复执行）

```bash
cd /root/travel-planner && cp .env .env.bak-$(date +%m%d-%H%M) && \
sed -i 's|^DEFAULT_USER_TOKEN_QUOTA=.*|DEFAULT_USER_TOKEN_QUOTA=3000|' .env && \
sed -i 's|^QUOTA_REQUEST_CONTACT=.*|QUOTA_REQUEST_CONTACT=电话：18204331623 / 邮箱：3552955252@qq.com|' .env && \
grep -q '^DEFAULT_USER_TOKEN_QUOTA=' .env || echo 'DEFAULT_USER_TOKEN_QUOTA=3000' >> .env && \
grep -q '^QUOTA_REQUEST_CONTACT=' .env || echo 'QUOTA_REQUEST_CONTACT=电话：18204331623 / 邮箱：3552955252@qq.com' >> .env && \
grep -nE '^(DEFAULT_USER_TOKEN_QUOTA|QUOTA_REQUEST_CONTACT)=' .env
```

期望最后两行输出：

```
DEFAULT_USER_TOKEN_QUOTA=3000
QUOTA_REQUEST_CONTACT=电话：18204331623 / 邮箱：3552955252@qq.com
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

期望：`quota= 3000 | contact= 电话：18204331623 / 邮箱：3552955252@qq.com`

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

## Step 5 量一下「一轮对话到底烧多少」，再决定 3000 是否合适

先用任意账号发一句话，然后查库：

```bash
docker exec -i postgres psql -U travel_user -d travel_planner_db -c \
'select u.username, t.used_tokens, t.request_count from token_usage t join "user" u on u.id = t.user_id order by t.used_tokens desc limit 10;'
```

把 `used_tokens / request_count` 算出来就是**单轮真实消耗**。

**2026-09-23 实测：`wangyuan` 发 1 条 = 11585 token**（比早期估的 3000~8000 高不少，
因为模型换成推理模型 `qwen3.8-omni-flash`，reasoning token 也计费）。

| 想要的体验 | 建议 `DEFAULT_USER_TOKEN_QUOTA` | 大致能发几条 | 1,000,000 免费额度能撑 |
|---|---|---|---|
| 只让体验 1 句，然后引导联系你 | 3000（当前值） | 1 条 | ~86 条消息总量 |
| 能试 2~3 轮 | 30000 | 2~3 条 | ~28 人 × 3 条 |
| 完整跑完一次行程规划（3~6 轮） | 100000 | 8~9 条，够用 | ~8 人 |

改数值：重跑 Step 1（把 `3000` 换成新值）+ Step 2 即可，**无需迁移数据库**
（`quota_tokens=0` 的老账号会自动跟随新的全局默认）。

> 只想给某一个人放量（不改全局默认）：登录管理员账号后在页面右上角「消息通知」里点「通过」，
> 或直接调接口
> `POST /api/v1/users/quota/grant?username=xxx&quota_tokens=20000`。

---

## Step 6 提额申请：用户端弹窗 + 管理员「消息通知」

### 用户侧（外部访客）

- 额度用尽时后端返回 **402**，前端会同时做两件事：
  1. 侧边栏额度条显示「免费体验额度已用尽…可联系：`QUOTA_REQUEST_CONTACT`」；
  2. **弹出独立弹窗**，把 `QUOTA_REQUEST_CONTACT` 大号展示出来，**点一下即可复制**，
     并提供「申请更多额度」自助提交按钮。
- 若 `QUOTA_REQUEST_CONTACT` 留空，弹窗退化为「请联系管理员开通更多额度」（不再只提示「联系管理员」）。
- ⚠️ `QUOTA_REQUEST_CONTACT` 支持任意文本，想同时给邮箱和微信就写成
  `微信：xxx / 邮箱：xxx`（当前值是 `电话：18204331623 / 邮箱：3552955252@qq.com`）。

### 管理员侧

登录 `BOOTSTRAP_ADMIN_USERNAME` 账号后，侧边栏多出 **「消息通知」** 按钮（其他账号看不到，
由 `GET /me` 返回的 `is_admin` 决定），带**未处理数量角标**。点开是申请列表：

- **只显示电子邮箱**（出于保密，不显示用户名 / user_id）；
- 每条可「通过」（默认加 **20000** token）或「忽略」；
- 通过/忽略后角标自动刷新。

相关接口（均需管理员身份，否则 403）：

```
GET  /api/v1/users/quota-requests?status_filter=pending   # 列表（只回 email + 时间 + 状态）
POST /api/v1/users/quota-requests/{request_id}/approve?quota_tokens=20000
POST /api/v1/users/quota-requests/{request_id}/reject
```

> 设计要点：因为前端拿不到用户名，**加量走 `request_id` 而不是 `username`**，
> 这样「只显示邮箱」和「一键通过」才能同时成立。

命令行兜底（不点页面也能看到申请）：

```bash
docker exec postgres psql -U travel_user -d travel_planner_db -c \
'SELECT email, status, created_at FROM quota_request ORDER BY created_at DESC LIMIT 20;'
```

---

## 背景：两本账别混淆

- **应用层配额**（本页在改的）：`token_usage` 表按账号累计，超限 `POST /chat/stream` 直接 402，
  页面弹「额度用尽 + 申请」。只防白嫖，不改变云厂商账单。
- **百炼账号免费额度**（你截图里的 `847,370/1,000,000`，过期 2026/11/25）：**所有账号合计**的总池子，
  开关「免费额度用完即停」打开时，池子空了整个服务就不可用。应用层配额只能延缓它被消耗的速度。
