from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


_engine = None
_async_session_maker = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.app_env == "development",
            # 基础设施（Docker 端口转发等）可能静默切断空闲/负载中的 TCP 连接，
            # 且 PG 侧无感知；pool_pre_ping 在 checkout 前轻量探活，坏连接
            # 自动丢弃重连，避免随机 500「connection is closed」。
            pool_pre_ping=True,
            # 定期重建连接作为双保险，防止长生命周期进程持有过期连接。
            pool_recycle=3600,
        )
    return _engine


def get_session_maker():
    global _async_session_maker
    if _async_session_maker is None:
        _async_session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_maker


async def get_db() -> AsyncSession:
    session_maker = get_session_maker()
    async with session_maker() as session:
        yield session


async def init_db():
    try:
        engine = get_engine()
        # #66：混合检索辅助索引表随 create_all 建表（import 即注册到 metadata）。
        from app.models.retrieval_index import (  # noqa: F401
            Bm25Chunk,
            ChapterAnchor,
            EntityAnchor,
            EventAnchor,
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # #70：旧 settings 单行表已迁移入 llm_models（#68），启动时删除
            # 遗留物理表（幂等；存量数据库清理，新库无此表也不报错）。
            await conn.exec_driver_sql("DROP TABLE IF EXISTS settings")
            # 轻量内联迁移（#34）：生产环境为 PostgreSQL，
            # ``ADD COLUMN IF NOT EXISTS`` 允许表已存在时安全补齐。
            await conn.exec_driver_sql(
                "ALTER TABLE chat_messages "
                "ADD COLUMN IF NOT EXISTS conversation_id INTEGER"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id "
                "ON chat_messages (conversation_id)"
            )
            # #47：documents 表补齐封面路径列（nullable，存量记录保持空）。
            await conn.exec_driver_sql(
                "ALTER TABLE documents "
                "ADD COLUMN IF NOT EXISTS cover_image_path VARCHAR(512)"
            )
            # #53：documents 表补齐小说名列（nullable，存量记录回退文件名）。
            await conn.exec_driver_sql(
                "ALTER TABLE documents "
                "ADD COLUMN IF NOT EXISTS title VARCHAR(255)"
            )
            # #62：documents 表补齐处理状态与进度两列（NOT NULL + DEFAULT，
            # 存量行自动回填为 ready/100，与同步上传落库值一致）。
            await conn.exec_driver_sql(
                "ALTER TABLE documents "
                "ADD COLUMN IF NOT EXISTS status VARCHAR(20) "
                "NOT NULL DEFAULT 'ready'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE documents "
                "ADD COLUMN IF NOT EXISTS progress INTEGER "
                "NOT NULL DEFAULT 100"
            )
            # #63：documents 表补齐可空错误信息列（处理失败时写入原因）。
            await conn.exec_driver_sql(
                "ALTER TABLE documents "
                "ADD COLUMN IF NOT EXISTS error_message TEXT"
            )
            # #52：conversations 表补齐客户端隔离与小说绑定两列；
            # 存量会话 client_id 补齐为 'default'、document_id 保持 NULL。
            await conn.exec_driver_sql(
                "ALTER TABLE conversations "
                "ADD COLUMN IF NOT EXISTS client_id VARCHAR(64) "
                "NOT NULL DEFAULT 'default'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE conversations "
                "ADD COLUMN IF NOT EXISTS document_id INTEGER"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_conversations_client_id "
                "ON conversations (client_id)"
            )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_conversations_document_id "
                "ON conversations (document_id)"
            )
            # #52：绑定会话幂等的数据库级兜底——同一客户端下
            # (client_id, document_id) 唯一（并发下防重复绑定）。
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_client_document "
                "ON conversations (client_id, document_id) "
                "WHERE document_id IS NOT NULL"
            )
    except Exception as e:
        print(f"Database initialization skipped: {e}")
