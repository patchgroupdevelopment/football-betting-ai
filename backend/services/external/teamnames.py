"""Matching team names across sources.

API-Football says "Manchester United", football-data.org "Manchester United FC",
football-data.co.uk "Man United". Names are normalised (accents, club prefixes
and suffixes, known abbreviations) and compared; two names that share a word
but differ in their distinctive word ("Manchester City" / "Manchester United",
"Real Madrid" / "Atletico Madrid") are deliberately scored low.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Expansions of abbreviations used mainly by football-data.co.uk (applied after normalisation).
ABBREVIATIONS: dict[str, str] = {
    "man united": "manchester united",
    "man utd": "manchester united",
    "man city": "manchester city",
    "nottm forest": "nottingham forest",
    "nott m forest": "nottingham forest",
    "sheffield weds": "sheffield wednesday",
    "sheffield utd": "sheffield united",
    "wolves": "wolverhampton",
    "wolverhampton wanderers": "wolverhampton",
    "spurs": "tottenham",
    "tottenham hotspur": "tottenham",
    "west brom": "west bromwich",
    "west bromwich albion": "west bromwich",
    "qpr": "queens park rangers",
    "ath madrid": "atletico madrid",
    "atl madrid": "atletico madrid",
    "ath bilbao": "athletic bilbao",
    "athletic club": "athletic bilbao",
    "sociedad": "real sociedad",
    "betis": "real betis",
    "espanol": "espanyol",
    "vallecano": "rayo vallecano",
    "celta": "celta vigo",
    "la coruna": "deportivo la coruna",
    "sp gijon": "sporting gijon",
    "m gladbach": "borussia monchengladbach",
    "monchengladbach": "borussia monchengladbach",
    "ein frankfurt": "eintracht frankfurt",
    "dortmund": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "bayern munich": "bayern munchen",
    "fc koln": "koln",
    "cologne": "koln",
    "hertha": "hertha berlin",
    "st pauli": "sankt pauli",
    "inter": "internazionale milano",
    "inter milan": "internazionale milano",
    "internazionale": "internazionale milano",
    "brighton": "brighton hove albion",
    "brighton hove": "brighton hove albion",
    "milan": "ac milan",
    "verona": "hellas verona",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "st etienne": "saint etienne",
    "sp lisbon": "sporting cp",
    "sporting lisbon": "sporting cp",
    "sporting portugal": "sporting cp",
    "guimaraes": "vitoria guimaraes",
    "psv eindhoven": "psv",
    "nac breda": "nac",
    "ad o den haag": "ado den haag",
}

# Words that carry no identity (legal forms, "club", sponsors' boilerplate).
NOISE_WORDS = frozenset(
    {
        "fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "us", "cd", "ud", "sd", "rc", "rcd", "ca", "cr", "ec",
        "club", "de", "del", "la", "the", "calcio", "fk", "sk", "if", "bk", "ff", "sv", "vfb", "vfl", "tsg",
        "bsc", "fsv", "spvgg", "1", "04", "05", "1899", "1846", "1860", "1896", "1900", "1909", "1910", "fbc",
        "football", "futbol", "futebol", "clube", "sport", "esporte", "regatas", "kv", "krc", "rsc", "kaa",
        "nv", "bv", "sl", "and",
    }
)
# Extra words that do not change which club is meant: "Leeds" = "Leeds United", "Sociedad" = "Real Sociedad".
# A distinctive extra word ("Paris" vs "Paris Saint Germain", "Brugge" vs "Cercle Brugge") is not accepted.
GENERIC_EXTRA_WORDS = frozenset(
    {"united", "utd", "city", "town", "rovers", "wanderers", "albion", "county", "athletic", "real", "hotspur"}
)
SUBSET_SCORE = 0.9
WEAK_SUBSET_SCORE = 0.75
# Kept although short: they distinguish clubs ("Inter" vs "Internacional" is handled by expansion).
DISTINCT_THRESHOLD = 0.6


def strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def normalize(name: str) -> str:
    text = strip_accents(name).lower().replace("&", " and ").replace("'", " ")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = ABBREVIATIONS.get(text, text)
    # Founding years ("Como 1907", "Stade Rennais 1901") never distinguish clubs.
    tokens = [token for token in text.split() if token not in NOISE_WORDS and not token.isdigit()]
    normalized = " ".join(tokens) or text
    return ABBREVIATIONS.get(normalized, normalized)


def _raw_tokens(name: str) -> list[str]:
    text = re.sub(r"[^a-z0-9 ]", " ", strip_accents(name).lower())
    return text.split()


def _initials_match(initials: set[str], other_name: str, shared: set[str]) -> bool:
    """"Estudiantes L.P." vs "Estudiantes de La Plata": L and P are initials of the other name's words."""
    remaining = [token for token in _raw_tokens(other_name) if token not in shared]
    available = {token[0] for token in remaining}
    return bool(initials) and initials <= available


def similarity(a: str, b: str) -> float:
    """0–1 score of two team names referring to the same club."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    if ta <= tb or tb <= ta:
        shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        extra = longer - shorter
        # "Sociedad" ⊂ "Real Sociedad" is fine; "Barcelona" ⊂ "Espanyol Barcelona" is another club.
        return SUBSET_SCORE if extra <= GENERIC_EXTRA_WORDS else WEAK_SUBSET_SCORE
    shared = ta & tb
    for own, other_name in ((ta - shared, b), (tb - shared, a)):
        if shared and own and all(len(token) == 1 for token in own) and _initials_match(own, other_name, shared):
            return 0.9
    ratio = SequenceMatcher(None, na, nb).ratio()
    only_a, only_b = ta - shared, tb - shared
    if shared and only_a and only_b:
        # Same city/prefix, different identity word: Manchester City vs Manchester United.
        best = max(SequenceMatcher(None, x, y).ratio() for x in only_a for y in only_b)
        if best < DISTINCT_THRESHOLD:
            return min(ratio, DISTINCT_THRESHOLD)
    return ratio


def unique_partial_match(name: str, candidates: dict[int, list[str]]) -> int | None:
    """The only candidate sharing a distinctive word with ``name`` ("Flamengo" -> "Flamengo RJ").

    Used only where the candidate set is small and specific (one league's teams
    not yet linked to API-Football); two candidates sharing the word means no match.
    """
    words = {w for w in normalize(name).split() if len(w) >= 4 and w not in GENERIC_EXTRA_WORDS}
    if not words:
        return None
    hits = [cid for cid, names in candidates.items() if any(words & set(normalize(n).split()) for n in names)]
    return hits[0] if len(hits) == 1 else None


AMBIGUITY_MARGIN = 0.03


def best_match(name: str, candidates: dict[int, list[str]], threshold: float) -> tuple[int | None, float]:
    """``candidates`` maps an id to the names it is known by. Returns (id, score) of the best one above threshold.

    Two candidates scoring about the same is treated as no match: guessing would
    silently attach one club's history to another.
    """
    scored = sorted(
        ((max((similarity(name, other) for other in names), default=0.0), candidate_id)
         for candidate_id, names in candidates.items()),
        reverse=True,
    )
    if not scored or scored[0][0] < threshold:
        return None, scored[0][0] if scored else 0.0
    if len(scored) > 1 and scored[1][0] >= threshold and scored[0][0] - scored[1][0] < AMBIGUITY_MARGIN:
        return None, scored[0][0]
    return scored[0][1], scored[0][0]
