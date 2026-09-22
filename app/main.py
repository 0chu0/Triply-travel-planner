"""
FastAPI 应用入口
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.utils.logger import app_logger
from app.api.v1 import conversations, chat, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    import asyncio

    # 打印当前事件循环信息（调试用）
    loop = asyncio.get_running_loop()
    app_logger.info(f"FastAPI 使用的事件循环: {type(loop).__name__}")

    from app.core.checkpointer import checkpointer_lifespan
    from app.mcp_core.client import MCPClientManager
    from app.core.store import store_lifespan

    app_logger.info("🚀 启动应用...")

    async with checkpointer_lifespan():
        app_logger.info("✅ Checkpointer 已就绪")

        # 2. 启动 Store (在这里加入)
        async with store_lifespan():
            app_logger.info("✅ Store 已就绪")

            # 3. 初始化 MCP
            mcp = await MCPClientManager.get_instance()
            app_logger.info("✅ MCP 服务初始化成功")

            # 4. 种子初始管理员账号（关闭开放注册后保证可登录）
            try:
                from app.api.v1.users import bootstrap_admin
                await bootstrap_admin()
            except Exception as e:
                app_logger.warning(f"⚠️ 种子管理员初始化跳过: {e}")

            yield

            # --- 关闭流程 (自动按相反顺序执行 context manager 的 exit，这里手动关闭 MCP) ---

            await mcp.close()
            app_logger.info("MCP 服务已关闭")

        # store_lifespan 的 exit 会在这里自动执行

    # checkpointer_lifespan 的 exit 会在这里自动执行

    app_logger.info("应用已关闭")


app = FastAPI(
    title="LangGraph 旅行规划系统",
    description="企业级多 Agent 旅行规划服务",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "LangGraph Travel Planner",
        "version": "1.0.0",
        "docs": "/docs",
        "frontend": "/app/",
    }


# ============== 前端静态页面托管 ==============
# 把 frontend/ 目录挂到 /app，这样别人（如面试官）可以直接用浏览器打开：
#     http://<服务器IP>:8000/app/
# 注意：必须放在所有 API 路由注册之后。目录不存在时自动跳过，不影响 API。
_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend",
)

if os.path.isdir(_FRONTEND_DIR):
    app.mount(
        "/app",
        StaticFiles(directory=_FRONTEND_DIR, html=True),
        name="frontend",
    )
    app_logger.info(f"✅ 前端页面已挂载: /app  ->  {_FRONTEND_DIR}")
else:
    app_logger.warning(f"⚠️ 未找到前端目录，跳过静态挂载: {_FRONTEND_DIR}")