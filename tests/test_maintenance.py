"""会话保留策略测试。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.conversation import Conversation
from app.services import maintenance as maintenance_module
from app.services.maintenance import archive_stale_conversations


def _naive_utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def test_archive_stale_conversations_only_marks_active_rows(
    db_maker, monkeypatch
) -> None:
    now = _naive_utcnow()
    async with db_maker() as db:
        db.add_all(
            [
                Conversation(
                    user_id=1,
                    title="很久没动",
                    kb_ids=[1],
                    updated_at=now - timedelta(days=120),
                ),
                Conversation(
                    user_id=1,
                    title="最近活跃",
                    kb_ids=[1],
                    updated_at=now - timedelta(days=2),
                ),
                Conversation(
                    user_id=1,
                    title="已经归档",
                    kb_ids=[1],
                    updated_at=now - timedelta(days=200),
                    archived_at=now - timedelta(days=100),
                ),
            ]
        )
        await db.commit()

    monkeypatch.setattr(maintenance_module, "get_session_maker", lambda: db_maker)
    archived = await archive_stale_conversations(days=90)
    assert archived == 1

    async with db_maker() as db:
        rows = list(await db.scalars(select(Conversation).order_by(Conversation.id)))

    by_title = {row.title: row for row in rows}
    assert by_title["很久没动"].archived_at is not None
    assert by_title["最近活跃"].archived_at is None
    assert by_title["已经归档"].archived_at is not None


async def test_archive_stale_conversations_disabled_when_retention_zero(
    monkeypatch,
) -> None:
    called = {"maker": False}

    def maker():
        called["maker"] = True
        raise AssertionError("保留天数为 0 时不应访问数据库")

    monkeypatch.setattr(maintenance_module, "get_session_maker", maker)
    assert await archive_stale_conversations(days=0) == 0
    assert called["maker"] is False
