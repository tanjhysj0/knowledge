"""文档路由层：仅做 HTTP 适配、依赖注入和服务调用。"""
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.schemas import DocumentResponse, PaginatedDocumentsResponse
from app.services.llm import is_e2e_mock_request
from app.services.documents import (
    ALLOWED_COVER_EXTS,
    CoverTooLargeError,
    CoverTypeError,
    DocumentNotFoundError,
    DocumentNotFailedError,
    DocumentTitleError,
)
from app.services import documents as document_service

router = APIRouter()
settings = get_settings()

_ALLOWED_FILE_TYPES = {"txt", "md", "pdf", "docx"}


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    cover: Optional[UploadFile] = File(None),
    title: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """上传小说正文（必填）与封面（可选，#48）。

    校验顺序（#48）：先正文 type/size → 读正文 → 再封面 ext/size → 读封面。
    封面非法返回 400，不落库、不写文件。

    #53：``title`` 为小说名（管理端表单必填）；API 层缺省时回退文件名去扩展名。

    #63：上传与索引分离——落库（pending/0）后立即返回，解析/分块/embedding/
    向量写入整体移入后台任务。

    #80：``X-E2E-Test`` 头在路由层解析为 ``e2e_mock`` 透传后台任务，
    使 E2E 下图谱抽取走 MockLLMProvider（确定性三元组）。
    """
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(status_code=400, detail="File too large")

    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in _ALLOWED_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_ext}")

    content = await file.read()

    # 封面校验：扩展名白名单 → size → 读内容后按实际长度复核（#48）。
    cover_content: Optional[bytes] = None
    cover_ext: Optional[str] = None
    if cover is not None:
        cover_ext = cover.filename.rsplit(".", 1)[-1].lower() if "." in cover.filename else ""
        if cover_ext not in ALLOWED_COVER_EXTS:
            raise HTTPException(
                status_code=400, detail=f"Unsupported cover type: {cover_ext}"
            )
        if cover.size and cover.size > settings.cover_max_size:
            raise HTTPException(status_code=400, detail="Cover too large")
        cover_content = await cover.read()
        if len(cover_content) > settings.cover_max_size:
            raise HTTPException(status_code=400, detail="Cover too large")

    try:
        document = await document_service.upload_document(
            filename=file.filename,
            file_ext=file_ext,
            content=content,
            db=db,
            cover_content=cover_content,
            cover_ext=cover_ext,
            title=title,
        )
    except CoverTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CoverTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # #63：索引处理移入后台任务；响应只等落库（秒级返回）。
    # #80：E2E mock 标志由请求头解析（后台任务无 request 上下文）。
    background_tasks.add_task(
        document_service.process_document_index,
        document.id,
        e2e_mock=is_e2e_mock_request(request),
    )
    return document


@router.get("", response_model=PaginatedDocumentsResponse)
async def list_documents(
    page: int = 1,
    page_size: int = 10,
    all_statuses: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """#63：前台书架默认仅返回 ``ready`` 小说；``all_statuses=true``
    时管理端获得全量视图（含 pending/processing/failed）。"""
    return await document_service.list_documents(
        db=db,
        page=page,
        page_size=page_size,
        all_statuses=all_statuses,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    """单文档详情：管理端编辑页按 id 拉取预填数据。不存在返回 404。"""
    try:
        return await document_service.get_document(db=db, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: int,
    title: Optional[str] = Form(None),
    cover: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """编辑小说（#53）：仅改小说名与换封面，正文不可换。

    校验顺序：先封面 ext/size → 读封面 → 服务层编辑（title 缺省表示不修改）。
    非法输入返回 400；文档不存在返回 404。
    """
    cover_content: Optional[bytes] = None
    cover_ext: Optional[str] = None
    if cover is not None:
        cover_ext = cover.filename.rsplit(".", 1)[-1].lower() if "." in cover.filename else ""
        if cover_ext not in ALLOWED_COVER_EXTS:
            raise HTTPException(
                status_code=400, detail=f"Unsupported cover type: {cover_ext}"
            )
        if cover.size and cover.size > settings.cover_max_size:
            raise HTTPException(status_code=400, detail="Cover too large")
        cover_content = await cover.read()
        if len(cover_content) > settings.cover_max_size:
            raise HTTPException(status_code=400, detail="Cover too large")

    try:
        return await document_service.update_document(
            db=db,
            document_id=document_id,
            title=title,
            cover_content=cover_content,
            cover_ext=cover_ext,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentTitleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CoverTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CoverTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        await document_service.delete_document(db=db, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message": "Document deleted"}


@router.post("/{document_id}/reindex", response_model=DocumentResponse)
async def reindex_document(
    document_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """重试索引（#65）：failed 小说重置 pending 并重新入队后台处理。

    仅 failed 可重试（其余状态 409）；成功后经 BackgroundTasks 入队与
    首次上传相同的处理链路，进度照常写回小说表。

    #80：E2E mock 标志与上传端点一致，由请求头解析透传。
    """
    try:
        document = await document_service.requeue_document_index(
            db=db, document_id=document_id
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DocumentNotFailedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # #65：与首次上传共用同一后台处理链路。
    background_tasks.add_task(
        document_service.process_document_index,
        document.id,
        e2e_mock=is_e2e_mock_request(request),
    )
    return document
