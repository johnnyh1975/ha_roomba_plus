"""The shipped blueprints, copied once, never overwritten (4.6.0, B18)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from custom_components.roomba_plus import blueprint_install as bi


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "shipped"
    source.mkdir()
    for name in ("a.yaml", "b.yaml", "c.yaml"):
        (source / name).write_text(f"blueprint: {name}\n")
    return source


class TestCopy:
    def test_new_ones_are_copied(self, tmp_path):
        root = tmp_path / "blueprints" / "automation"
        assert bi._copy(_source(tmp_path), root, set()) == ["a.yaml", "b.yaml", "c.yaml"]
        assert (root / "roomba_plus" / "a.yaml").read_text() == "blueprint: a.yaml\n"

    def test_an_existing_file_is_left_alone(self, tmp_path):
        root = tmp_path / "blueprints" / "automation"
        (root / "roomba_plus").mkdir(parents=True)
        (root / "roomba_plus" / "a.yaml").write_text("edited\n")
        assert bi._copy(_source(tmp_path), root, set()) == ["b.yaml", "c.yaml"]
        assert (root / "roomba_plus" / "a.yaml").read_text() == "edited\n"

    def test_one_imported_under_another_folder_is_not_doubled(self, tmp_path):
        root = tmp_path / "blueprints" / "automation"
        (root / "johnnyh1975").mkdir(parents=True)
        (root / "johnnyh1975" / "b.yaml").write_text("imported\n")
        assert bi._copy(_source(tmp_path), root, set()) == ["a.yaml", "c.yaml"]

    def test_one_copied_before_is_not_brought_back(self, tmp_path):
        root = tmp_path / "blueprints" / "automation"
        assert bi._copy(_source(tmp_path), root, {"a.yaml", "b.yaml"}) == ["c.yaml"]


class TestInstall:
    async def test_copied_once_then_remembered(self, hass, tmp_path):
        source = _source(tmp_path)
        hass.config.config_dir = str(tmp_path)
        with patch.object(bi, "SOURCE", source):
            assert await bi.async_install_blueprints(hass) == ["a.yaml", "b.yaml", "c.yaml"]
            (tmp_path / "blueprints" / "automation" / "roomba_plus" / "a.yaml").unlink()
            assert await bi.async_install_blueprints(hass) == []
        assert not (tmp_path / "blueprints" / "automation" / "roomba_plus" / "a.yaml").exists()

    async def test_a_failure_is_logged_not_raised(self, hass, caplog):
        with patch.object(bi, "_copy", side_effect=OSError("read-only")):
            assert await bi.async_install_blueprints(hass) == []
        assert "could not copy the shipped blueprints" in caplog.text

    def test_the_integration_ships_the_repositorys_blueprints(self):
        shipped = sorted(p.name for p in bi.SOURCE.glob("*.yaml"))
        repo = sorted(p.name for p in Path("blueprints/automation").glob("*.yaml"))
        assert shipped == repo and shipped
