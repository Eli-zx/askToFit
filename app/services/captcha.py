"""图形验证码生成与校验（Redis 存储，一次性使用）。"""

from __future__ import annotations

import base64
import hmac
import io
import random
import secrets
import uuid
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from config import Config

CAPTCHA_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CAPTCHA_KEY_PREFIX = "captcha:"


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_captcha(code: str) -> str:
    """绘制验证码并返回 data URI。"""
    width, height = 132, 44
    image = Image.new("RGB", (width, height), (246, 248, 252))
    draw = ImageDraw.Draw(image)
    font = _load_font(30)

    palette = [(79, 70, 229), (14, 116, 144), (190, 24, 93), (22, 101, 52), (124, 58, 237)]
    step = width // (len(code) + 1)
    for index, char in enumerate(code):
        x = step * (index + 1) - 10 + random.randint(-3, 3)
        y = random.randint(1, 8)
        draw.text((x, y), char, font=font, fill=random.choice(palette))

    # 干扰线
    for _ in range(4):
        draw.line(
            (
                random.randint(0, width),
                random.randint(0, height),
                random.randint(0, width),
                random.randint(0, height),
            ),
            fill=(random.randint(150, 220), random.randint(150, 220), random.randint(150, 220)),
            width=1,
        )
    # 噪点
    for _ in range(60):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)
        draw.point((x, y), fill=(random.randint(120, 210),) * 3)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def create_captcha(redis: Any) -> dict:
    code = "".join(secrets.choice(CAPTCHA_CHARS) for _ in range(Config.captcha_length))
    captcha_id = uuid.uuid4().hex
    await redis.setex(CAPTCHA_KEY_PREFIX + captcha_id, Config.captcha_ttl_seconds, code)
    return {
        "captcha_id": captcha_id,
        "image": _render_captcha(code),
        "expires_in": Config.captcha_ttl_seconds,
    }


async def verify_captcha(redis: Any, captcha_id: str, code: str) -> bool:
    """校验后立即删除，保证一次性使用。"""
    if not captcha_id or not code:
        return False
    key = CAPTCHA_KEY_PREFIX + captcha_id
    stored = await redis.get(key)
    await redis.delete(key)
    if stored is None:
        return False
    return hmac.compare_digest(str(stored).upper(), code.strip().upper())
