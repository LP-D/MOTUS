"""Tirages de groupes (lettre imposée, longueur) observés sur le serveur Tuzmo.

Le serveur choisit le groupe de chaque mot /infinite. Sur 467 mots observés au
24/09/2026, 40 des 130 groupes du corpus n'ont jamais été tirés (V,9 compris,
pourtant 2 147 mots). Ne jamais tiré ne veut pas dire exclu : ces groupes sont
marqués « non observé, statut incertain » dans les caches racine, sans changement
de comportement du solveur. Chaque tirage est journalisé (une ligne JSON par mot)
pour accumuler la preuve avant toute conclusion d'exclusion structurelle.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .cache import cache_key

STATUS_OBSERVED = "observe"
STATUS_UNCERTAIN = "non_observe_incertain"


def record_draw(path: str | Path, letter: str, length: int, source: str, **extra) -> dict:
    """Ajoute un tirage au journal `path` (créé au besoin) et le retourne."""
    entry = {"t": time.time(), "letter": letter.upper(), "length": int(length), "source": source, **extra}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_draw_counts(path: str | Path) -> Counter:
    """Nombre de tirages observés par clé de cache (`"A_7"`). Les lignes `resumed`
    (partie reprise, déjà comptée à son tirage) ne comptent pas."""
    counts: Counter = Counter()
    path = Path(path)
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            if not entry.get("resumed"):
                counts[cache_key(entry["letter"], entry["length"])] += 1
    return counts


def annotate_draw_status(cache: dict[str, dict], counts: Counter) -> list[str]:
    """Écrit `draw_status` et `draws_observed` dans chaque entrée de `cache` (en
    place). Purement informatif : le solveur n'en tient pas compte. Retourne les
    clés au statut incertain."""
    uncertain = []
    for key, entry in cache.items():
        n = counts.get(key, 0)
        entry["draws_observed"] = n
        entry["draw_status"] = STATUS_OBSERVED if n else STATUS_UNCERTAIN
        if not n:
            uncertain.append(key)
    return sorted(uncertain)
