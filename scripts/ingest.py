"""
Triply 知识库一键重建脚本
=======================
功能：删除旧向量库（data/vectorstore），并基于 data/documents/destinations/*.md
      重新切分 + 嵌入，生成全新的 Chroma 向量库。

复用了应用自身的构建链路（与 app/tools/rag_tools.py 中 _get_rag_pipeline 完全一致）：
    DocumentManager.load_destination_documents()
        -> AdvancedParentDocumentSplitter.split_documents()   # 得到 child_docs
        -> VectorStoreManager.create_vectorstore(child_docs)  # 用 qwen3.7-text-embedding 嵌入

⚠️ 关键点：向量库里存的是"切分后的子块(child_docs)"，不是整篇 .md。
          本脚本严格复用同一条切分+嵌入链路，保证与线上检索行为一致。

用法：
    本地（在项目根目录，且已激活含 langchain/dashscope 的 venv）：
        python scripts/ingest.py
    服务器（容器内，/app 为项目根）：
        docker compose exec backend python scripts/ingest.py

注意：
    - 需要 DASHSCOPE_API_KEY（.env 或环境变量）可用，否则 embedding 调用会失败。
    - 重建按 qwen3.7-text-embedding 计费（每批 ≤20 条），文档越多 API 调用越多。
    - 脚本只重建"稠密向量库"(Dense 腿)；BM25 词面索引由应用在启动时基于
      同一批 child_docs 在内存中重建，不持久化，无需本脚本处理。
    - 重建完成后，**运行中的 backend 进程内存里仍是旧 store**，需重启 backend
      才能热加载新库（本地重启后端进程；服务器 docker compose restart backend）。
"""

import os
import sys
import shutil
from pathlib import Path

# 1. 把项目根目录加入 sys.path，使 `import app` 可用（兼容任意 CWD）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 2. 加载 .env（与 pipeline.py 一致，确保 settings.dashscope_api_key 可读）
try:
    from dotenv import load_dotenv
    load_dotenv(str(PROJECT_ROOT / ".env"))
except Exception:
    pass

from app.rag.document_loader import DocumentManager
from app.rag.text_splitter import AdvancedParentDocumentSplitter
from app.rag.vectorstore import VectorStoreManager
from app.utils.logger import app_logger


def main():
    # 向量库持久化目录（与 VectorStoreManager 默认路径一致）
    vectorstore_dir = PROJECT_ROOT / "data" / "vectorstore"

    # ---- 步骤 0：扫描源文档，给出预期 ----
    docs_dir = PROJECT_ROOT / "data" / "documents" / "destinations"
    md_files = sorted(docs_dir.glob("**/*.md")) if docs_dir.exists() else []
    print(f"📂 源文档目录: {docs_dir}")
    print(f"📄 发现 {len(md_files)} 个 .md 文件，准备重建知识库...")

    # ---- 步骤 1：删除旧向量库（强制重建） ----
    if vectorstore_dir.exists():
        print(f" 删除旧向量库: {vectorstore_dir}")
        shutil.rmtree(vectorstore_dir, ignore_errors=True)
    else:
        print(" 未检测到旧向量库，将直接创建。")

    # ---- 步骤 2：加载并切分文档（与应用相同链路） ----
    doc_manager = DocumentManager()
    documents = doc_manager.load_destination_documents()
    if not documents:
        print(" 未发现任何目的地文档，已中止。请确认 data/documents/destinations/ 下有 .md 文件。")
        sys.exit(1)

    parent_splitter = AdvancedParentDocumentSplitter()
    parent_docs, child_docs = parent_splitter.split_documents(documents)
    print(f" 切分完成：{len(documents)} 篇文档 -> {len(child_docs)} 个子块（用于向量化）")

    # ---- 步骤 3：重建向量库（嵌入 child_docs） ----
    # 先构造管理器（会 mkdir 空目录 + 初始化 qwen3.7-text-embedding，batch_size=20）
    vs_manager = VectorStoreManager()
    print(f" 初始化向量库管理器，嵌入模型: qwen3.7-text-embedding (batch_size=20)")
    print(f" 开始嵌入并写入向量库: {vs_manager.persist_directory}")
    vs_manager.create_vectorstore(child_docs)

    # ---- 完成 ----
    print("✅ 知识库重建完成！")
    print(f"   向量库位置: {vs_manager.persist_directory}")
    print(f"   共嵌入 {len(child_docs)} 个子块（来自 {len(documents)} 篇文档）")
    print("⚠️  重要：请重启 backend 服务以热加载新库：")
    print("   本地：重启你的后端进程（或 Ctrl+C 后重新启动）")
    print("   服务器：docker compose restart backend")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        app_logger.exception("❌ 知识库重建失败")
        print(f"❌ 重建失败：{e}")
        sys.exit(1)
