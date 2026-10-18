"""端到端验证：真实检索 + 重排序 + 流式大模型问答。

前置条件：后端已启动（默认 http://127.0.0.1:8000），MySQL / Redis / Milvus 正常，
演示账号与知识库已通过 scripts/verify_ingestion.py 初始化。

用法：
    uv run python scripts/verify_chat.py
    uv run python scripts/verify_chat.py --base-url http://127.0.0.1:8001
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from app.services.captcha import CAPTCHA_KEY_PREFIX  # noqa: E402
from config import Config  # noqa: E402

DEFAULT_USERNAME = "demo"
DEFAULT_PASSWORD = "demo1234"
DEFAULT_KB_NAME = "服装售前知识库"
DEFAULT_QUESTIONS = ["满多少包邮？", "那偏远地区呢？", "开发票要多久？"]


def wait_for_server(client: httpx.Client, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = client.get("/api/health")
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("后端未在预期时间内启动")


def login(client: httpx.Client, username: str, password: str) -> str:
    captcha = client.get("/api/auth/captcha")
    captcha.raise_for_status()
    captcha_id = captcha.json()["captcha_id"]
    redis_client = redis.Redis(
        host=Config.redis_host,
        port=Config.redis_port,
        password=Config.redis_password or None,
        db=Config.db,
        decode_responses=True,
    )
    try:
        code = redis_client.get(CAPTCHA_KEY_PREFIX + captcha_id)
    finally:
        redis_client.close()
    if not code:
        raise SystemExit("未能读取验证码，请确认 Redis 与服务配置一致")

    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "captcha_id": captcha_id,
            "captcha_code": code,
        },
    )
    if response.status_code != 200:
        raise SystemExit(f"演示账号登录失败：{response.status_code} {response.text}")
    return response.json()["access_token"]


def parse_sse_block(block: str) -> tuple[str | None, dict | None]:
    event = None
    data = None
    for line in block.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            data = json.loads(line[len("data: ") :])
    return event, data


def stream_question(
    client: httpx.Client, token: str, conversation_id: int, question: str
) -> dict:
    print(f"\n========== 提问：{question} ==========")
    answer_parts: list[str] = []
    meta: dict = {}
    done: dict = {}
    with client.stream(
        "POST",
        f"/api/chat/conversations/{conversation_id}/messages/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": question},
        timeout=180,
    ) as response:
        response.raise_for_status()
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                event, data = parse_sse_block(block)
                if event == "meta":
                    meta = data or {}
                    print("命中资料：")
                    for index, source in enumerate(meta.get("sources", []), start=1):
                        print(
                            f"  {index}. [{source.get('title')} / {source.get('section')}] "
                            f"score={source.get('score')}"
                        )
                elif event == "delta":
                    answer_parts.append(data.get("content", ""))
                elif event == "done":
                    done = data or {}
                elif event == "error":
                    print(f"错误事件：{data}")
    answer = "".join(answer_parts)
    print(f"\n回答（{done.get('latency_ms', 0)}ms）：\n{answer}")
    if meta.get("rewritten_query") and meta["rewritten_query"] != question:
        print(f"检索改写：{meta['rewritten_query']}")
    if not answer:
        raise SystemExit("未收到有效回答")
    return done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME)
    parser.add_argument(
        "--questions",
        nargs="+",
        default=DEFAULT_QUESTIONS,
        help="按顺序提问的问题列表",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        wait_for_server(client)
        token = login(client, args.username, args.password)
        headers = {"Authorization": f"Bearer {token}"}

        kb_response = client.get("/api/knowledge_bases", headers=headers)
        kb_response.raise_for_status()
        kb = next(
            (item for item in kb_response.json() if item["name"] == args.kb_name), None
        )
        if kb is None:
            raise SystemExit(f"账号 {args.username} 未找到知识库：{args.kb_name}")

        conversation = client.post(
            "/api/chat/conversations",
            headers=headers,
            json={
                "kb_ids": [kb["id"]],
                "model_name": Config.model,
                "temperature": 0.3,
                "top_p": 0.8,
                "max_tokens": 800,
                "history_rounds": 5,
            },
        )
        if conversation.status_code != 201:
            raise SystemExit(
                f"创建会话失败：{conversation.status_code} {conversation.text}"
            )
        conversation_id = conversation.json()["id"]
        print(f"会话已创建：id={conversation_id} kb={kb['name']} model={Config.model}")

        try:
            for question in args.questions:
                stream_question(client, token, conversation_id, question)

            messages = client.get(
                f"/api/chat/conversations/{conversation_id}/messages", headers=headers
            )
            messages.raise_for_status()
            payload = messages.json()
            print(f"\n消息记录：共 {payload['total']} 条")
            for item in payload["items"]:
                sources = len(item.get("sources") or [])
                print(
                    f"  - {item['role']}: {item['content'][:50]}"
                    f"{'...' if len(item['content']) > 50 else ''} (sources={sources})"
                )
        finally:
            client.delete(
                f"/api/chat/conversations/{conversation_id}", headers=headers
            )

    print("\n流式问答端到端验证通过")


if __name__ == "__main__":
    main()
