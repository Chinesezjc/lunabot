from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, List, Set


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list, tuple))


def _decode_enum(enum_values: Any, value: Any) -> Any:
    if not isinstance(value, int):
        return value
    if isinstance(enum_values, list):
        if 0 <= value < len(enum_values):
            return enum_values[value]
        return value
    if isinstance(enum_values, dict):
        return enum_values.get(value, enum_values.get(str(value), value))
    return value


def _looks_like_compact_table(data: Dict[str, Any]) -> bool:
    value_keys = [k for k in data.keys() if not str(k).startswith("__")]
    if not value_keys:
        return False
    if not all(isinstance(data[k], list) for k in value_keys):
        return False

    lengths = {len(data[k]) for k in value_keys}
    if len(lengths) != 1:
        return False

    for key in value_keys:
        for item in data[key]:
            if item is None:
                continue
            if not _is_scalar(item):
                return False
            break
    return True


def _convert_compact_table(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    enums = data.get("__ENUM__", {})
    value_keys = [k for k in data.keys() if not str(k).startswith("__")]
    row_count = len(data[value_keys[0]]) if value_keys else 0
    rows: List[Dict[str, Any]] = [{} for _ in range(row_count)]

    for key in value_keys:
        col = data.get(key, [])
        enum_values = enums.get(key)
        for idx in range(row_count):
            if idx >= len(col):
                continue
            value = col[idx]
            if value is None:
                continue
            if enum_values is not None:
                value = _decode_enum(enum_values, value)
            rows[idx][key] = value
    return rows


def _normalize_cn_payload(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize_cn_payload(item) for item in node]
    if isinstance(node, dict):
        if _looks_like_compact_table(node):
            rows = _convert_compact_table(node)
            return [_normalize_cn_payload(item) for item in rows]
        return {k: _normalize_cn_payload(v) for k, v in node.items()}
    return node


CN_USER_CARD_KEYS = [
    "cardId",
    "level",
    "exp",
    "totalExp",
    "skillLevel",
    "skillExp",
    "totalSkillExp",
    "masterRank",
    "specialTrainingStatus",
    "defaultImage",
    "duplicateCount",
    "createdAt",
    "episodes",
]

CN_USER_CARD_EPISODE_KEYS = [
    "cardEpisodeId",
    "scenarioStatus",
    "scenarioStatusReasons",
    "isNotSkipped",
]

CN_USER_HONOR_KEYS = [
    "honorId",
    "level",
    "obtainedAt",
]

CN_USER_MYSEKAI_CHARACTER_TALK_KEYS = [
    "mysekaiCharacterTalkId",
    "isRead",
]

CN_USER_AREA_ACTION_SET_KEYS = [
    "id",
    "status",
]

CN_USER_CHALLENGE_LIVE_SOLO_STAGE_KEYS = [
    "challengeLiveStageType",
    "characterId",
    "challengeLiveStageId",
    "rank",
    "challengeLiveStageStatus",
    "point",
]

CN_USER_CHALLENGE_LIVE_SOLO_HIGHSCORE_REWARD_KEYS = [
    "characterId",
    "challengeLiveHighScoreRewardId",
    "challengeLiveHighScoreStatus",
]

CN_USER_MUSIC_RESULT_KEYS = [
    "musicId",
    "musicDifficultyType",
    "playType",
    "playResult",
    "highScore",
    "fullComboFlg",
    "fullPerfectFlg",
    "mvpCount",
    "superStarCount",
]

CN_USER_MUSIC_ACHIEVEMENT_KEYS = [
    "musicId",
    "musicAchievementId",
]

CN_USER_CHARACTER_MISSION_V2_STATUS_KEYS = [
    "missionId",
    "parameterGroupId",
    "seq",
    "characterId",
    "missionStatus",
]

CN_COMPACT_KEY_ALIASES = {
    "compactUserMusicResults": "userMusicResults",
    "compactUserMusicAchievements": "userMusicAchievements",
    "compactUserCharacterMissionV2Statuses": "userCharacterMissionV2Statuses",
}


def _row_to_dict(row: Any, keys: List[str]) -> Dict[str, Any] | None:
    if isinstance(row, dict):
        return row
    if not isinstance(row, list):
        return None
    item: Dict[str, Any] = {}
    for idx, key in enumerate(keys):
        if idx >= len(row):
            break
        value = row[idx]
        if value is None:
            continue
        item[key] = _normalize_cn_payload(value)
    return item


def _normalize_row_list(value: Any, keys: List[str]) -> Any:
    if isinstance(value, dict):
        value = _normalize_cn_payload(value)
    if not isinstance(value, list):
        return value
    if not value:
        return []
    if all(isinstance(item, dict) for item in value):
        return value

    ret: List[Dict[str, Any]] = []
    for row in value:
        item = _row_to_dict(row, keys)
        if item is not None:
            ret.append(item)
    return ret


def _normalize_cn_music_results(value: Any) -> Any:
    rows = _normalize_row_list(value, CN_USER_MUSIC_RESULT_KEYS)
    if not isinstance(rows, list):
        return rows
    for item in rows:
        if not isinstance(item, dict):
            continue
        if "musicDifficultyType" not in item and "musicDifficulty" in item:
            item["musicDifficultyType"] = item.pop("musicDifficulty")
    return rows


def _normalize_cn_character_mission_v2_statuses(value: Any, user_id: int | None) -> Any:
    rows = _normalize_row_list(value, CN_USER_CHARACTER_MISSION_V2_STATUS_KEYS)
    if not isinstance(rows, list):
        return rows
    if user_id is None:
        return rows
    for item in rows:
        if isinstance(item, dict) and "userId" not in item:
            item["userId"] = user_id
    return rows


def _normalize_cn_suite_payload(payload: Any) -> Any:
    data = _normalize_cn_payload(payload)
    if not isinstance(data, dict):
        return data

    data = dict(data)
    for compact_key, canonical_key in CN_COMPACT_KEY_ALIASES.items():
        if canonical_key not in data and compact_key in data:
            data[canonical_key] = data[compact_key]
        data.pop(compact_key, None)

    if "userCards" in data:
        cards = _normalize_row_list(data["userCards"], CN_USER_CARD_KEYS)
        if isinstance(cards, list):
            for card in cards:
                if isinstance(card, dict) and "episodes" in card:
                    card["episodes"] = _normalize_row_list(card["episodes"], CN_USER_CARD_EPISODE_KEYS)
        data["userCards"] = cards

    if "userHonors" in data:
        data["userHonors"] = _normalize_row_list(data["userHonors"], CN_USER_HONOR_KEYS)
    if "userMysekaiCharacterTalks" in data:
        data["userMysekaiCharacterTalks"] = _normalize_row_list(
            data["userMysekaiCharacterTalks"],
            CN_USER_MYSEKAI_CHARACTER_TALK_KEYS,
        )
    if "userAreas" in data and isinstance(data["userAreas"], list):
        for area in data["userAreas"]:
            if isinstance(area, dict) and "actionSets" in area:
                area["actionSets"] = _normalize_row_list(area["actionSets"], CN_USER_AREA_ACTION_SET_KEYS)
    if "userChallengeLiveSoloStages" in data:
        data["userChallengeLiveSoloStages"] = _normalize_row_list(
            data["userChallengeLiveSoloStages"],
            CN_USER_CHALLENGE_LIVE_SOLO_STAGE_KEYS,
        )
    if "userChallengeLiveSoloHighScoreRewards" in data:
        data["userChallengeLiveSoloHighScoreRewards"] = _normalize_row_list(
            data["userChallengeLiveSoloHighScoreRewards"],
            CN_USER_CHALLENGE_LIVE_SOLO_HIGHSCORE_REWARD_KEYS,
        )
    if "userMusicResults" in data:
        data["userMusicResults"] = _normalize_cn_music_results(data["userMusicResults"])
    if "userMusicAchievements" in data:
        data["userMusicAchievements"] = _normalize_row_list(
            data["userMusicAchievements"],
            CN_USER_MUSIC_ACHIEVEMENT_KEYS,
        )

    user_id: int | None = None
    user_gamedata = data.get("userGamedata")
    if isinstance(user_gamedata, dict):
        try:
            user_id = int(user_gamedata.get("userId"))
        except Exception:
            user_id = None
    if "userCharacterMissionV2Statuses" in data:
        data["userCharacterMissionV2Statuses"] = _normalize_cn_character_mission_v2_statuses(
            data["userCharacterMissionV2Statuses"],
            user_id,
        )

    return data


def _coerce_dict_field(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], dict):
            return value[0]
        if value and all(isinstance(item, dict) for item in value):
            return value[0]
    return {}


