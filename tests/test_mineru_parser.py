"""MinerU 解析器测试：Docker 调用、输出映射与失败处理。"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import mineru_parser


@pytest.fixture(autouse=True)
def reset_status_cache(monkeypatch):
    monkeypatch.setattr(mineru_parser, "_status_cache", None)


def test_mineru_status_reports_missing_docker(monkeypatch) -> None:
    monkeypatch.setattr(mineru_parser.shutil, "which", lambda _name: None)

    status = mineru_parser.mineru_status(refresh=True)

    assert status["available"] is False
    assert "docker" in status["reason"]


def test_mineru_status_reports_missing_image(monkeypatch) -> None:
    monkeypatch.setattr(mineru_parser.shutil, "which", lambda _name: "docker")
    monkeypatch.setattr(
        mineru_parser.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error: No such image: asktofit-mineru:cpu",
        ),
    )

    status = mineru_parser.mineru_status(refresh=True)

    assert status["available"] is False
    assert "No such image" in status["reason"]
    assert "docker build" in status["reason"]


def test_content_list_maps_headings_pages_and_sections() -> None:
    items = [
        {"type": "text", "text": "售后政策", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "退换货规则", "text_level": 2, "page_idx": 0},
        {"type": "text", "text": "七天无理由退货。", "page_idx": 0},
        {"type": "text", "text": "运费规则", "text_level": 2, "page_idx": 1},
        {"type": "text", "text": "质量问题由商家承担运费。", "page_idx": 1},
    ]

    document = mineru_parser.content_list_to_document(items, "原始文件名", Path("."))

    assert document.title == "售后政策"
    assert [block.text for block in document.blocks] == [
        "七天无理由退货。",
        "质量问题由商家承担运费。",
    ]
    assert document.blocks[0].section == "退换货规则"
    assert document.blocks[0].page == 1
    assert document.blocks[1].section == "运费规则"
    assert document.blocks[1].page == 2


def test_table_html_is_converted_to_rows() -> None:
    html = (
        "<table><tr><td>尺码</td><td>胸围</td></tr>"
        "<tr><td>S</td><td>88cm</td></tr></table>"
    )
    items = [
        {
            "type": "table",
            "page_idx": 2,
            "table_body": html,
            "table_caption": ["尺码对照表"],
            "table_footnote": ["手工测量存在误差"],
        }
    ]

    document = mineru_parser.content_list_to_document(items, "尺码指南", Path("."))
    text = document.blocks[0].text

    assert "表格标题：尺码对照表" in text
    assert "尺码\t胸围" in text
    assert "S\t88cm" in text
    assert "表格说明：手工测量存在误差" in text
    assert document.blocks[0].page == 3


def test_image_caption_and_ocr_are_merged(tmp_path, monkeypatch) -> None:
    image = tmp_path / "images" / "size.png"
    image.parent.mkdir()
    image.write_bytes(b"fake-image")
    monkeypatch.setattr(
        mineru_parser, "ocr_image_bytes", lambda data, seen=None: "M 码 胸围 96cm"
    )
    items = [
        {
            "type": "image",
            "page_idx": 0,
            "img_path": "images/size.png",
            "img_caption": ["尺码示意图"],
        }
    ]

    document = mineru_parser.content_list_to_document(items, "尺码指南", tmp_path)
    text = document.blocks[0].text

    assert "图片说明：尺码示意图" in text
    assert "M 码 胸围 96cm" in text


def test_v2_content_list_is_flattened(tmp_path) -> None:
    payload = [
        {
            "page_idx": 0,
            "blocks": [
                {"type": "text", "text": "第一页正文"},
                {"type": "text", "text": "第二段", "text_level": 2},
            ],
        }
    ]
    path = tmp_path / "sample_content_list_v2.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    items = mineru_parser._load_content_list(path)

    assert [item["text"] for item in items] == ["第一页正文", "第二段"]
    assert all(item["page_idx"] == 0 for item in items)


def test_parse_with_mineru_maps_container_output(tmp_path, monkeypatch) -> None:
    source = tmp_path / "policy.pdf"
    source.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(
        mineru_parser,
        "mineru_status",
        lambda **kwargs: {
            "available": True,
            "image": "asktofit-mineru:cpu",
            "reason": None,
        },
    )

    def fake_run(input_path: Path, output_dir: Path) -> None:
        result_dir = output_dir / "source" / "auto"
        result_dir.mkdir(parents=True)
        items = [
            {"type": "text", "text": "深度解析正文", "page_idx": 0},
        ]
        (result_dir / "source_content_list.json").write_text(
            json.dumps(items), encoding="utf-8"
        )

    monkeypatch.setattr(mineru_parser, "_run_container", fake_run)

    document = mineru_parser.parse_with_mineru(source, "pdf", "政策文档")

    assert document.title == "政策文档"
    assert [block.text for block in document.blocks] == ["深度解析正文"]


def test_parse_with_mineru_rejects_unsupported_type(tmp_path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# notes", encoding="utf-8")

    with pytest.raises(mineru_parser.DocumentParseError, match="暂不支持"):
        mineru_parser.parse_with_mineru(source, "md")


def test_parse_with_mineru_reports_unavailable(monkeypatch, tmp_path) -> None:
    source = tmp_path / "policy.pdf"
    source.write_bytes(b"%PDF-fake")
    monkeypatch.setattr(
        mineru_parser,
        "mineru_status",
        lambda **kwargs: {
            "available": False,
            "image": "asktofit-mineru:cpu",
            "reason": "Docker 未启动",
        },
    )

    with pytest.raises(mineru_parser.DocumentParseError, match="Docker 未启动"):
        mineru_parser.parse_with_mineru(source, "pdf")


def test_container_timeout_cleans_up(monkeypatch, tmp_path) -> None:
    removed: list[str] = []
    monkeypatch.setattr(mineru_parser, "_remove_container", removed.append)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker run", timeout=1)

    monkeypatch.setattr(mineru_parser.subprocess, "run", raise_timeout)

    with pytest.raises(mineru_parser.DocumentParseError, match="超时"):
        mineru_parser._run_container(tmp_path / "input.pdf", tmp_path / "out")

    assert len(removed) == 1
    assert removed[0].startswith("asktofit-mineru-")


def test_missing_content_list_is_reported(tmp_path) -> None:
    with pytest.raises(mineru_parser.DocumentParseError, match="content_list"):
        mineru_parser._find_content_list(tmp_path)
