#!/usr/bin/env bash
# ============================================================
# travel-planner 数据库部署脚本（PostgreSQL + pgvector + Redis）
# 目标：Linux 服务器（CentOS / Ubuntu）
# 用法：把本工程传到服务器后，在含本脚本的目录下执行
#       sudo bash deploy_db.sh
# 安全提示：脚本内含数据库密码，部署完成后建议 chmod 600 deploy_db.sh
# 本脚本参数已对齐 E:\travel-planner\.env 的实际取值
# ============================================================
set -e

echo "==> 创建数据持久化目录"
sudo mkdir -p /data/postgres
sudo mkdir -p /data/redis/data /data/redis/config

echo "==> 启动 PostgreSQL（含 pgvector 扩展），端口 5432"
docker run -d --name postgres \
  -e POSTGRES_PASSWORD=travel123456 \
  -e POSTGRES_USER=travel_user \
  -e POSTGRES_DB=travel_planner_db \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -p 5432:5432 \
  -v /data/postgres:/var/lib/postgresql/data \
  pgvector/pgvector:pg17

echo "==> 调整 Redis 内存超量使用策略（避免后台保存失败）"
sudo sysctl vm.overcommit_memory=1

echo "==> 启动 Redis（端口 6379，无密码，db 0，开启键空间事件）"
docker run -p 6379:6379 --name redis -d --restart=always \
  -v /data/redis/data:/data \
  redis:latest \
  redis-server --notify-keyspace-events Ex

echo "==> 等待容器就绪..."
sleep 5
docker ps

echo ""
echo "==> 验证 Redis 连通（应返回 PONG）"
docker exec redis redis-cli ping

echo ""
echo "数据库就绪。后续步骤："
echo "  1) 构建并启动后端：  docker compose build --no-cache && docker compose up -d"
echo "  2) 初始化库表：      docker compose exec backend python scripts/init_db.py"
echo "  3) 验证：            curl http://localhost:8000/   （应返回 healthy）"
echo "  4) 前端（方案 A，用户手填 API 地址）："
echo "       docker run -d --name travel-front --restart unless-stopped -p 80:80 \\"
echo "         -v \$PWD/frontend:/usr/share/nginx/html:ro nginx:alpine"
echo "       浏览器打开 http://<服务器IP>/ ，在页面 API 地址输入框填 http://<服务器IP>:8000"
