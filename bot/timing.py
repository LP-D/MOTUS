from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

# Ordre d'exécution réel des points de mesure dans un cycle de coup : le solveur est
# appelé en premier, puis le coup est tapé/soumis/confirmé, et enfin le feedback qui
# vient d'apparaître est lu puis parsé — pas l'ordre "1,2,3,4,5,6" de l'énoncé de la
# tâche (qui liste les étapes par thème, pas par chronologie d'exécution).
#   3. solver_suggest      — appel au solveur, réception de la proposition
#   4. guess_typed         — saisie du mot (clavier/clics)
#   5. guess_submitted     — validation du coup (soumission)
#   6. feedback_confirmed  — détection que le nouveau feedback est bien affiché
#   1. feedback_detected   — lecture DOM du feedback affiché
#   2. feedback_parsed     — parsing du feedback en pattern exploitable
STEP_ORDER = [
    "solver_suggest",
    "guess_typed",
    "guess_submitted",
    "feedback_confirmed",
    "feedback_detected",
    "feedback_parsed",
]


@dataclass
class CycleTimer:
    """Instrumentation pure : enregistre les timestamps des points de mesure d'un
    cycle de coup (aucun effet sur le déroulement du jeu). `mark()` peut être appelé
    pour un sous-ensemble seulement des 6 points (ex. un mot rejeté ne va jamais
    jusqu'à `feedback_confirmed`) — `finish()` calcule les durées pour les marks
    effectivement posés, dans leur ordre chronologique réel, et log une ligne JSON."""

    attempt: int
    log_path: Path
    _marks: dict[str, float] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)

    def set_cycle_start(self) -> None:
        self._marks = {"cycle_start": time.perf_counter()}
        self._order = []

    def mark(self, step: str) -> None:
        if step not in STEP_ORDER:
            raise ValueError(f"étape inconnue : {step!r} (attendues : {STEP_ORDER})")
        self._marks[step] = time.perf_counter()
        self._order.append(step)

    def finish(self, extra: dict | None = None) -> dict:
        cycle_start = self._marks.get("cycle_start")
        if cycle_start is None:
            raise ValueError("set_cycle_start() doit être appelé avant finish()")

        durations: dict[str, float] = {}
        prev_time, prev_name = cycle_start, "cycle_start"
        for step in self._order:
            t = self._marks[step]
            durations[f"duration_{prev_name}_to_{step}_s"] = round(t - prev_time, 6)
            prev_time, prev_name = t, step

        record: dict = {
            "attempt": self.attempt,
            "timestamp_unix": time.time(),
            "steps_completed": list(self._order),
            **durations,
        }
        if self._order:
            record["duration_total_s"] = round(self._marks[self._order[-1]] - cycle_start, 6)
        if extra:
            record.update(extra)

        self._append(record)
        return record

    def _append(self, record: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
