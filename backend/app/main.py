import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.config import get_settings
from app.core.database import init_db, get_session_maker
from app.api.router import router
from app.services.settings import load_llm_settings_from_db, reset_llm_memory

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # #67：DB 是 LLM 配置唯一事实源，启动时恢复到内存单例；
    # 失败不影响启动，但也不回退到环境变量：显式置为未配置。
    try:
        session_maker = get_session_maker()
        async with session_maker() as db:
            await load_llm_settings_from_db(db)
    except Exception as exc:
        reset_llm_memory()
        logger.warning("LLM settings load failed, running unconfigured: %s", exc)
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
