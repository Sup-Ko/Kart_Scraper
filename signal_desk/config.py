"""Loading and validating the Signal Desk configuration.

The config is plain JSON so it can be read and written with only the standard
library, and hand-edited without learning a new format. Use ``signal-desk init``
to drop a documented sample in place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("signaldesk.config.json")
DEFAULT_DB_PATH = Path("signaldesk.db")


@dataclass
class SelfConfig:
    names: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)


@dataclass
class Topic:
    name: str
    keywords: list[str] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class Feed:
    name: str
    url: str
    channel: str = "topic"  # "topic" or "self"
    topic: str = "general"
    weight: float = 1.0


@dataclass
class ScoringConfig:
    recency_half_life_hours: float = 48.0
    self_weight: float = 1.6
    priority_boost: float = 1.5


@dataclass
class Config:
    self: SelfConfig = field(default_factory=SelfConfig)
    topics: list[Topic] = field(default_factory=list)
    feeds: list[Feed] = field(default_factory=list)
    priority_keywords: list[str] = field(default_factory=list)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    hibp_api_key: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        return cls(
            self=SelfConfig(**(data.get("self") or {})),
            topics=[Topic(**t) for t in data.get("topics", [])],
            feeds=[Feed(**f) for f in data.get("feeds", [])],
            priority_keywords=list(data.get("priority_keywords", [])),
            scoring=ScoringConfig(**(data.get("scoring") or {})),
            hibp_api_key=data.get("hibp_api_key", ""),
        )

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"No config at {path}. Run `signal-desk init` to create one."
            )
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def sample_config_text() -> str:
    """Return a documented sample configuration as JSON text."""
    sample = {
        "_comment": "Signal Desk config. Collects ONLY public feeds and your own accounts.",
        "self": {
            "names": ["Your Name"],
            "emails": ["you@example.com"],
            "handles": ["@yourhandle"],
        },
        "topics": [
            {
                "name": "AI policy",
                "keywords": ["EU AI Act", "AI regulation", "AI safety"],
                "weight": 1.2,
            },
            {
                "name": "My industry",
                "keywords": ["your company", "your competitor"],
                "weight": 1.0,
            },
        ],
        "feeds": [
            {
                "name": "Hacker News Front Page",
                "url": "https://hnrss.org/frontpage",
                "channel": "topic",
                "topic": "tech",
                "weight": 0.8,
            }
        ],
        "priority_keywords": [
            "breach",
            "lawsuit",
            "acquired",
            "outage",
            "recall",
            "leak",
        ],
        "scoring": {
            "recency_half_life_hours": 48,
            "self_weight": 1.6,
            "priority_boost": 1.5,
        },
        "hibp_api_key": "",
    }
    return json.dumps(sample, indent=2)
