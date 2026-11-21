from __future__ import annotations

import pytest

from jarvis import config
from jarvis.tools import confirm, registry


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    config.set_config(
        config.Config(
            {
                "usuario": {"tratamiento": "señor", "ciudad": "Buenos Aires"},
                "gemini": {"modelos_texto": ["modelo-a", "modelo-b"]},
                "apps": {"alias": {"navegador": "Google Chrome"}},
            }
        )
    )
    confirm.reset()
    registry.load_all()
    yield tmp_path
    confirm.reset()
