"""
向量数据库管理
"""
from typing import List
from pathlib import Path
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_community.embeddings import DashScopeEmbeddings
import langchain_community.embeddings.dashscope as _ds_emb
from app.config import settings
from app.utils.logger import app_logger


class VectorStoreManager:
    """向量数据库管理器"""

    def __init__(
            self,
            persist_directory: str = None,
            collection_name: str = "travel_guides"
    ):
        # 锚定到项目根目录（与 document_loader.py 一致），避免相对路径随运行时的 CWD 漂移
        project_root = Path(__file__).parent.parent.parent
        if persist_directory is None:
            persist_directory = str(project_root / "data" / "vectorstore")
        self.persist_directory = Path(persist_directory)
        self.collection_name = collection_name

        # 创建目录
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # 注册 qwen3.7-text-embedding 的批大小（该模型要求 ≤20 条/批，
        # 库默认 25 会触发 InvalidParameter，故显式设为 20）
        _ds_emb.BATCH_SIZE.setdefault("qwen3.7-text-embedding", 20)

        # 初始化 Embedding 模型
        self.embeddings = DashScopeEmbeddings(
            model="qwen3.7-text-embedding",
            dashscope_api_key=settings.dashscope_api_key
        )

        # 初始化向量数据库
        self.vectorstore = None

    def create_vectorstore(
            self,
            documents: List[Document]
    ) -> Chroma:
        """创建向量数据库"""

        app_logger.info(f"创建向量数据库（{len(documents)} 个文档）...")

        self.vectorstore = Chroma.from_documents(
            documents=documents,
            embedding=self.embeddings,
            persist_directory=str(self.persist_directory),
            collection_name=self.collection_name
        )

        app_logger.info("✅ 向量数据库创建完成")

        return self.vectorstore

    def load_vectorstore(self) -> Chroma:
        """加载已有向量数据库"""

        app_logger.info("加载向量数据库...")

        self.vectorstore = Chroma(
            persist_directory=str(self.persist_directory),
            embedding_function=self.embeddings,
            collection_name=self.collection_name
        )

        app_logger.info("✅ 向量数据库加载完成")

        return self.vectorstore

    def get_vectorstore(self) -> Chroma:
        """获取向量数据库实例"""
        if self.vectorstore is None:
            try:
                return self.load_vectorstore()
            except:
                app_logger.warning("⚠️ 向量数据库不存在，需先创建")
                raise RuntimeError("向量数据库未初始化")
        return self.vectorstore