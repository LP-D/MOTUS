"""Journal des tirages de groupes : statut « non observé, statut incertain » écrit
dans le cache sans changer le comportement du solveur."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import group_draws  # noqa: E402
from motus_solver.cache import rank_groups  # noqa: E402
from motus_solver.corpus import Corpus  # noqa: E402
from motus_solver.draws import (  # noqa: E402
    STATUS_OBSERVED,
    STATUS_UNCERTAIN,
    annotate_draw_status,
    load_draw_counts,
    record_draw,
)
from motus_solver.solver import Solver  # noqa: E402


def test_counts_ignore_resumed_games(tmp_path):
    path = tmp_path / "draws.jsonl"
    record_draw(path, "a", 5, "bot_runner")
    record_draw(path, "A", 5, "bot_runner", resumed=True)  # même mot, repris : déjà compté
    record_draw(path, "B", 6, "validate_root_candidates")
    assert load_draw_counts(path) == {"A_5": 1, "B_6": 1}
    assert load_draw_counts(tmp_path / "absent.jsonl") == {}


def test_unobserved_groups_are_marked_uncertain_not_excluded():
    cache = {"A_5": {"word": "ABCDE", "entropy": 1.0}, "Z_5": {"word": "ZEBRE", "entropy": 1.0}}
    uncertain = annotate_draw_status(cache, Counter({"A_5": 3}))
    assert uncertain == ["Z_5"]
    assert cache["Z_5"]["draw_status"] == STATUS_UNCERTAIN and cache["Z_5"]["draws_observed"] == 0
    assert cache["A_5"]["draw_status"] == STATUS_OBSERVED and cache["A_5"]["draws_observed"] == 3
    # comportement inchangé : l'entrée reste utilisée comme coup 1
    corpus = Corpus(["ZEBRE", "ZONES", "ZELES"])
    assert Solver("Z", 5, corpus, root_cache=cache).suggest(top_n=1)[0][0] == "ZEBRE"


def test_seed_takes_each_history_source_once(tmp_path):
    (tmp_path / "revealed_solutions.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"t": 2, "letter": "C", "length": 7, "answer": "CONVIER", "source": "validate_root_candidates"},
        {"t": 3, "letter": "I", "length": 9, "answer": "INTRANETS", "was_in_corpus": False},  # déjà dans les stats
    ]), encoding="utf-8")
    (tmp_path / "dashboard_stats.json").write_text(json.dumps([
        {"letter": "I", "length": 9, "timestamp": 1}]), encoding="utf-8")
    draws = tmp_path / "draws.jsonl"
    assert group_draws.seed(draws, tmp_path) == 2
    assert load_draw_counts(draws) == {"C_7": 1, "I_9": 1}


def test_rank_groups_depth_extends_cache_ranking():
    words = ["RATER", "RIVER", "RADIO", "ROBOT", "RUSES", "REINE", "RASER", "RUBAN", "RAMER", "REVER",
             "RIRES", "RONDE", "RAPES", "RENTE"]
    corpus = Corpus(words)
    deep = rank_groups(corpus, ["R_5"], depth=12)["R_5"]
    top10 = rank_groups(corpus, ["R_5"], depth=10)["R_5"]
    assert len(deep) == 12 and deep[:10] == top10
    without = rank_groups(corpus, ["R_5"], blocklist={deep[0]}, depth=12)["R_5"]
    assert deep[0] not in without
