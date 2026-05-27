"""DreaminaVideoBackend — 即梦 Dreamina 视频生成后端。

通过 dreamina CLI (subprocess) 调用即梦 Seedance 2.0 API。
提交 → 轮询 → 下载，与 ViduVideoBackend 同模式。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from lib.providers import PROVIDER_DREAMINA
from lib.video_backends.base import (
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    poll_with_retry,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# dreamina 配置
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "seedance2.0fast"
_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 90.0
_DURATION_MIN = 4
_DURATION_MAX = 15
_SUPPORTED_RATIOS = frozenset({"1:1", "3:4", "16:9", "4:3", "9:16", "21:9"})


def _cli_path() -> str:
    """返回 dreamina CLI 路径，环境变量 `DREAMINA_CLI` 可覆盖。"""
    return os.environ.get("DREAMINA_CLI", "dreamina")


# ---------------------------------------------------------------------------
# CLI 输出解析
# ---------------------------------------------------------------------------
_SUBMIT_ID_RE = re.compile(r'"submit_id"\s*:\s*"([a-fA-F0-9-]+)"')
_GEN_STATUS_RE = re.compile(r'"gen_status"\s*:\s*"(\w+)"')
_FAIL_REASON_RE = re.compile(r'"fail_reason"\s*:\s*"([^"]*)"')


def _parse_submit_id(text: str) -> str | None:
    m = _SUBMIT_ID_RE.search(text)
    return m.group(1) if m else None


def _parse_gen_status(text: str) -> str | None:
    m = _GEN_STATUS_RE.search(text)
    return m.group(1) if m else None


def _parse_fail_reason(text: str) -> str | None:
    m = _FAIL_REASON_RE.search(text)
    if m and m.group(1):
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# dreamina CLI 异步封装
# ---------------------------------------------------------------------------


async def _run_dreamina(*args: str, timeout: float = 30) -> tuple[int, str, str]:
    """异步运行 dreamina CLI，返回 (returncode, stdout, stderr)。"""
    cmd = [_cli_path(), *args]
    logger.debug("dreamina CLI: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise

    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


# ---------------------------------------------------------------------------
# DreaminaVideoBackend
# ---------------------------------------------------------------------------


class DreaminaVideoBackend:
    """即梦 Dreamina 视频生成后端。

    通过 dreamina CLI 调用即梦 Seedance 2.0 API。
    需要预先 ``dreamina login`` 完成 OAuth 登录。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        # dreamina 走 OAuth 而非 API Key；参数保留以兼容工厂模式
        self._model = model or DEFAULT_MODEL

        self._capabilities: set[VideoCapability] = {
            VideoCapability.TEXT_TO_VIDEO,
            VideoCapability.IMAGE_TO_VIDEO,
        }

    # ── 协议属性 ──────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return PROVIDER_DREAMINA

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._capabilities

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return VideoCapabilities(
            first_frame=True,
            last_frame=False,
            reference_images=False,
            max_reference_images=0,
        )

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        raise NotImplementedError("DreaminaVideoBackend 暂不支持 resume_video")

    # ── 主流程 ────────────────────────────────────────────────────────

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """提交 → 轮询 → 下载 一条龙。"""

        # 1. 提交 — 按参考图数量选模式
        ref_images = [p for p in (request.reference_images or []) if p]
        if ref_images:
            submit_id = await self._submit_multimodal2video(request, ref_images)
        elif request.start_image is not None:
            submit_id = await self._submit_image2video(request)
        else:
            submit_id = await self._submit_text2video(request)

        # 2. 轮询至完成
        duration = self._coerce_duration(request.duration_seconds)
        max_wait = max(_MIN_POLL_TIMEOUT_SECONDS, float(duration) * _POLL_TIMEOUT_PER_SECOND)

        self._last_raw = ""

        await poll_with_retry(
            poll_fn=lambda: self._poll_task(submit_id),
            is_done=lambda status: status == "success",
            is_failed=lambda status: (
                f"Dreamina 任务失败: {_parse_fail_reason(self._last_raw) or '未知原因'}"
                if status == "fail"
                else None
            ),
            poll_interval=_POLL_INTERVAL_SECONDS,
            max_wait=max_wait,
            label="Dreamina",
            on_progress=lambda status, elapsed: logger.info(
                "Dreamina 视频生成中... submit_id=%s status=%s elapsed=%ds",
                submit_id, status, int(elapsed),
            ),
        )

        # 3. 下载到临时目录，然后移到目标路径
        output_dir = request.output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        await self._download_result(submit_id, str(output_dir))
        downloaded = self._find_downloaded(output_dir, submit_id)
        if downloaded != request.output_path:
            await asyncio.to_thread(os.replace, downloaded, request.output_path)
        video_path = request.output_path

        logger.info("Dreamina 视频下载完成: %s", video_path)

        return VideoGenerationResult(
            video_path=video_path,
            provider=PROVIDER_DREAMINA,
            model=self._model,
            duration_seconds=duration,
            task_id=submit_id,
        )

    # ── 提交 ──────────────────────────────────────────────────────────

    async def _submit_text2video(self, request: VideoGenerationRequest) -> str:
        duration = self._coerce_duration(request.duration_seconds)
        ratio = request.aspect_ratio if request.aspect_ratio in _SUPPORTED_RATIOS else "9:16"

        rc, stdout, stderr = await _run_dreamina(
            "text2video",
            "--prompt", request.prompt,
            "--duration", str(duration),
            "--ratio", ratio,
            "--model_version", self._model,
            "--poll", "0",
        )

        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina text2video 提交失败: {error}")

        submit_id = _parse_submit_id(stdout) or _parse_submit_id(stderr)
        if not submit_id:
            raise RuntimeError(f"无法提取 submit_id: {stdout[:300]}")

        logger.info("Dreamina text2video 已提交 submit_id=%s duration=%ds ratio=%s model=%s",
                     submit_id, duration, ratio, self._model)
        return submit_id

    async def _submit_image2video(self, request: VideoGenerationRequest) -> str:
        start_image = str(request.start_image)
        duration = self._coerce_duration(request.duration_seconds)

        rc, stdout, stderr = await _run_dreamina(
            "image2video",
            "--image", start_image,
            "--prompt", request.prompt,
            "--duration", str(duration),
            "--model_version", self._model,
            "--poll", "0",
        )

        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina image2video 提交失败: {error}")

        submit_id = _parse_submit_id(stdout) or _parse_submit_id(stderr)
        if not submit_id:
            raise RuntimeError(f"无法提取 submit_id: {stdout[:300]}")

        logger.info("Dreamina image2video 已提交 submit_id=%s image=%s", submit_id, start_image)
        return submit_id

    async def _submit_multimodal2video(self, request: VideoGenerationRequest, ref_images: list[Path]) -> str:
        """参考生视频模式：将参考图作为 --image，音色作为 --audio 传给 multimodal2video。"""
        duration = self._coerce_duration(request.duration_seconds)
        ratio = request.aspect_ratio if request.aspect_ratio in _SUPPORTED_RATIOS else "9:16"

        # ArcReel 内部 [图N] → dreamina 期望的 图片N
        prompt = re.sub(r"\[图(\d+)\]", r"图片\1", request.prompt)
        # 去掉公共层追加的反向提示词（智能导演模式自带【禁止标签】，冲突）
        prompt = re.sub(r"\s*禁止出现：BGM、文字字幕、水印。?$", "", prompt)

        # 解析【音色】行：图片N 的音色参考 图片M → 图片N 的音色参考 音频M
        audio_files: list[Path] = []
        timbre_map: dict[str, str] = {}  # 图片M → 音频编号
        m = re.search(r"【音色】[：:]([^\]]*?)(?:\n|【|$)", prompt)
        if m:
            timbre_line = m.group(1)
            # 提取 图片N 的音色参考 图片M 模式
            for tm in re.finditer(r"图片(\d+)\s*的\s*音色参考\s*图片(\d+)", timbre_line):
                timbre_img = f"图片{tm.group(2)}"
                audio_idx = len(audio_files) + 1
                timbre_map[timbre_img] = f"音频{audio_idx}"
                # 从 ref_images 找对应音频文件
                ref_idx = int(tm.group(2)) - 1
                if ref_idx < len(ref_images):
                    # 尝试从 project 查找 timbre 音频文件
                    audio_path = self._resolve_timbre_audio(request, ref_images[ref_idx])
                    if audio_path:
                        audio_files.append(audio_path)
            # prompt 中替换：图片M → 音频N（在【音色】行内）
            for old, new in timbre_map.items():
                prompt = prompt.replace(old, new)

        # 构建 CLI 参数
        cmd_args = ["multimodal2video"]
        for img in ref_images:
            cmd_args.extend(["--image", str(img)])
        for af in audio_files:
            cmd_args.extend(["--audio", str(af)])
        cmd_args.extend([
            "--prompt", prompt,
            "--duration", str(duration),
            "--ratio", ratio,
            "--model_version", self._model,
            "--poll", "0",
        ])

        rc, stdout, stderr = await _run_dreamina(*cmd_args)

        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina multimodal2video 提交失败: {error}")

        submit_id = _parse_submit_id(stdout) or _parse_submit_id(stderr)
        if not submit_id:
            raise RuntimeError(f"无法提取 submit_id: {stdout[:300]}")

        logger.info("Dreamina multimodal2video 已提交 submit_id=%s images=%d",
                     submit_id, len(ref_images))
        return submit_id

    def _resolve_timbre_audio(self, request: VideoGenerationRequest, ref_image: Path) -> Path | None:
        """从参考图路径反查 timbre 音频文件。"""
        if not request.project_name:
            return None
        try:
            import json as _json
            pm_path = Path("/app/projects") / request.project_name / "project.json"
            if not pm_path.exists():
                return None
            with open(pm_path) as f:
                proj = _json.load(f)
            timbres = proj.get("timbres") or {}
            # 按文件名匹配：参考图的 stem 匹配 timbre 的 audio_file
            stem = ref_image.stem
            for name, info in timbres.items():
                audio_file = info.get("audio_file") if isinstance(info, dict) else None
                if audio_file and stem in audio_file:
                    audio_path = Path("/app/projects") / request.project_name / audio_file
                    if audio_path.exists():
                        return audio_path
        except Exception:
            pass
        return None

    # ── 轮询 / 下载 ───────────────────────────────────────────────────

    async def _poll_task(self, submit_id: str) -> str:
        """查询任务状态。将原始输出存入 ``self._last_raw`` 供 is_failed 解析。"""
        rc, stdout, stderr = await _run_dreamina("query_result", "--submit_id", submit_id)
        self._last_raw = stdout or stderr

        status = _parse_gen_status(self._last_raw)
        if status is None:
            logger.warning("无法解析 dreamina query_result: %s", self._last_raw[:200])
            return "querying"
        return status

    async def _download_result(self, submit_id: str, download_dir: str) -> None:
        rc, stdout, stderr = await _run_dreamina(
            "query_result", "--submit_id", submit_id, "--download_dir", download_dir,
            timeout=120,
        )
        if rc != 0:
            error = (stderr or stdout)[:500]
            raise RuntimeError(f"Dreamina 下载失败: {error}")

    def _find_downloaded(self, directory: Path, submit_id: str) -> Path:
        """按修改时间倒序查找下载的视频文件。"""
        files = sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)

        short_id = submit_id[:8]
        for f in files:
            if f.is_file() and short_id in f.name:
                return f

        # fallback: 最新的非目录文件
        for f in files:
            if f.is_file():
                return f

        raise FileNotFoundError(f"在 {directory} 中未找到 submit_id={submit_id} 的下载文件")

    def _coerce_duration(self, requested: int) -> int:
        return max(_DURATION_MIN, min(_DURATION_MAX, requested))
