"""CordisClaw relay — forward group messages to CordisClaw for fallback processing.

LunaBot acts as the primary message handler.  When LunaBot cannot confidently
handle a message, this plugin forwards it to CordisClaw's qq_serve endpoint.
CordisClaw's agent decides whether to respond (returns IGNORE if not).

Architecture:
  QQ group message
       │
       ▼
  LunaBot (primary LLM handler)
       │
       ├── handled → LunaBot replies directly
       │
       └── not handled → POST to CordisClaw qq_serve
                              │
                              ▼
                         CordisClaw agent
                              │
                         ┌────┴────┐
                         │ respond  │ IGNORE
                         ▼         ▼
                      qq_send    (silent)
"""

import aiohttp
import json

from nonebot import on_message, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

from .utils import get_logger, Config

logger = get_logger("CordisClawRelay")
config = Config("cordisclaw_relay")
driver = get_driver()

# ── Configuration ──────────────────────────────────────────────────────────
# Settings read from config/cordisclaw_relay.yaml (create if missing):
#
#   cordisclaw_url: "http://127.0.0.1:8080/onebot/event"
#   allow_groups: []     # empty = all groups
#   forward_all: false   # true = forward every message; false = only on LunaBot failure
#   timeout: 10          # HTTP request timeout in seconds

CORDISCLAW_URL = config.item("cordisclaw_url").get(default="http://127.0.0.1:8090/onebot/event")
ALLOW_GROUPS = config.item("allow_groups").get(default=[])
FORWARD_ALL = config.item("forward_all").get(default=False)
TIMEOUT = config.item("timeout").get(default=10)


def _group_allowed(group_id: int) -> bool:
    if not ALLOW_GROUPS:
        return True
    return str(group_id) in [str(g) for g in ALLOW_GROUPS]


# ── Event handler ──────────────────────────────────────────────────────────

async def _is_group_message(bot: Bot, event) -> bool:
    """Only forward group messages (not private chat for now)."""
    return isinstance(event, GroupMessageEvent) and _group_allowed(event.group_id)


cordisclaw_relay = on_message(
    rule=Rule(_is_group_message),
    priority=99,  # Run after all other handlers (NoneBot default priority ~1-99)
    block=False,
)


@cordisclaw_relay.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    """Forward group messages to CordisClaw for fallback processing."""
    try:
        # Build OneBot v11 event payload.
        payload = {
            "post_type": "message",
            "message_type": "group",
            "time": event.time,
            "self_id": event.self_id,
            "sub_type": event.sub_type,
            "message_id": event.message_id,
            "group_id": event.group_id,
            "user_id": event.user_id,
            "message": event.get_message(),
            "raw_message": event.get_plaintext(),
            "sender": {
                "user_id": event.sender.user_id,
                "nickname": event.sender.nickname or "",
                "card": event.sender.card or "",
                "role": event.sender.role or "member",
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                CORDISCLAW_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"CordisClaw returned {resp.status} for group {event.group_id}"
                    )
    except aiohttp.ClientError as e:
        logger.warning(f"CordisClaw relay failed: {e}")
    except Exception as e:
        logger.error(f"CordisClaw relay unexpected error: {e}")


# ── Startup check ──────────────────────────────────────────────────────────

@driver.on_startup
async def _check_cordisclaw():
    """Log relay configuration on startup."""
    logger.info(
        f"CordisClaw relay: url={CORDISCLAW_URL} "
        f"groups={ALLOW_GROUPS or 'all'} "
        f"forward_all={FORWARD_ALL}"
    )
    # Health check (best effort — won't crash startup if CordisClaw is down).
    try:
        health_url = CORDISCLAW_URL.rsplit("/", 1)[0] + "/health"
        async with aiohttp.ClientSession() as session:
            async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    logger.info("CordisClaw qq_serve is reachable")
                else:
                    logger.warning(f"CordisClaw health check returned {resp.status}")
    except Exception as e:
        logger.info(f"CordisClaw qq_serve not reachable (this is normal if CordisClaw is not running): {e}")
