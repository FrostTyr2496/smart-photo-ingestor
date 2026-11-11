"""Config loader: defaults, PHOTOVAULT_HOME, per-section overrides, bodies merge, and each validation error."""

from pathlib import Path

import pytest

from photovault.config import (
    DEFAULT_BODIES,
    Config,
    ConfigError,
    IngestConfig,
    NamingConfig,
    PolicyConfig,
    default_home,
    load_config,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "photovault.example.toml"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "pvhome"
    monkeypatch.setenv("PHOTOVAULT_HOME", str(h))
    return h


def write(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_with_no_file(home):
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.home == home
    assert cfg.catalog_path == home / "catalog.sqlite"
    assert cfg.ingest == IngestConfig()
    assert cfg.naming == NamingConfig()
    assert cfg.naming.template == "M{num:04d}_{date}-{time}-{subsec}_{body}_{ident}"
    assert cfg.naming.day_boundary_hour == 12
    assert cfg.policy == PolicyConfig(required={"WORKING": 1})
    assert cfg.bodies == DEFAULT_BODIES
    assert cfg.exiftool_workers == 4


def test_default_bodies_are_the_documented_six():
    assert DEFAULT_BODIES == {
        "NIKON Z 6": "Z6",
        "NIKON Z 6_2": "Z6II",
        "NIKON Z 8": "Z8",
        "FC3411": "AIR2S",
        "iPhone 14 Pro": "IP14P",
        "HERO10 Black": "HERO10",
    }


def test_dataclasses_are_frozen(home):
    cfg = load_config()
    with pytest.raises(AttributeError):
        cfg.exiftool_workers = 2  # type: ignore[misc]


def test_photovault_home_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "elsewhere"))
    assert default_home() == tmp_path / "elsewhere"
    assert load_config().catalog_path == tmp_path / "elsewhere" / "catalog.sqlite"


def test_default_home_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PHOTOVAULT_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_home() == tmp_path / "Library" / "Application Support" / "PhotoVault"


def test_home_config_file_is_picked_up(home):
    write(home, '[performance]\nexiftool_workers = 2\n')
    assert load_config().exiftool_workers == 2


def test_explicit_path_is_used(home, tmp_path):
    other = tmp_path / "other.toml"
    other.write_text('[ingest]\nworking_subdir = "Imports2"\n', encoding="utf-8")
    assert load_config(other).ingest.working_subdir == "Imports2"


def test_explicit_missing_path_raises(home, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_ingest_override(home):
    write(home, '[ingest]\nworking_subdir = "Photography/Cards"\n')
    assert load_config().ingest == IngestConfig(working_subdir="Photography/Cards")


def test_naming_override(home):
    write(home, '[naming]\ntemplate = "{body}_{ident}_{num:04d}"\nday_boundary_hour = 4\n')
    cfg = load_config()
    assert cfg.naming == NamingConfig(template="{body}_{ident}_{num:04d}", day_boundary_hour=4)


def test_policy_override(home):
    write(home, '[policy]\nrequired = { WORKING = 1, MASTER = 1 }\n')
    assert load_config().policy == PolicyConfig(required={"WORKING": 1, "MASTER": 1})


def test_performance_override(home):
    write(home, "[performance]\nexiftool_workers = 8\n")
    assert load_config().exiftool_workers == 8


def test_bodies_merge_over_defaults(home):
    write(home, '[bodies]\n"NIKON Z 9" = "Z9"\n"NIKON Z 6" = "Zsix"\n')
    cfg = load_config()
    assert cfg.bodies["NIKON Z 9"] == "Z9"
    assert cfg.bodies["NIKON Z 6"] == "Zsix"  # file wins
    assert cfg.bodies["NIKON Z 8"] == "Z8"  # defaults kept
    assert len(cfg.bodies) == len(DEFAULT_BODIES) + 1


def test_bad_day_boundary_hour(home):
    write(home, "[naming]\nday_boundary_hour = 24\n")
    with pytest.raises(ConfigError, match="day_boundary_hour"):
        load_config()


def test_bad_day_boundary_hour_negative(home):
    write(home, "[naming]\nday_boundary_hour = -1\n")
    with pytest.raises(ConfigError, match="day_boundary_hour"):
        load_config()


def test_bad_policy_role(home):
    write(home, '[policy]\nrequired = { SHELF = 1 }\n')
    with pytest.raises(ConfigError, match="SHELF"):
        load_config()


def test_bad_policy_count(home):
    write(home, "[policy]\nrequired = { WORKING = 0 }\n")
    with pytest.raises(ConfigError, match="required.WORKING"):
        load_config()


def test_template_missing_body(home):
    write(home, '[naming]\ntemplate = "M{num:04d}_{ident}"\n')
    with pytest.raises(ConfigError, match="{body}"):
        load_config()


def test_template_missing_ident(home):
    write(home, '[naming]\ntemplate = "M{num:04d}_{body}"\n')
    with pytest.raises(ConfigError, match="{ident}"):
        load_config()


@pytest.mark.parametrize("value", [0, 9])
def test_exiftool_workers_out_of_range(home, value):
    write(home, f"[performance]\nexiftool_workers = {value}\n")
    with pytest.raises(ConfigError, match="exiftool_workers"):
        load_config()


def test_unknown_top_level_section(home):
    write(home, "[surprise]\nx = 1\n")
    with pytest.raises(ConfigError, match=r"\[surprise\]"):
        load_config()


def test_unknown_key_in_section(home):
    write(home, "[ingest]\nworking_subdr = 'typo'\n")
    with pytest.raises(ConfigError, match="working_subdr"):
        load_config()


def test_wrong_type_names_the_key(home):
    write(home, "[performance]\nexiftool_workers = 'four'\n")
    with pytest.raises(ConfigError, match="exiftool_workers"):
        load_config()


def test_invalid_toml(home):
    write(home, "[ingest\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config()


def test_example_file_matches_defaults(home, tmp_path):
    """Uncomment the example's setting lines ("# key = value") and check they load to the defaults."""
    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        lines.append(line[2:] if line.startswith("# ") else line)
    path = tmp_path / "example.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cfg = load_config(path)
    defaults = load_config()
    assert cfg.ingest == defaults.ingest
    assert cfg.naming == defaults.naming
    assert cfg.policy == defaults.policy
    assert cfg.bodies == defaults.bodies
    assert cfg.exiftool_workers == defaults.exiftool_workers
