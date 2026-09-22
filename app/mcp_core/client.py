"""
MCP 客户端管理器
统一管理所有 MCP 服务连接
"""
import asyncio
import os
from typing import Optional, List
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from app.utils.logger import app_logger

load_dotenv()


class MCPClientManager:
    """
    MCP 客户端管理器（单例模式）
    """

    _instance: Optional['MCPClientManager'] = None
    _client: Optional[MultiServerMCPClient] = None
    _tools: Optional[List] = None
    _lock = asyncio.Lock()

    # 项目根目录（用于 stdio 服务）
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))

    # 环境变量（追加 PYTHONPATH）
    ENV_VARS = os.environ.copy()
    ENV_VARS["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + ENV_VARS.get("PYTHONPATH", "")

    # 服务器配置
    SERVER_CONFIGS = {
        # ========== 自建服务（stdio） ==========
        "weather": {
            "command": "python",
            "args": ["-m", "app.mcp_core.servers.weather_server"],
            "transport": "stdio",
            "env": ENV_VARS,
        },
        "search": {
            "command": "python",
            "args": ["-m", "app.mcp_core.servers.search_server"],
            "transport": "stdio",
            "env": ENV_VARS,
        },

        # ========== 外部服务（HTTP） ==========
        "amap": {
            "url": f"https://mcp.amap.com/mcp?key={os.getenv('AMAP_API_KEY', '')}",
            "transport": "http",
        },
        "VariFlight-Aviation": {  # 航班服务（已验证可用；URL 不含尾斜杠，避免 307→HTTP 降级）
            "url": f"https://ai.variflight.com/servers/aviation/mcp?api_key={os.getenv('VARIFLIGHT_API_KEY', '')}",
            "transport": "streamable_http",
        },
        "aigohotel-mcp": {
            "url": "https://mcp.aigohotel.com/mcp",
            "transport": "streamable_http",
            "headers": {
                "Authorization": f"Bearer {os.getenv('AIGOHOTEL_MCP_API')}",
                "Content-Type": "application/json"
            }
        },
    }

    @classmethod
    async def get_instance(cls, servers: List[str] = None) -> 'MCPClientManager':
        """获取单例实例"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.initialize(servers=servers)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（用于测试）"""
        cls._instance = None

    async def initialize(self, servers: List[str] = None):
        """
        初始化 MCP 客户端

        Args:
            servers: 要启用的服务列表，默认启用所有
        """
        if self._client is not None:
            app_logger.warning("⚠️ MCP 客户端已初始化，跳过")
            return

        # 默认启用所有服务
        servers = servers or list(self.SERVER_CONFIGS.keys())
        configs = {k: v for k, v in self.SERVER_CONFIGS.items() if k in servers}

        app_logger.info(f"初始化 MCP: {list(configs.keys())}")

        # 创建客户端
        self._client = MultiServerMCPClient(configs)

        # 逐服务加载工具：单点失败不影响其他服务
        # （langchain_mcp_adapters 的 get_tools() 内部用 TaskGroup/gather 且无 return_exceptions，
        #  任一服务失败会整体抛错，导致全部工具丢失；故改为逐个加载并容错）
        all_tools = []
        for name in configs:
            try:
                tools = await self._client.get_tools(server_name=name)
                all_tools.extend(tools)
                app_logger.info(f"✅ {name}: 加载 {len(tools)} 个工具")
            except Exception as e:
                app_logger.warning(f"⚠️ 服务 {name} 加载失败（已跳过）: {e}")
        self._tools = all_tools
        app_logger.info(f"✅ 共加载 {len(self._tools)} 个 MCP 工具（来自 {len(configs)} 个服务）")

    async def close(self):
        """关闭客户端"""
        if self._client:
            self._client = None
            self._tools = None
            app_logger.info("MCP 客户端已关闭")

    #充当对外接口(懒加载策略)
    async def get_tools(self) -> List:
        """
        获取所有 MCP 工具

        Returns:
            LangChain 工具列表
        """
        if self._client is None:
            raise RuntimeError("MCP 客户端未初始化，请先调用 initialize()")

        # 如果已缓存，直接返回（None 表示尚未加载，空列表表示已加载但无工具）
        if self._tools is not None:
            return self._tools

        # 否则重新获取
        self._tools = await self._client.get_tools()
        return self._tools


async def get_mcp_client(servers: List[str] = None) -> MCPClientManager:
    """获取 MCP 客户端管理器实例"""
    return await MCPClientManager.get_instance(servers)