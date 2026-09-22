"""
日志配置模块
使用 loguru 提供增强日志功能
"""
import sys
from loguru import logger
from app.config import settings


def setup_logger():
    """配置日志系统"""

    # 移除默认处理器
    logger.remove()

    # 控制台日志（开发环境彩色输出）
    logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
        level="DEBUG" if settings.debug else "INFO"
    )

    # 文件日志（JSON 格式，便于日志分析）
    logger.add(
        "logs/app.log",
        rotation="500 MB",  # 日志轮转
        retention="10 days",  # 保留时间
        compression="zip",  # 压缩
        serialize=True,  # JSON 格式
        level="INFO"
    )

    # 错误日志单独记录
    logger.add(
        "logs/error.log",
        rotation="100 MB",
        retention="30 days",
        compression="zip",
        level="ERROR",
        backtrace=True,  # 记录异常堆栈
        diagnose=True  # 记录变量值
    )

    logger.info("✅ 日志系统初始化完成")
    return logger


# 导出配置好的 logger
app_logger = setup_logger()