@dataclass
class Suite:
    upload_time: int = 0
    source: str = "?"
    local_source: str = ""

    userGamedata: Dict[str, Any] = field(default_factory=dict)
    userDecks: List[Dict[str, Any]] = field(default_factory=list)
    userCards: List[Dict[str, Any]] = field(default_factory=list)
    userMusicResults: List[Dict[str, Any]] = field(default_factory=list)
    userMysekaiMaterials: List[Dict[str, Any]] = field(default_factory=list)
    userAreas: List[Dict[str, Any]] = field(default_factory=list)
    userChallengeLiveSoloDecks: List[Dict[str, Any]] = field(default_factory=list)
    userCharacters: List[Dict[str, Any]] = field(default_factory=list)
    userMysekaiCanvases: List[Dict[str, Any]] = field(default_factory=list)
    userMysekaiFixtureGameCharacterPerformanceBonuses: List[Dict[str, Any]] = field(default_factory=list)
    userMysekaiGates: List[Dict[str, Any]] = field(default_factory=list)
    userWorldBloomSupportDecks: List[Dict[str, Any]] = field(default_factory=list)
    userHonors: List[Dict[str, Any]] = field(default_factory=list)
    userMysekaiCharacterTalks: List[Dict[str, Any]] = field(default_factory=list)
    userChallengeLiveSoloResults: List[Dict[str, Any]] = field(default_factory=list)
    userChallengeLiveSoloStages: List[Dict[str, Any]] = field(default_factory=list)
    userChallengeLiveSoloHighScoreRewards: List[Dict[str, Any]] = field(default_factory=list)
    userEvents: List[Dict[str, Any]] = field(default_factory=list)
    userWorldBlooms: List[Dict[str, Any]] = field(default_factory=list)
    userMusicAchievements: List[Dict[str, Any]] = field(default_factory=list)
    userPlayerFrames: List[Dict[str, Any]] = field(default_factory=list)
    userMaterials: List[Dict[str, Any]] = field(default_factory=list)
    userBonds: List[Dict[str, Any]] = field(default_factory=list)
    userCharacterMissionV2s: List[Dict[str, Any]] = field(default_factory=list)
    userCharacterMissionV2Statuses: List[Dict[str, Any]] = field(default_factory=list)
    userGachas: List[Dict[str, Any]] = field(default_factory=list)

    _present_fields: Set[str] = field(default_factory=set, repr=False)
    _extra_fields: Dict[str, Any] = field(default_factory=dict, repr=False)
    _region: str = field(default="", repr=False)

    SCALAR_FIELDS: ClassVar[Set[str]] = {"upload_time", "source", "local_source"}
    DICT_FIELDS: ClassVar[Set[str]] = {"userGamedata"}
    LIST_FIELDS: ClassVar[Set[str]] = {
        "userDecks",
        "userCards",
        "userMusicResults",
        "userMysekaiMaterials",
        "userAreas",
        "userChallengeLiveSoloDecks",
        "userCharacters",
        "userMysekaiCanvases",
        "userMysekaiFixtureGameCharacterPerformanceBonuses",
        "userMysekaiGates",
        "userWorldBloomSupportDecks",
        "userHonors",
        "userMysekaiCharacterTalks",
        "userChallengeLiveSoloResults",
        "userChallengeLiveSoloStages",
        "userChallengeLiveSoloHighScoreRewards",
        "userEvents",
        "userWorldBlooms",
        "userMusicAchievements",
        "userPlayerFrames",
        "userMaterials",
        "userBonds",
        "userCharacterMissionV2s",
        "userCharacterMissionV2Statuses",
        "userGachas",
    }
    ALL_FIELDS: ClassVar[List[str]] = [
        "upload_time",
        "source",
        "local_source",
        "userGamedata",
        "userDecks",
        "userCards",
        "userMusicResults",
        "userMysekaiMaterials",
        "userAreas",
        "userChallengeLiveSoloDecks",
        "userCharacters",
        "userMysekaiCanvases",
        "userMysekaiFixtureGameCharacterPerformanceBonuses",
        "userMysekaiGates",
        "userWorldBloomSupportDecks",
        "userHonors",
        "userMysekaiCharacterTalks",
        "userChallengeLiveSoloResults",
        "userChallengeLiveSoloStages",
        "userChallengeLiveSoloHighScoreRewards",
        "userEvents",
        "userWorldBlooms",
        "userMusicAchievements",
        "userPlayerFrames",
        "userMaterials",
        "userBonds",
        "userCharacterMissionV2s",
        "userCharacterMissionV2Statuses",
        "userGachas",
    ]

    @classmethod
    def from_region(cls, region: str, raw_payload: Any) -> "Suite":
        if isinstance(raw_payload, cls):
            return raw_payload

        payload = raw_payload
        if region == "cn":
            payload = _normalize_cn_suite_payload(payload)
        if not isinstance(payload, dict):
            raise ValueError(f"invalid suite payload type: {type(payload)}")

        present_fields = set(payload.keys())
        extra_fields = {k: v for k, v in payload.items() if k not in cls.ALL_FIELDS}

        kwargs: Dict[str, Any] = {}
        for key in cls.ALL_FIELDS:
            if key in payload:
                value = payload[key]
                if key in cls.DICT_FIELDS:
                    value = _coerce_dict_field(value)
                elif key in cls.LIST_FIELDS and not isinstance(value, list):
                    value = [value] if isinstance(value, dict) else []
                kwargs[key] = value
                continue
            if key == "upload_time":
                kwargs[key] = 0
            elif key == "source":
                kwargs[key] = "?"
            elif key == "local_source":
                kwargs[key] = ""
            elif key in cls.DICT_FIELDS:
                kwargs[key] = {}
            else:
                kwargs[key] = []

        suite = cls(**kwargs)
        suite._present_fields = present_fields
        suite._extra_fields = extra_fields
        suite._region = region
        return suite

    def has_field(self, key: str) -> bool:
        return key in self._present_fields

    def missing_fields(self, required_keys: Iterable[str]) -> List[str]:
        return [key for key in required_keys if key not in self._present_fields]

    def to_dict(self) -> Dict[str, Any]:
        ret = {key: getattr(self, key) for key in self.ALL_FIELDS}
        ret.update(self._extra_fields)
        return ret

    def __contains__(self, key: str) -> bool:
        return self.has_field(key)

    def __getitem__(self, key: str) -> Any:
        if key in self.ALL_FIELDS:
            return getattr(self, key)
        if key in self._extra_fields:
            return self._extra_fields[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self.ALL_FIELDS:
            setattr(self, key, value)
        else:
            self._extra_fields[key] = value
        self._present_fields.add(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default
