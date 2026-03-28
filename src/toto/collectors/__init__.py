"""Data collection modules."""

from toto.collectors.base import BaseCollector
from toto.collectors.football_lab import FootballLabCollector
from toto.collectors.jleague import JLeagueCollector
from toto.collectors.mock import MockCollector
from toto.collectors.toto_official import TotoOfficialCollector
from toto.collectors.totomo import TotomoCollector

__all__ = [
    "BaseCollector",
    "FootballLabCollector",
    "JLeagueCollector",
    "MockCollector",
    "TotoOfficialCollector",
    "TotomoCollector",
]
