from pydantic import ConfigDict
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Embedding（默认 bge-m3 中英多语言 / dim=1024）
    embedding_provider: str = "local"  # "local" = sentence-transformers；"http" = 远端 Infinity 服务
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    # "http" 模式下的远端服务地址（Infinity，OpenAI 兼容 /embeddings）。
    embedding_api_url: str = "http://localhost:7997"

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 55432
    postgres_user: str = "docqa"
    postgres_password: str = "docqa"
    postgres_db: str = "docqa"

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_env: str = "development"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # File Storage
    upload_dir: str = "./uploads"
    max_file_size: int = 10 * 1024 * 1024  # 10MB

    # 封面图片存储（#47）：与小说正文共享 upload 根，但单独子目录。
    # 默认 5MB，单本封面预期不超过 2-3 MB。
    cover_dir: str = "./uploads/covers"
    cover_max_size: int = 5 * 1024 * 1024  # 5MB

    # Document Processing
    chunk_size: int = 500
    chunk_overlap: int = 50

    # Hybrid retrieval（#66/#81）：六路检索策略开关（默认全开；entity/event
    # 索引不可用时检索器自动降级为空结果，graph 图数据为空同样静默空结果，
    # 均不阻断整体问答）。
    retrieval_dense_enabled: bool = True
    retrieval_bm25_enabled: bool = True
    retrieval_entity_enabled: bool = True
    retrieval_event_enabled: bool = True
    retrieval_chapter_enabled: bool = True
    retrieval_graph_enabled: bool = True
    # 各路检索 top_k；融合后取 top-N 进入证据包。
    retrieval_top_k: int = 5
    retrieval_fused_top_n: int = 5
    # 证据循环补充检索轮次上限（PRD 默认 2）。
    evidence_max_iterations: int = 2
    # 证据循环补检的每轮命中增量（每次补充检索合并进证据包的最大条数）。
    evidence_refine_top_n: int = 5

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def sync_database_url(self) -> str:
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()