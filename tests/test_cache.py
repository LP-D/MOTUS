import json

from motus_solver.cache import build_root_cache, cache_key, load_cache, save_cache
from motus_solver.corpus import Corpus
from motus_solver.solver import Solver
from motus_solver.tree import best_guess_composite, best_guess_entropy_pure
from motus_solver.scoring import letter_frequencies, positional_frequencies

FAKE_WORDS = ["RIVER", "RIVAL", "RIVET", "ROBOT", "ROUGE", "RADIO", "RAPIDE", "REVEIL", "REGIME"]


def make_corpus() -> Corpus:
    return Corpus(sorted(set(FAKE_WORDS)))


def test_build_root_cache_matches_dynamic_first_move():
    corpus = make_corpus()
    cache = build_root_cache(corpus)

    assert cache_key("R", 5) in cache
    assert cache_key("R", 6) in cache
    # aucune entrée pour un couple absent du corpus
    assert cache_key("Z", 5) not in cache

    candidates = corpus.subset("R", 5)
    global_freq = letter_frequencies(corpus)
    positional_freq = positional_frequencies(corpus, 5)
    expected_word, expected_entropy, expected_vowels = best_guess_composite(
        candidates, candidates, global_freq, positional_freq
    )

    entry = cache[cache_key("R", 5)]
    assert entry["word"] == expected_word
    assert entry["entropy"] == expected_entropy
    assert entry["vowels"] == expected_vowels


def test_build_root_cache_parallel_matches_sequential():
    corpus = make_corpus()
    sequential = build_root_cache(corpus, workers=1)
    parallel = build_root_cache(corpus, workers=2)
    assert parallel == sequential


def test_build_root_cache_excludes_blocklisted_words():
    corpus = make_corpus()
    baseline = build_root_cache(corpus)
    best_word = baseline[cache_key("R", 5)]["word"]

    blocked = build_root_cache(corpus, blocklist={best_word})
    assert blocked[cache_key("R", 5)]["word"] != best_word


def test_cache_roundtrip_json(tmp_path):
    corpus = make_corpus()
    cache = build_root_cache(corpus)
    path = tmp_path / "root_cache.json"
    save_cache(cache, path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == cache

    loaded = load_cache(path)
    assert loaded == cache


def test_load_cache_missing_file_returns_empty_dict(tmp_path):
    assert load_cache(tmp_path / "does_not_exist.json") == {}


def test_solver_uses_cache_for_first_move_only():
    corpus = make_corpus()
    cache = build_root_cache(corpus)
    entry = cache[cache_key("R", 5)]

    solver = Solver(letter="R", length=5, corpus=corpus, root_cache=cache)
    first_suggestion = solver.suggest(top_n=5)
    assert first_suggestion == [(entry["word"], entry["entropy"], entry["vowels"])]

    solver.play(entry["word"])
    # coup 2 : le cache (spécifique au premier coup) ne doit plus être consulté ;
    # suggest() retombe sur le calcul dynamique (jusqu'à top_n candidats, pas 1 seul).
    second_suggestion = solver.suggest(top_n=5)
    assert len(second_suggestion) > len(first_suggestion)


def test_solver_falls_back_to_dynamic_when_cached_word_removed_from_candidates():
    """Reproduit le bug découvert via le dashboard : si le mot du cache racine est
    rejeté par le jeu réel et retiré de solver.candidates (sans jouer de coup, donc
    sans changer self.history), suggest() ne doit PAS continuer à renvoyer
    indéfiniment ce même mot déjà écarté — il doit retomber sur le calcul dynamique."""
    corpus = make_corpus()
    cache = build_root_cache(corpus)
    entry = cache[cache_key("R", 5)]

    solver = Solver(letter="R", length=5, corpus=corpus, root_cache=cache)
    assert solver.suggest(top_n=1)[0][0] == entry["word"]

    # simule un rejet du mot en cache : retiré des candidats, mais aucun coup joué
    # (history reste vide, exactement comme au coup 1)
    solver.candidates.remove(entry["word"])
    assert not solver.history

    next_suggestion = solver.suggest(top_n=1)[0][0]
    assert next_suggestion != entry["word"]
    assert next_suggestion in solver.candidates


def test_build_root_cache_entropy_pure_strategy_matches_dynamic_first_move():
    """Tâche 3 : cache racine dédié à la stratégie entropie pure (fichier/entrées
    séparés du cache composite, cf. scripts/simulate_resolution_rate.py)."""
    corpus = make_corpus()
    cache = build_root_cache(corpus, strategy="entropy_pure")

    assert cache_key("R", 5) in cache

    candidates = corpus.subset("R", 5)
    expected_word, expected_entropy = best_guess_entropy_pure(candidates)

    entry = cache[cache_key("R", 5)]
    assert entry["word"] == expected_word
    assert entry["entropy"] == expected_entropy
    # pas de composante "vowels" (propre au scoring composite, non utilisée par
    # l'entropie pure) — distingue clairement les deux formats d'entrée.
    assert "vowels" not in entry


def test_build_root_cache_entropy_pure_parallel_matches_sequential():
    corpus = make_corpus()
    sequential = build_root_cache(corpus, workers=1, strategy="entropy_pure")
    parallel = build_root_cache(corpus, workers=2, strategy="entropy_pure")
    assert parallel == sequential


def test_build_root_cache_default_strategy_still_composite():
    """La stratégie composite (défaut, inchangé) ne doit pas être affectée par
    l'ajout du paramètre `strategy`."""
    corpus = make_corpus()
    default_call = build_root_cache(corpus)
    explicit_composite = build_root_cache(corpus, strategy="composite")
    assert default_call == explicit_composite


def test_refresh_blocklisted_entries_recomputes_only_stale_groups():
    """Un coup 1 en cache refusé par le vrai jeu (liste noire) est recalculé pour
    son groupe seulement, à l'identique d'un build complet avec cette liste noire."""
    from motus_solver.cache import refresh_blocklisted_entries

    corpus = make_corpus()
    cache = build_root_cache(corpus)
    stale_word = cache[cache_key("R", 5)]["word"]
    untouched = dict(cache[cache_key("R", 6)])

    refreshed = refresh_blocklisted_entries(cache, corpus, {stale_word})

    assert refreshed == [cache_key("R", 5)]
    assert cache[cache_key("R", 5)]["word"] != stale_word
    assert cache[cache_key("R", 5)] == build_root_cache(corpus, blocklist={stale_word})[cache_key("R", 5)]
    assert cache[cache_key("R", 6)] == untouched


def test_solver_falls_back_to_dynamic_when_key_missing():
    corpus = make_corpus()
    empty_cache: dict[str, dict] = {}
    solver_with_empty_cache = Solver(letter="R", length=5, corpus=corpus, root_cache=empty_cache)
    solver_without_cache = Solver(letter="R", length=5, corpus=corpus)

    assert solver_with_empty_cache.suggest(top_n=1) == solver_without_cache.suggest(top_n=1)
