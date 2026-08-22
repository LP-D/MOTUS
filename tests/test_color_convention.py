"""Vérification bloquante (demandée explicitement) : convention couleur de Tusmo.

Contrairement à Wordle (vert = bien placé), Tusmo affiche le "bien placé" en
ROUGE et le "mal placé" en JAUNE/OR — confirmé empiriquement en jeu réel (partie
/infinite, mot COMMENCER) via `getComputedStyle()` :

    cell__mark--correct -> rgb(209, 72, 72)   (rouge, PAS vert)
    cell__mark--present -> rgb(229, 185, 83)  (jaune/or)

Le parsing (`bot.parser.cell_state`) ne lit JAMAIS la couleur — uniquement le nom
de classe CSS ("correct"/"present"/absence de .cell__mark), qui correspond mot
pour mot aux valeurs littérales "correct"/"present"/"absent" renvoyées par l'API
serveur elle-même (POST /api/game/{id}/guess, cf. docs/tuzmo_site_notes.md). Le
code était donc déjà correct ; ces tests figent la convention en dur pour
empêcher qu'un futur refactor bascule par erreur sur une détection par couleur en
supposant la convention Wordle standard (vert = correct)."""
from __future__ import annotations

from bot.parser import cell_state, row_pattern

# Couleurs de fond réellement mesurées (getComputedStyle, partie /infinite réelle,
# session du 21/08/2026) — documentées ici pour référence, PAS utilisées par le
# parsing (qui reste basé sur le nom de classe, indépendant de la couleur).
TUSMO_CORRECT_BACKGROUND_RGB = "rgb(209, 72, 72)"  # rouge, PAS vert (Wordle)
TUSMO_PRESENT_BACKGROUND_RGB = "rgb(229, 185, 83)"  # jaune/or


def test_correct_class_maps_to_correct_state_regardless_of_wordle_convention():
    """cell__mark--correct -> état CORRECT ('2'), quelle que soit la couleur
    affichée (rouge sur Tusmo, pas vert comme Wordle) : le parsing ignore la
    couleur, seul le nom de classe compte."""
    assert cell_state("cell cell--revealed", "cell__mark cell__mark--correct anim-stamp") == "2"


def test_present_class_maps_to_present_state():
    assert cell_state("cell cell--revealed", "cell__mark cell__mark--present anim-pop") == "1"


def test_absent_class_maps_to_absent_state():
    assert cell_state("cell cell--absent", None) == "0"


def test_row_pattern_matches_real_api_ground_truth():
    """Reproduit une vraie soumission observée en jeu (COMMENCER, partie
    /infinite, session du 21/08/2026) : le DOM capturé et le JSON renvoyé par
    l'API serveur pour la MÊME soumission doivent produire le même pattern —
    preuve directe que le parsing DOM est fidèle à la source de vérité serveur,
    indépendamment de toute interprétation de couleur.

    Réponse API brute observée :
    {"guesses": [{"word": "COMMENCER", "result": [
        "correct","absent","absent","absent","present",
        "absent","absent","absent","present"
    ]}]}
    """
    dom_cells = [
        ("cell cell--revealed", "cell__mark cell__mark--correct anim-stamp"),  # C -> correct
        ("cell cell--absent", None),  # O -> absent
        ("cell cell--absent", None),  # M -> absent
        ("cell cell--absent", None),  # M -> absent
        ("cell cell--revealed", "cell__mark cell__mark--present anim-pop"),  # E -> present
        ("cell cell--absent", None),  # N -> absent
        ("cell cell--absent", None),  # C -> absent
        ("cell cell--absent", None),  # E -> absent
        ("cell cell--revealed", "cell__mark cell__mark--present anim-pop"),  # R -> present
    ]
    api_result = [
        "correct", "absent", "absent", "absent", "present",
        "absent", "absent", "absent", "present",
    ]
    code_of = {"correct": "2", "present": "1", "absent": "0"}
    expected_pattern = "".join(code_of[r] for r in api_result)

    assert row_pattern(dom_cells) == expected_pattern == "200010001"
