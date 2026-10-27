"""
Embedding 客户端管理器

负责按配置初始化 Embedding 服务客户端，并为字段、指标和用户问题的向量化
提供统一访问入口
"""

import asyncio
from typing import Optional

import httpx

from app.conf.app_config import EmbeddingConfig, app_config


class EmbeddingClientManager:
    """管理 Embedding 服务客户端的初始化与复用"""

    def __init__(self, config: EmbeddingConfig):
        self.client: Optional[TEIEmbeddingClient] = None
        self.config = config

    def _get_url(self) -> str:
        """拼接 Embedding 服务地址"""
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        """显式初始化客户端，避免模块导入时立即建立外部连接"""
        # langchain-huggingface 新版本的 HuggingFaceEndpointEmbeddings 只接受
        # HuggingFace Hub repo id，不能再传本地 TEI 服务 URL。项目使用 Docker
        # 中的 Text Embeddings Inference 服务，因此直接调用其 HTTP API，避免
        # 依赖版本差异影响启动。
        self.client = TEIEmbeddingClient(self._get_url())


class TEIEmbeddingClient:
    """Text Embeddings Inference (TEI) HTTP 客户端，兼容 LangChain Embeddings API。"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        payload = {"inputs": [text.replace("\n", " ") for text in texts]}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{self.base_url}/embed", json=payload)
            response.raise_for_status()
            data = response.json()
        # TEI returns either a batch of vectors or a single vector depending on
        # server version and request size; normalize both forms.
        if data and isinstance(data[0], (int, float)):
            return [data]
        return data

    async def aembed_query(self, text: str) -> list[float]:
        return (await self.aembed_documents([text]))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return asyncio.run(self.aembed_documents(texts))

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


# 模块级单例，供整个项目复用同一套 Embedding 客户端管理器
embedding_client_manager = EmbeddingClientManager(app_config.embedding)


if __name__ == "__main__":
    embedding_client_manager.init()
    client = embedding_client_manager.client

    async def test():
        """执行一次最小化向量化调用，验证服务是否可用"""
        text = "What is deep learning?"
        query_result = await client.aembed_query(text)
        print(query_result[:3])

    asyncio.run(test())
