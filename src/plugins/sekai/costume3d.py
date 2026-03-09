from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, List, Set


@dataclass
class Costume3D:
    id: int = 0
    seq: int = 0
    costume3dGroupId: int = 0
    costume3dType: str = ""
    name: str = ""
    partType: str = ""
    colorId: int = 0
    colorName: str = ""
    characterId: int = 0
    costume3dRarity: str = ""
    howToObtain: str = ""
    assetbundleName: str = ""
    designer: str = ""
    publishedAt: int = 0
    archiveDisplayType: str = "none"
    archivePublishedAt: int = 0

    _present_fields: Set[str] = field(default_factory=set, repr=False)
    _extra_fields: Dict[str, Any] = field(default_factory=dict, repr=False)

    FIELDS: ClassVar[List[str]] = [
        "id",
        "seq",
        "costume3dGroupId",
        "costume3dType",
        "name",
        "partType",
        "colorId",
        "colorName",
        "characterId",
        "costume3dRarity",
        "howToObtain",
        "assetbundleName",
        "designer",
        "publishedAt",
        "archiveDisplayType",
        "archivePublishedAt",
    ]

    ROW_KEYS_15: ClassVar[List[str]] = [
        "id",
        "seq",
        "costume3dGroupId",
        "costume3dType",
        "name",
        "partType",
        "colorId",
        "colorName",
        "characterId",
        "costume3dRarity",
        "howToObtain",
        "assetbundleName",
        "designer",
        "archiveDisplayType",
        "archivePublishedAt",
    ]
    ROW_KEYS_16: ClassVar[List[str]] = FIELDS
    INT_FIELDS: ClassVar[Set[str]] = {
        "id",
        "seq",
        "costume3dGroupId",
        "colorId",
        "characterId",
        "publishedAt",
        "archivePublishedAt",
    }

    @classmethod
    def from_raw(cls, raw: "Costume3D | dict | list") -> "Costume3D":
        if isinstance(raw, cls):
            return raw

        if isinstance(raw, dict):
            payload = raw
        elif isinstance(raw, list):
            keys = cls.ROW_KEYS_16 if len(raw) >= 16 else cls.ROW_KEYS_15
            payload = {}
            for idx, key in enumerate(keys):
                if idx < len(raw):
                    payload[key] = raw[idx]
        else:
            raise TypeError(f"unsupported costume3d payload type: {type(raw)}")

        present_fields = set(payload.keys())
        extra_fields = {k: v for k, v in payload.items() if k not in cls.FIELDS}
        kwargs = {k: payload.get(k, 0 if k in cls.INT_FIELDS else "") for k in cls.FIELDS}
        costume = cls(**kwargs)
        costume._present_fields = present_fields
        costume._extra_fields = extra_fields
        return costume

    def has_field(self, key: str) -> bool:
        return key in self._present_fields

    def missing_fields(self, required_keys: Iterable[str]) -> List[str]:
        return [key for key in required_keys if key not in self._present_fields]

    def to_dict(self) -> Dict[str, Any]:
        ret = {k: getattr(self, k) for k in self.FIELDS}
        ret.update(self._extra_fields)
        return ret

    def __contains__(self, key: str) -> bool:
        return key in self._present_fields or key in self._extra_fields

    def __getitem__(self, key: str) -> Any:
        if key in self.FIELDS:
            if key not in self._present_fields:
                raise KeyError(key)
            return getattr(self, key)
        if key in self._extra_fields:
            return self._extra_fields[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self.FIELDS:
            setattr(self, key, value)
        else:
            self._extra_fields[key] = value
        self._present_fields.add(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
