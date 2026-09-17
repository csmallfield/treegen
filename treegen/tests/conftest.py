from pathlib import Path

import pytest

from treegen.schema import load_species

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def small_oak():
    """Quercus at reduced attractor count so the suite runs in seconds."""
    p = load_species(ROOT / "species" / "quercus.toml")
    p["growth"]["attractor_count"] = 2500
    p["light"]["grid_resolution"] = 32
    return p
