# ========== 多阶段构建 ==========
# 阶段1：构建依赖（用 uv 从 pyproject.toml + uv.lock 导出并装到独立 venv）
FROM python:3.13-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv
# 构建阶段需要编译器：chroma-hnswlib 等要现场编译 C/C++ 扩展
# 先换阿里云 Debian 镜像，否则从 deb.debian.org 拉包极慢
RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends g++ gcc python3-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev > requirements.lock.txt \
 && uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache-dir --index-url https://mirrors.aliyun.com/pypi/simple/ -r requirements.lock.txt

# ========== 运行时镜像 ==========
FROM python:3.13-slim
WORKDIR /app
# 系统依赖：gcc/libpq-dev 用于 psycopg 编译或二进制；curl 用于健康检查
# 同样先换阿里云 Debian 镜像
RUN sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null; \
    apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev curl && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /app/logs

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
# 复制应用代码（data 不在镜像里，由 docker-compose 挂载 ./data:/app/data 提供，避免构建上下文过大）
COPY app /app/app
COPY scripts /app/scripts
# 前端静态页面：由 FastAPI 挂载到 /app 路径，别人可通过 http://<IP>:8000/app/ 直接访问
COPY frontend /app/frontend
# config.py 直接读 /app/.env，必须拷进去
COPY .env /app/.env

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV APP_PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1
CMD uvicorn app.main:app --host 0.0.0.0 --port 8000
