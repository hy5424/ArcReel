"""DreaminaImageBackend — 即梦 Dreamina 图片生成后端。

通过 dreamina CLI (subprocess) 调用即梦 API。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from lib.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)
from lib.providers import PROVIDER_DREAMINA
from lib.video_backends.dreamina import (
    _parse_submit_id,
    _parse_gen_status,
    _parse_fail_reason,
    _run_dreamina,
)

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "4.5"
_DURATION_MIN = 4
_DURATION_MAX = 15
_POLL_INTERVAL_SECONDS = 3.0
_MIN_POLL_TIMEOUT_SECONDS = 300.0
_POLL_TIMEOUT_PER_SECOND = 60.0
_SUPPORTED_RATIOS = frozenset({"21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"})


class DreaminaImageBackend:
    """即梦 Dreamina 图片生成后端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self._model = model or DEFAULT_IMAGE_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return PROVIDER_DREAMINA

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._capabilities

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """提交 → 轮询 → 下载。"""

        # 1. 提交
        if request.reference_images:
            submit_id = await self._submit_image2image(request)
        else:
            submit_id = await self._submit_text2image(request)

        # 2. 轮询
        from lib.video_backends.base import poll_with_retry

        max_wait = max(_MIN_POLL_TIMEOUT_SECONDS, 30.0 * _POLL_TIMEOUT_PER_SECOND)
        self._last_raw = ""

        await poll_with_retry(
            poll_fn=lambda: self._poll_task(submit_id),
            is_done=lambda status: status == "success",
            is_failed=lambda status: (
                f"Dreamina 图片生成失败: {_parse_fail_reason(self._last_raw) or '未知原因'}"
                if status == "fail"
                else None
            ),
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=max_wait,
            label="Dreamina-Image",
            on_progress=lambda status, elapsed: logger.info(
                "Dreamina 图片生成中... submit_id=%s status=%s elapsed=%ds",
                submit_id, status, int(elapsed),
            ),
        )

        # 3. 下载
        output_dir = request.output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        await self._download_result(submit_id, str(output_dir))
        image_path = self._find_downloaded(output_dir, submit_id)

        logger.info("Dreamina 图片下载完成: %s", image_path)

        return ImageGenerationResult(
            image_path=image_path,
            provider=PROVIDER_DREAMINA,
            model=self._model,
        )

    async def _submit_text2image(self, request: ImageGenerationRequest) -> str:
        ratio = request.aspect_ratio if request.aspect_ratio in _SUPPORTED_RATIOS else "16:9"

        rc, stdout, stderr = await _run_dreamina(
            "text2image",
            "--prompt", request.prompt,
            "--ratio", ratio,
            "--model_version", self._model,
            "--poll", "0",
        )

        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina text2image 提交失败: {error}")

        submit_id = _parse_submit_id(stdout) or _parse_submit_id(stderr)
        if not submit_id:
            raise RuntimeError(f"无法提取 submit_id: {stdout[:300]}")

        logger.info("Dreamina text2image 已提交 submit_id=%s model=%s", submit_id, self._model)
        return submit_id

    async def _submit_image2image(self, request: ImageGenerationRequest) -> str:
        images = [ref.path for ref in request.reference_images if ref.path]
        if not images:
            raise ValueError("image2image 需要至少一张参考图")

        ratio = request.aspect_ratio if request.aspect_ratio in _SUPPORTED_RATIOS else "16:9"

        cmd = ["image2image", "--prompt", request.prompt, "--ratio", ratio,
               "--model_version", self._model, "--poll", "0"]
        for img in images:
            cmd.insert(1, img)
            cmd.insert(1, "--images")

        rc, stdout, stderr = await _run_dreamina(*cmd)

        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina image2image 提交失败: {error}")

        submit_id = _parse_submit_id(stdout) or _parse_submit_id(stderr)
        if not submit_id:
            raise RuntimeError(f"无法提取 submit_id: {stdout[:300]}")

        logger.info("Dreamina image2image 已提交 submit_id=%s", submit_id)
        return submit_id

    async def _poll_task(self, submit_id: str) -> str:
        rc, stdout, stderr = await _run_dreamina("query_result", "--submit_id", submit_id)
        self._last_raw = stdout or stderr
        status = _parse_gen_status(self._last_raw)
        return status if status else "querying"

    async def _download_result(self, submit_id: str, download_dir: str) -> None:
        rc, stdout, stderr = await _run_dreamina(
            "query_result", "--submit_id", submit_id, "--download_dir", download_dir,
            timeout=120,
        )
        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina 图片下载失败: {error}")

    def _find_downloaded(self, directory: Path, submit_id: str) -> Path:
        files = sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        short_id = submit_id[:8]
        for f in files:
            if f.is_file() and short_id in f.name:
                return f
        for f in files:
            if f.is_file():
                return f
        raise FileNotFoundError(f"在 {directory} 中未找到 submit_id={submit_id} 的下载文件")
