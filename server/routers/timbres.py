"""音色管理路由（CRUD 由 _asset_router_factory 统一生成 + 自定义音频上传端点）。"""

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from lib.app_data_dir import app_data_dir
from lib.i18n import Translator
from lib.project_manager import ProjectManager
from server.auth import CurrentUser
from server.routers._asset_router_factory import build_asset_router

logger = logging.getLogger(__name__)
pm = ProjectManager(app_data_dir())
ALLOWED_AUDIO_EXTS = {".wav", ".mp3"}
MAX_AUDIO_BYTES = 5 * 1024 * 1024


def get_project_manager() -> ProjectManager:
    return pm


router = build_asset_router(asset_type="timbre", pm_getter=lambda: get_project_manager())


@router.post("/projects/{project_name}/timbres/{entry_name}/audio")
async def upload_timbre_audio(
    project_name: str,
    entry_name: str,
    _user: CurrentUser,
    _t: Translator,
    file: UploadFile = File(...),
):
    """上传音色参考音频文件。"""
    manager = get_project_manager()
    project = manager.load_project(project_name)
    timbres = project.get("timbres") or {}
    if entry_name not in timbres:
        raise HTTPException(status_code=404, detail=_t("timbre_not_found", name=entry_name))

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTS:
        raise HTTPException(status_code=415, detail=_t("asset_unsupported_format"))
    data = await file.read()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail=_t("asset_upload_too_large"))

    # 写入项目 timbres 目录
    root = manager.get_project_path(project_name) / "timbres"
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    target = root / f"{uuid.uuid4().hex}{ext}"
    await asyncio.to_thread(target.write_bytes, data)
    rel_path = str(target.relative_to(manager.get_project_path(project_name)))

    # 更新 project.json
    def _mutate(p: dict):
        entry = p.setdefault("timbres", {}).setdefault(entry_name, {})
        entry["audio_file"] = rel_path

    manager.update_project(project_name, _mutate)
    logger.info("timbre audio uploaded: %s/%s -> %s", project_name, entry_name, rel_path)
    return {"success": True, "audio_file": rel_path}
