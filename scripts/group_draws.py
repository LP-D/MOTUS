#!/usr/bin/env python3
"""Journal des tirages de groupes (lettre, longueur) du serveur, cf. motus_solver.draws.

    python scripts/group_draws.py --seed      # une fois : reprend l'historique
    python scripts/group_draws.py --annotate  # écrit draw_status dans les 2 caches
    python scripts/group_draws.py             # statut par groupe

Historique repris par --seed (sans doublon entre sources) :
- data/revealed_solutions.jsonl, lignes `source: validate_root_candidates` (une par
  partie de validation, close par giveup) ;
- data/dashboard_stats.json : une ligne par partie jouée par le bot (les solutions
  révélées par le bot y figurent aussi, d'où l'exclusion des lignes sans source
  ci-dessus).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from motus_solver.cache import load_cache, save_cache  # noqa: E402
from motus_solver.draws import (  # noqa: E402
    STATUS_UNCERTAIN,
    annotate_draw_status,
    load_draw_counts,
)

DATA = ROOT_DIR / "data"
DRAWS = DATA / "group_draws.jsonl"
CACHES = (DATA / "root_cache.json", DATA / "root_cache_entropy_pure.json")


def seed(draws_path: Path, data: Path) -> int:
    if draws_path.exists():
        raise SystemExit(f"{draws_path} existe déjà : --seed ne s'utilise qu'une fois")
    rows = []
    for line in (data / "revealed_solutions.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("source") == "validate_root_candidates":
            rows.append({"t": r["t"], "letter": r["letter"], "length": r["length"],
                         "source": "historique:revealed_solutions"})
    for g in json.loads((data / "dashboard_stats.json").read_text(encoding="utf-8")):
        rows.append({"t": g.get("timestamp"), "letter": g["letter"], "length": g["length"],
                     "source": "historique:dashboard_stats"})
    rows.sort(key=lambda r: r["t"] or 0)
    draws_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return len(rows)


def annotate(draws_path: Path, cache_paths=CACHES) -> dict[str, list[str]]:
    counts = load_draw_counts(draws_path)
    out = {}
    for path in cache_paths:
        cache = load_cache(path)
        out[Path(path).name] = annotate_draw_status(cache, counts)
        save_cache(cache, path)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--annotate", action="store_true")
    args = parser.parse_args()
    if args.seed:
        print(f"{seed(DRAWS, DATA)} tirages historiques écrits dans {DRAWS}")
    if args.annotate:
        for name, uncertain in annotate(DRAWS).items():
            print(f"{name} : {len(uncertain)} groupe(s) au statut {STATUS_UNCERTAIN}")
    counts = load_draw_counts(DRAWS)
    keys = sorted(load_cache(CACHES[0]))
    unobserved = [k for k in keys if not counts.get(k)]
    print(json.dumps({"draws": sum(counts.values()), "groups_observed": len(keys) - len(unobserved),
                      "groups_unobserved_uncertain": len(unobserved), "unobserved": unobserved},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
