"""League display names."""

from __future__ import annotations

from backend.models import League
from backend.utils.formatting import country_name


def league_title(league: League) -> str:
    """Azerbaijani name from config.yaml, else the API name with the country in Azerbaijani."""
    if league.name_az:
        return league.name_az
    country = country_name(league.country)
    return f"{league.name} ({country})" if country else league.name
