import asyncio
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.config import get_settings
from app.core.database import init_db, get_session_maker
from app.api.router import router
from app.services.documents import (
    process_document_index,
    recover_stale_processing_documents,
)
from app.services import models as models_service
from app.services.runtime_config import reset_runtime_model

settings = get_settings()
logger = logging.getLogger(__name__)

# #63：后台索引任务句柄集合——持有引用防止任务被 GC；完成后自动移除。
_background_index_tasks: set = set()


def _spawn_index_task(document_id: int) -> None:
    """在事件循环中启动后台索引任务并跟踪其句柄。"""
    task = asyncio.create_task(process_document_index(document_id))
    _background_index_tasks.add(task)
    task.add_done_callback(_background_index_tasks.discard)


async def _recover_indexing() -> None:
    """启动恢复（#63）：``processing`` 重置为 ``pending`` 并重新入队。

    同时把全部 ``pending`` 小说（含重启前上传后未及处理的新记录）重新入队，
    不留死状态。
    """
    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            pending_ids = await recover_stale_processing_documents(db)
    except Exception as exc:
        logger.warning("document index recovery failed: %s", exc)
        return
    for doc_id in pending_ids:
        _spawn_index_task(doc_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # #70：``llm_models`` 是 LLM 配置唯一事实源（旧 settings 单行表与
    # 迁移逻辑已删除）；启动时把默认模型行加载进运行时单例供 provider
    # 构造与 preflight 读取。加载失败不回退到环境变量，显式置为未配置。
    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            await models_service.sync_runtime_model_from_db(db)
    except Exception as exc:
        reset_runtime_model()
        logger.warning("LLM settings load failed, running unconfigured: %s", exc)
    # #63：重启后残留 processing 重置为 pending 并重新入队处理。
    await _recover_indexing()
    yield


app = FastAPI(
    title="DocQA API",
    description="文档问答助手 API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(router)
