"""Assistant configuration with JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".redclaw" / "assistant"


@dataclass
class AssistantConfig:
    """Assistant configuration stored at ~/.redclaw/assistant/config.json."""

    persona_name: str = ""
    timezone: str = "UTC"
    briefing_time: str = "07:30"
    briefing_enabled: bool = True
    briefing_weather: bool = True
    briefing_news: bool = True
    briefing_tasks: bool = True
    weather_location: str = ""
    news_topics: list[str] = field(default_factory=lambda: ["tech"])

    _path: str = field(default="", repr=False)

    @classmethod
    def load(cls, config_dir: str | None = None) -> AssistantConfig:
        """Load config from disk, returning defaults if not found."""
        path = Path(config_dir) / "config.json" if config_dir else DEFAULT_CONFIG_DIR / "config.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["_path"] = str(path)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception as e:
                logger.warning(f"Failed to load assistant config: {e}")
        cfg = cls()
        cfg._path = str(path)
        return cfg

    def save(self) -> None:
        """Persist config to disk."""
        path = Path(self._path) if self._path else DEFAULT_CONFIG_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k != "_path"}
        _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


def _atomic_write(path: Path, content: str) -> None:
    """Write file atomically."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".redclaw_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
