"""手动初始化 MySQL 数据库与数据表。

用法：uv run python scripts/init_db.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logger import get_logger  # noqa: E402
from app.db.session import dispose_engine, ensure_database, init_models  # noqa: E402

logger = get_logger(__name__)


async def main() -> None:
    ensure_database()
    await init_models()
    await dispose_engine()
    logger.info("数据库初始化完成")


if __name__ == "__main__":
    asyncio.run(main())
