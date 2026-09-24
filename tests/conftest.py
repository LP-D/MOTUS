from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_group_draws(tmp_path, monkeypatch):
    """Aucun test ne doit écrire dans le vrai journal des tirages (data/group_draws.jsonl)."""
    module = sys.modules.get("bot_runner")
    if module is not None:
        monkeypatch.setattr(module, "DEFAULT_DRAWS", tmp_path / "group_draws.jsonl")
