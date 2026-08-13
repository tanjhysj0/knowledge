from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, text
from app.core.database import Base


class LLMModel(Base):
    """LLM 模型配置（#68）：列表化，一个模型一条记录。

    「有且只有一个默认」由 partial unique index 保证：仅 ``is_default=true``
    的行参与唯一约束，数据库层杜绝并发写出的第二条默认记录。
    API Key 每条记录独立明文保存（与旧 settings 单行方案一致），
    响应侧经 ``mask_api_key`` 脱敏。
    """

    __tablename__ = "llm_models"

    __table_args__ = (
        Index(
            "uq_llm_models_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    # 接口类型：openai / anthropic。
    provider_type = Column(String(20), nullable=False)
    base_url = Column(String(512), nullable=False, default="")
    model_name = Column(String(255), nullable=False, default="")
    api_key = Column(String(512), nullable=False, default="")
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
