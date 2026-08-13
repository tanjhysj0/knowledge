from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String
from app.core.database import Base


class AppSetting(Base):
    """LLM 配置持久化模型（#67）：单行表，``id=1`` 固定唯一行。

    DB 是 LLM 配置的唯一事实源；环境变量与 ``.env`` 中的 LLM key 不再
    作为运行时配置来源。API Key 明文存储（与旧 .env 方案一致），
    响应侧通过 ``mask_api_key`` 脱敏。
    """

    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    llm_provider = Column(String(20), nullable=False, default="openai")
    openai_api_key = Column(String(512), nullable=False, default="")
    openai_base_url = Column(String(512), nullable=False, default="")
    openai_model = Column(String(255), nullable=False, default="")
    anthropic_api_key = Column(String(512), nullable=False, default="")
    anthropic_base_url = Column(String(512), nullable=False, default="")
    anthropic_model = Column(String(255), nullable=False, default="")
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
