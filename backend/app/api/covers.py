"""封面静态资源子路由（#47）。

仅暴露 ``cover_dir`` 下的图片文件，扩展名白名单 + 路径穿越防护。
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.services.documents import ALLOWED_COVER_EXTS

router = APIRouter()


def _resolve_cover_path(filename: str) -> Path | None:
    """校验扩展名与路径归属后返回安全路径；不合法返回 None。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_COVER_EXTS:
        return None

    # 惰性读取 settings，便于测试 monkeypatch cover_dir。
    cover_dir = Path(get_settings().cover_dir).resolve()
    target = (cover_dir / filename).resolve()
    # 解析后必须仍位于 cover_dir 内，拦截 ``..`` 穿越与绝对路径注入。
    if cover_dir != target and cover_dir not in target.parents:
        return None
    if not target.is_file():
        return None
    return target


@router.get("/{filename}")
async def get_cover(filename: str):
    """返回封面图片；白名单外 / 穿越 / 不存在一律 404。"""
    target = _resolve_cover_path(filename)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")

    ext = target.suffix.lstrip(".").lower()
    return FileResponse(str(target), media_type=ALLOWED_COVER_EXTS[ext])
