"""上传文件落盘与安全校验。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from config import Config

_UNSAFE_NAME = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class UploadTooLargeError(Exception):
    pass


class UnsupportedFileTypeError(Exception):
    pass


@dataclass
class SavedFile:
    stored_name: str
    original_name: str
    file_type: str
    file_size: int
    path: Path


def _safe_original_name(filename: str | None) -> str:
    name = Path(filename or "").name.strip()
    name = _UNSAFE_NAME.sub("_", name)
    return name[:200] or "document"


async def save_upload(file: UploadFile, kb_id: int) -> SavedFile:
    original_name = _safe_original_name(file.filename)
    extension = Path(original_name).suffix.lower().lstrip(".")
    if extension not in Config.allowed_extensions:
        raise UnsupportedFileTypeError(
            f"不支持的文件类型：{extension or '未知'}，"
            f"支持 {', '.join(Config.allowed_extensions)}"
        )

    directory = Path(Config.upload_dir) / f"kb_{kb_id}"
    directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}.{extension}"
    target = directory / stored_name
    max_bytes = Config.max_upload_mb * 1024 * 1024
    size = 0

    try:
        with target.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(
                        f"文件超过大小限制 {Config.max_upload_mb}MB"
                    )
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if size == 0:
        target.unlink(missing_ok=True)
        raise ValueError("上传文件为空")

    return SavedFile(
        stored_name=stored_name,
        original_name=original_name,
        file_type=extension,
        file_size=size,
        path=target,
    )


def delete_stored_file(file_path: str | Path) -> None:
    """仅允许删除上传目录内的文件，防止路径穿越。"""
    if not file_path:
        return
    path = Path(file_path).resolve()
    root = Path(Config.upload_dir).resolve()
    if root == path or root not in path.parents:
        return
    path.unlink(missing_ok=True)
