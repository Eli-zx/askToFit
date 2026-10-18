"""MinerU 深度解析：通过 Docker 容器解析复杂版式文档。

宿主只负责挂载输入文件、限制资源并读取 ``*_content_list.json``；
排版分析、表格识别与扫描页 OCR 都在容器内完成。解析结果会转换成系统
统一的 :class:`ParsedDocument`，后续指纹判重、父子切块与向量化完全复用。

容器不可用时抛 :class:`DocumentParseError`，由入库流水线标记为失败，
不做静默降级，避免用户以为开启了深度解析却实际走了轻量解析。
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.logger import get_logger
from app.services.parsers import (
    DocumentParseError,
    ParsedBlock,
    ParsedDocument,
    clean_text,
    ocr_image_bytes,
)
from config import Config

logger = get_logger(__name__)

MINERU_SUPPORTED_EXTENSIONS = frozenset(
    {"pdf", "ppt", "pptx", "doc", "docx", "png", "jpg", "jpeg"}
)

_STATUS_TTL_SECONDS = 30.0
_status_lock = threading.Lock()
_status_cache: tuple[float, dict[str, Any]] | None = None
_run_semaphore = threading.Semaphore(max(1, Config.mineru_max_concurrency))

_TABLE_CELL_END = re.compile(r"(?i)</t[dh]\s*>")
_TABLE_ROW_END = re.compile(r"(?i)</tr\s*>")
_TABLE_BREAK = re.compile(r"(?i)<br\s*/?>")
_TAG = re.compile(r"<[^>]+>")
_INLINE_SPACE = re.compile(r" +")
_TRAILING_SPACE = re.compile(r"[ \t]+$")
_IMAGE_LABEL = "图片文字："


def mineru_supported(file_type: str) -> bool:
    return file_type.lower().lstrip(".") in MINERU_SUPPORTED_EXTENSIONS


def mineru_status(*, refresh: bool = False) -> dict[str, Any]:
    """检查 Docker 与镜像是否可用（结果缓存 30 秒）。"""
    global _status_cache

    now = time.monotonic()
    with _status_lock:
        if (
            not refresh
            and _status_cache is not None
            and now - _status_cache[0] < _STATUS_TTL_SECONDS
        ):
            return dict(_status_cache[1])

    image = Config.mineru_image
    docker = shutil.which("docker")
    if not docker:
        status: dict[str, Any] = {
            "available": False,
            "image": image,
            "reason": "未检测到 docker 命令，请先安装并启动 Docker Desktop",
        }
    else:
        try:
            proc = subprocess.run(
                [docker, "image", "inspect", image, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            status = {
                "available": False,
                "image": image,
                "reason": "Docker 响应超时，请确认 Docker 已启动",
            }
        except OSError as exc:
            status = {
                "available": False,
                "image": image,
                "reason": f"无法调用 Docker：{exc}",
            }
        else:
            if proc.returncode == 0 and proc.stdout.strip():
                status = {"available": True, "image": image, "reason": None}
            else:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                hint = detail[0][:200] if detail else "未知错误"
                status = {
                    "available": False,
                    "image": image,
                    "reason": (
                        f"MinerU 镜像 {image} 不可用（{hint}）。"
                        f"请先构建：docker build -t {image} docker/mineru"
                    ),
                }

    with _status_lock:
        _status_cache = (time.monotonic(), status)
    return dict(status)


def parse_with_mineru(
    path: str | Path, file_type: str, title_hint: str | None = None
) -> ParsedDocument:
    """调用 MinerU 容器解析文档并转换为统一的块结构。"""
    path = Path(path)
    if not path.exists():
        raise DocumentParseError("文件不存在")
    extension = file_type.lower().lstrip(".")
    if extension not in MINERU_SUPPORTED_EXTENSIONS:
        raise DocumentParseError(
            f"MinerU 暂不支持 {extension or '未知'} 格式，"
            "请改用标准解析或转换为 PDF"
        )

    status = mineru_status()
    if not status["available"]:
        raise DocumentParseError(status["reason"] or "MinerU 未就绪")

    started = time.perf_counter()
    # Windows 上容器进程可能短暂占用输出文件，忽略清理失败避免掩盖真实解析结果
    with tempfile.TemporaryDirectory(
        prefix="asktofit-mineru-", ignore_cleanup_errors=True
    ) as temp_dir:
        root = Path(temp_dir)
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir()
        output_dir.mkdir()

        input_path = input_dir / f"source.{extension}"
        shutil.copyfile(path, input_path)

        with _run_semaphore:
            _run_container(input_path, output_dir)

        content_list_path = _find_content_list(output_dir)
        items = _load_content_list(content_list_path)
        document = content_list_to_document(
            items,
            title_hint or path.stem,
            content_list_path.parent,
        )

    document.blocks = [block for block in document.blocks if block.text.strip()]
    if not document.blocks:
        raise DocumentParseError(
            "MinerU 未提取到有效文本，请检查文件是否加密或内容为空"
        )

    logger.info(
        "MinerU 解析完成 file=%s blocks=%d elapsed=%.1fs",
        path.name,
        len(document.blocks),
        time.perf_counter() - started,
    )
    return document


def _run_container(input_path: Path, output_dir: Path) -> None:
    container_name = f"asktofit-mineru-{uuid.uuid4().hex[:10]}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--memory",
        Config.mineru_memory,
        "-v",
        f"{input_path.parent.resolve()}:/input:ro",
        "-v",
        f"{output_dir.resolve()}:/output",
        Config.mineru_image,
        "mineru",
        "-p",
        f"/input/{input_path.name}",
        "-o",
        "/output",
        "-b",
        "pipeline",
        "-l",
        "ch",
        "-d",
        "cpu",
    ]
    logger.info(
        "启动 MinerU 容器 image=%s file=%s timeout=%ss",
        Config.mineru_image,
        input_path.name,
        Config.mineru_timeout_seconds,
    )
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=Config.mineru_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # 超时只杀了 docker CLI，容器仍在运行，必须显式清理
        _remove_container(container_name)
        raise DocumentParseError(
            f"MinerU 解析超时（{Config.mineru_timeout_seconds} 秒），"
            "可拆分文档或调大 MINERU_TIMEOUT_SECONDS 后重试"
        ) from None
    except OSError as exc:
        raise DocumentParseError(f"MinerU 容器启动失败：{exc}") from exc

    if proc.returncode != 0:
        output = (proc.stderr or proc.stdout or "").strip()
        detail = output.splitlines()[-1][:300] if output else "未知错误"
        raise DocumentParseError(f"MinerU 解析失败：{detail}")


def _remove_container(name: str) -> None:
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("清理超时的 MinerU 容器失败 name=%s", name, exc_info=True)


def _find_content_list(output_dir: Path) -> Path:
    """优先使用扁平结构的 v1 content_list，兼容 v2 文件。"""
    candidates = sorted(
        output_dir.rglob("*_content_list.json"),
        key=lambda item: item.stat().st_size,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    v2_candidates = sorted(
        output_dir.rglob("*_content_list_v2.json"),
        key=lambda item: item.stat().st_size,
        reverse=True,
    )
    if v2_candidates:
        return v2_candidates[0]

    raise DocumentParseError(
        "MinerU 未生成 content_list.json，可能是文件格式损坏或模型加载失败"
    )


def _load_content_list(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentParseError(f"MinerU 输出解析失败：{exc}") from exc

    if isinstance(payload, list):
        items: list[Any] = payload
    elif isinstance(payload, dict):
        items = payload.get("content") or payload.get("items") or []
    else:
        items = []

    if path.name.endswith("_content_list_v2.json"):
        items = _flatten_v2(items)

    return [item for item in items if isinstance(item, dict)]


def _flatten_v2(items: list[Any]) -> list[dict[str, Any]]:
    """v2 按页分组，这里拍平成与 v1 一致的块序列。"""
    flattened: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "type" in item:
            flattened.append(item)
            continue
        nested = item.get("blocks") or item.get("items") or item.get("content")
        if isinstance(nested, list):
            for child in nested:
                if not isinstance(child, dict):
                    continue
                child.setdefault("page_idx", item.get("page_idx"))
                flattened.append(child)
    return flattened


def content_list_to_document(
    items: list[dict[str, Any]], title_hint: str, base_dir: Path
) -> ParsedDocument:
    """把 MinerU content_list 映射为标题 / 章节 / 页码齐全的块。"""
    title = title_hint
    title_locked = False
    current_section: str | None = None
    blocks: list[ParsedBlock] = []
    seen_images: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        page = _page_number(item)
        kind = str(item.get("type") or "").lower()
        level = _heading_level(item)

        if kind == "text":
            text = clean_text(_text_value(item.get("text")))
            if not text:
                continue
            if level is not None:
                if level == 1 and not title_locked:
                    title = text
                    title_locked = True
                    current_section = None
                else:
                    current_section = text
                continue
            blocks.append(
                ParsedBlock(
                    text=text, title=title, section=current_section, page=page
                )
            )
            continue

        if kind == "table":
            text = _table_text(item, base_dir, seen_images)
            if text:
                blocks.append(
                    ParsedBlock(
                        text=text,
                        title=title,
                        section=current_section,
                        page=page,
                    )
                )
            continue

        if kind == "image":
            text = _image_text(item, base_dir, seen_images)
            if text:
                blocks.append(
                    ParsedBlock(
                        text=text,
                        title=title,
                        section=current_section,
                        page=page,
                    )
                )
            continue

        # equation / code / list 等类型统一取 text 字段兜底
        text = clean_text(_text_value(item.get("text")))
        if text:
            blocks.append(
                ParsedBlock(
                    text=text, title=title, section=current_section, page=page
                )
            )

    return ParsedDocument(title=title, blocks=blocks)


def _page_number(item: dict[str, Any]) -> int | None:
    value = item.get("page_idx")
    if value is None:
        return None
    try:
        return int(value) + 1
    except (TypeError, ValueError):
        return None


def _heading_level(item: dict[str, Any]) -> int | None:
    value = item.get("text_level")
    if value is None:
        return None
    try:
        level = int(value)
    except (TypeError, ValueError):
        return None
    return level if 1 <= level <= 6 else None


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if isinstance(item, str))
    return "" if value is None else str(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _table_text(
    item: dict[str, Any], base_dir: Path, seen_images: set[str]
) -> str:
    caption = _string_list(item.get("table_caption"))
    footnote = _string_list(item.get("table_footnote"))
    body = _html_table_text(_text_value(item.get("table_body")))
    if not body:
        body = _image_ocr(item.get("img_path"), base_dir, seen_images)

    parts: list[str] = []
    if caption:
        parts.append("表格标题：" + " ".join(caption))
    if body:
        parts.append(body)
    if footnote:
        parts.append("表格说明：" + " ".join(footnote))
    return clean_text("\n".join(parts))


def _image_text(
    item: dict[str, Any], base_dir: Path, seen_images: set[str]
) -> str:
    caption = _string_list(item.get("img_caption"))
    footnote = _string_list(item.get("img_footnote"))
    ocr_text = _image_ocr(item.get("img_path"), base_dir, seen_images)

    parts: list[str] = []
    if caption:
        parts.append("图片说明：" + " ".join(caption))
    if ocr_text:
        parts.append(f"{_IMAGE_LABEL}\n{ocr_text}")
    if footnote:
        parts.append("图片备注：" + " ".join(footnote))
    return clean_text("\n".join(parts))


def _image_ocr(
    img_path: Any, base_dir: Path, seen_images: set[str]
) -> str:
    if not img_path:
        return ""
    path = Path(str(img_path))
    candidates = [base_dir / path, base_dir / path.name, base_dir / "images" / path.name]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return ocr_image_bytes(candidate.read_bytes(), seen_images)
        except OSError:
            logger.debug("MinerU 图片读取失败 path=%s", candidate, exc_info=True)
            return ""
    return ""


def _html_table_text(value: str) -> str:
    """把 MinerU 的 table_body HTML 转成保留行列关系的纯文本。"""
    if not value:
        return ""
    text = _TABLE_BREAK.sub("\n", value)
    text = _TABLE_CELL_END.sub("\t", text)
    text = _TABLE_ROW_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)

    lines: list[str] = []
    for line in text.splitlines():
        line = _TRAILING_SPACE.sub("", _INLINE_SPACE.sub(" ", line))
        if line.strip():
            lines.append(line)
    return "\n".join(lines)
