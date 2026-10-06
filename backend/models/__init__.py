"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from backend.models.betting import Bet, Prediction, PyramidStage
from backend.models.football import (
    Injury,
    League,
    Lineup,
    Match,
    MatchStats,
    Player,
    PlayerMatchStats,
    Result,
    ScorerStat,
    Standing,
    Team,
    TeamAlias,
    TeamRating,
)
from backend.models.odds import OddsSnapshot
from backend.models.system import AnalysisLog, ApiCacheEntry, ModelRun, Notification, User

__all__ = [
    "AnalysisLog",
    "ApiCacheEntry",
    "Bet",
    "Injury",
    "League",
    "Lineup",
    "Match",
    "MatchStats",
    "ModelRun",
    "Notification",
    "OddsSnapshot",
    "Player",
    "PlayerMatchStats",
    "Prediction",
    "PyramidStage",
    "Result",
    "ScorerStat",
    "Standing",
    "Team",
    "TeamAlias",
    "TeamRating",
    "User",
]
