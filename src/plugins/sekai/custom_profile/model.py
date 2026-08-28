# 自定义个人信息渲染请求模型（适配自 Haruki-Drawing-API src/sekai/profile/model.py）
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CustomProfileCardRenderRequest:
    schema_version: int = 1
    kind: str = "pjsk_custom_profile_card"
    region: str = "cn"
    card: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    profile_context: dict[str, Any] = field(default_factory=dict)
