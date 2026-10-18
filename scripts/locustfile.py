"""AskToFit Locust 压测入口。

用法：
    uv sync --group loadtest
    uv run python scripts/prepare_load_test.py --users 20
    uv run locust -f scripts/locustfile.py --headless -u 20 -r 2 -t 60s \
      --host http://127.0.0.1:8000
    uv run python scripts/prepare_load_test.py --cleanup
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from locust import HttpUser, between, events, task  # noqa: E402

from config import Config  # noqa: E402
from scripts.loadtest_sse import parse_sse_events  # noqa: E402

API = Config.api_prefix
CONVERSATIONS = f"{API}/chat/conversations"
STREAM_NAME = f"{CONVERSATIONS}/:id/messages/stream"
USERS_FILE = Path(os.getenv("ASKTOFIT_USERS_FILE", PROJECT_ROOT / "loadtest" / "users.json"))

QUESTIONS = [
    "满多少包邮？偏远地区运费怎么算？",
    "开发票要多久？电子普票和专票有什么区别？",
    "七天无理由退货需要保留吊牌吗？运费谁承担？",
    "我 165cm、55kg，想买宽松一点的针织衫，选什么码？",
    "会员积分和优惠券可以叠加使用吗？",
    "支持哪些付款方式？对公转账怎么走？",
    "退款一般多久到账？退到哪里？",
    "2026 春夏的冰丝针织衫有什么面料卖点？",
    "门店导购遇到尺码不合的顾客怎么快速应答？",
    "618 大促的满减规则和会员折扣能同时用吗？",
]


def load_users() -> dict:
    if not USERS_FILE.exists():
        raise SystemExit(
            f"压测用户文件不存在：{USERS_FILE}；"
            "请先运行 uv run python scripts/prepare_load_test.py"
        )
    try:
        payload = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"压测用户文件读取失败：{exc}") from exc
    users = payload.get("users") or []
    if not users:
        raise SystemExit(f"压测用户文件没有用户：{USERS_FILE}")
    if not payload.get("kb_id"):
        raise SystemExit(f"压测用户文件缺少 kb_id：{USERS_FILE}")
    return payload


class AskToFitUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        payload = load_users()
        self.kb_id = int(payload["kb_id"])
        account = random.choice(payload["users"])
        self.client.headers.update(
            {"Authorization": f"Bearer {account['token']}"}
        )
        self.conversation_id: int | None = None
        self._create_conversation()

    def _create_conversation(self) -> None:
        with self.client.post(
            CONVERSATIONS,
            json={"kb_ids": [self.kb_id], "title": "locust load test"},
            name=f"{CONVERSATIONS} [create]",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"创建压测会话失败：HTTP {response.status_code}")
                return
            try:
                self.conversation_id = int(response.json()["id"])
            except (KeyError, TypeError, ValueError):
                response.failure("创建压测会话响应缺少 id")
                return
            response.success()

    @task(1)
    def health(self) -> None:
        self.client.get(f"{API}/health", name=f"{API}/health")

    @task(2)
    def knowledge_bases(self) -> None:
        self.client.get(f"{API}/knowledge_bases", name=f"{API}/knowledge_bases")

    @task(2)
    def conversations(self) -> None:
        self.client.get(CONVERSATIONS, name=CONVERSATIONS)

    @task(1)
    def knowledge_gap_stats(self) -> None:
        self.client.get(
            f"{API}/chat/knowledge-gaps/stats",
            name=f"{API}/chat/knowledge-gaps/stats",
        )

    @task(6)
    def chat_stream(self) -> None:
        if self.conversation_id is None:
            self._create_conversation()
            if self.conversation_id is None:
                return
        self._stream_question(random.choice(QUESTIONS))

    def _stream_question(self, question: str) -> None:
        started = time.perf_counter()
        first_token_ms: float | None = None
        buffer = ""
        meta_seen = False
        done_seen = False
        error_message: str | None = None
        done_content = ""
        streamed_chars = 0
        url = f"{CONVERSATIONS}/{self.conversation_id}/messages/stream"

        with self.client.post(
            url,
            json={"content": question},
            stream=True,
            catch_response=True,
            timeout=300,
            name=STREAM_NAME,
        ) as response:
            if response.status_code != 200:
                response.failure(f"SSE HTTP {response.status_code}")
                return

            for chunk in response.iter_content(chunk_size=256):
                if not chunk:
                    continue
                buffer += chunk.decode("utf-8", errors="ignore")
                parsed, buffer = parse_sse_events(buffer)
                for item in parsed:
                    event = item.get("event")
                    data = item.get("data") if isinstance(item.get("data"), dict) else {}
                    if event == "delta":
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - started) * 1000
                            events.request.fire(
                                request_type="SSE_FIRST",
                                name=STREAM_NAME,
                                response_time=first_token_ms,
                                response_length=0,
                            )
                        streamed_chars += len(str(data.get("content") or ""))
                    elif event == "meta":
                        meta_seen = True
                    elif event == "done":
                        done_seen = True
                        done_content = str(data.get("content") or "")
                    elif event == "error":
                        error_message = str(data.get("message") or "未知错误")
                    if done_seen or error_message:
                        break
                if done_seen or error_message:
                    break

            total_ms = (time.perf_counter() - started) * 1000
            failure: str | None = None
            if error_message:
                failure = f"SSE error：{error_message}"
            elif not meta_seen:
                failure = "SSE 未收到 meta 事件"
            elif not done_seen:
                failure = "SSE 未收到 done 事件"
            elif not (done_content.strip() or streamed_chars > 0):
                failure = "SSE 回答内容为空"

            if failure:
                response.failure(failure)
            else:
                response.success()

        events.request.fire(
            request_type="SSE_TOTAL",
            name=STREAM_NAME,
            response_time=total_ms,
            response_length=len(done_content),
            exception=Exception(failure) if failure else None,
        )
