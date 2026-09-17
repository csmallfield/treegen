import pytest

from conftest import ROOT
from treegen.schema import SchemaError, load_scene, load_species, validate


def test_all_species_validate():
    for f in (ROOT / "species").glob("*.toml"):
        load_species(f)


def test_all_scenes_load():
    for f in (ROOT / "scenes").glob("*.toml"):
        load_scene(f)


def test_unknown_parameter_rejected():
    with pytest.raises(SchemaError):
        validate({"growth": {"attractor_cuont": 10}})


def test_range_rejected():
    with pytest.raises(SchemaError):
        validate({"radii": {"pipe_exponent": 9.0}})
