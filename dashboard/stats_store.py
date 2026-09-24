"""Stockage persistant (JSON, pas de dépendance lourde) des parties jouées via le
dashboard, et agrégation en rapport statistique (`GET /bot/stats`).

Persiste entre sessions du dashboard : chaque partie terminée (résolue, échouée, ou
candidats épuisés — pas les parties arrêtées manuellement ni en erreur, qui ne
reflètent pas une performance réelle du bot) est ajoutée à `data/dashboard_stats.json`
via `record_game`, jamais écrasée entre deux lancements.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STATS_PATH = ROOT_DIR / "data" / "dashboard_stats.json"

MAX_ATTEMPTS = 6
THRESHOLDS = [2, 3, 4, 5, 6]
DEFAULT_LENGTH_BUCKET_SIZE = 2

_lock = threading.Lock()


def record_game(
    letter: str,
    length: int,
    attempts: int,
    solved: bool,
    outcome: str,
    guesses: list[str],
    solution: str | None = None,
    path: Path = DEFAULT_STATS_PATH,
) -> None:
    """Ajoute une partie terminée au store persistant. `guesses` : mots réellement
    soumis et acceptés par le jeu pendant cette partie (pas les rejets) — pour une
    partie non résolue, sert de piste d'investigation puisque le mot cible réel
    n'est pas connu du bot (il n'a jamais isolé un candidat unique). `solution` :
    le mot déduit avec certitude par le solveur (candidat unique restant) quand
    `solved=True` — cf. tâche 4, `aggregate_solutions`."""
    with _lock:
        games = _load(path)
        games.append(
            {
                "letter": letter.upper(),
                "length": length,
                "attempts": attempts,
                "solved": solved,
                "outcome": outcome,
                "guesses": [g.upper() for g in guesses],
                "solution": solution.upper() if solution else None,
                "timestamp": time.time(),
            }
        )
        _save(games, path)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(games: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games, ensure_ascii=False, indent=2), encoding="utf-8")


def _rate_block(games: list[dict]) -> dict:
    total = len(games)
    if total == 0:
        return {"total": 0, "solved": 0, "resolution_rate_pct": None, "histogram_pct": {}}
    solved = sum(1 for g in games if g["solved"])
    histogram = {
        f"<= {t}": round(100 * sum(1 for g in games if g["solved"] and g["attempts"] <= t) / total, 1)
        for t in THRESHOLDS
    }
    histogram["echec"] = round(100 * sum(1 for g in games if not g["solved"]) / total, 1)
    return {
        "total": total,
        "solved": solved,
        "resolution_rate_pct": round(100 * solved / total, 1),
        "histogram_pct": histogram,
    }


def _length_buckets(lengths: list[int], bucket_size: int) -> list[tuple[int, int]]:
    lengths = sorted(set(lengths))
    return [
        (chunk[0], chunk[-1])
        for chunk in (lengths[i : i + bucket_size] for i in range(0, len(lengths), bucket_size))
    ]


def _bucket_label(bucket: tuple[int, int]) -> str:
    lo, hi = bucket
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def aggregate_games(games: list[dict], bucket_size: int = DEFAULT_LENGTH_BUCKET_SIZE) -> dict:
    """Fonction pure (pas d'I/O) : agrège une liste de parties (même format que
    `record_game`) en rapport statistique. Séparée de `aggregate()` pour être
    testable sans fichier."""
    overall = _rate_block(games)

    buckets = _length_buckets([g["length"] for g in games], bucket_size)
    by_length = [
        {"length_range": _bucket_label(b), **_rate_block([g for g in games if b[0] <= g["length"] <= b[1]])}
        for b in buckets
    ]

    letters = sorted({g["letter"] for g in games})
    by_letter = [
        {"letter": letter, **_rate_block([g for g in games if g["letter"] == letter])} for letter in letters
    ]

    failed_games = [
        {
            "letter": g["letter"],
            "length": g["length"],
            "attempts": g["attempts"],
            "outcome": g["outcome"],
            "guesses_attempted": g["guesses"],
        }
        for g in games
        if not g["solved"]
    ]

    return {"overall": overall, "by_length": by_length, "by_letter": by_letter, "failed_games": failed_games}


def aggregate(path: Path = DEFAULT_STATS_PATH, bucket_size: int = DEFAULT_LENGTH_BUCKET_SIZE) -> dict:
    return aggregate_games(_load(path), bucket_size)


def aggregate_solutions(games: list[dict]) -> dict:
    """Distribution des mots solutions trouvés (parties résolues uniquement — le
    mot cible n'est connu avec certitude que dans ce cas). Fonction pure,
    testable sans fichier ; les parties enregistrées avant l'ajout du champ
    `solution` (absent -> None) sont ignorées ici plutôt que de fausser les
    comptes avec des valeurs manquantes.

    `repeated_solutions` : mots solutions vus plus d'une fois dans l'historique
    accumulé — ne PRÉSUME PAS d'un pool limité/cyclique, se contente de compter ce
    qui a été réellement observé ; à vérifier empiriquement au fil de
    l'accumulation (peu concluant sur un petit échantillon)."""
    # parties résolues, et parties non résolues dont la solution a été révélée par
    # l'abandon serveur (solutions hors corpus) : le mot tiré est connu dans les deux cas
    solved_with_solution = [g for g in games if g.get("solution")]
    n = len(solved_with_solution)

    length_counts: dict[int, int] = {}
    letter_counts: dict[str, int] = {}
    solution_counts: dict[str, int] = {}
    for g in solved_with_solution:
        length_counts[g["length"]] = length_counts.get(g["length"], 0) + 1
        letter_counts[g["letter"]] = letter_counts.get(g["letter"], 0) + 1
        solution_counts[g["solution"]] = solution_counts.get(g["solution"], 0) + 1

    repeated = sorted(
        ({"word": w, "count": c} for w, c in solution_counts.items() if c > 1),
        key=lambda r: (-r["count"], r["word"]),
    )

    return {
        "n_solutions_recorded": n,
        "n_distinct_solutions": len(solution_counts),
        "length_distribution": [
            {"length": length, "count": c, "pct": round(100 * c / n, 1)}
            for length, c in sorted(length_counts.items())
        ]
        if n
        else [],
        "letter_distribution": [
            {"letter": letter, "count": c, "pct": round(100 * c / n, 1)}
            for letter, c in sorted(letter_counts.items())
        ]
        if n
        else [],
        "repeated_solutions": repeated,
        "has_observed_repeats": len(repeated) > 0,
    }


def aggregate_solutions_report(path: Path = DEFAULT_STATS_PATH) -> dict:
    return aggregate_solutions(_load(path))
