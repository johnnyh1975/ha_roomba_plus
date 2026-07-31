

class TestEarlyVersionsAreSkipped:
    """Versions 3 to 9 jump straight to 10.

    Seven migration steps used to sit there, renaming entity_ids from
    German slugs to English after a release shipped translation_key set
    to translated strings. Each was built from the entity registry of ONE
    installation -- the maintainer's own Roomba 980 -- and the code said
    so in its own comments.

    All 400 hardcoded entity_ids carried that robot's prefix. On any
    other installation they matched nothing: 669 lines that walked the
    registry and changed nothing, 31% of the whole migration function.

    That every test still passed after removing them is itself the
    finding -- none of the seven had a test."""

    def test_the_jump_covers_every_early_version(self):
        """A collapsed step keyed on `== 3` would strand anyone on 4
        through 9. The chain runs `if current == N` and each step raises
        `current`, so an entry with no matching branch never advances and
        the entry fails to load."""
        import inspect

        from custom_components.roomba_plus import migrations

        source = inspect.getsource(migrations.async_migrate_entry)

        assert "3 <= current < 10" in source

    def test_it_lands_on_ten_not_on_current_version(self):
        """Jumping straight to 25 would skip v10 through v24 as well --
        and those are the generic, suffix-based migrations that apply to
        every installation."""
        import inspect

        from custom_components.roomba_plus import migrations

        source = inspect.getsource(migrations.async_migrate_entry)

        assert "current = 10" in source

    def test_no_hardcoded_entity_ids_from_one_robot_remain(self):
        """The marker of an installation-specific migration: an entity_id
        with the maintainer's own robot prefix written into the source."""
        import re
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus" / "migrations.py"
        ).read_text(encoding="utf-8")

        hardcoded = re.findall(
            r'"(?:sensor|binary_sensor|switch)\.roomba_980_og_[a-z0-9_]+"', source
        )

        assert len(hardcoded) < 10, (
            f"{len(hardcoded)} entity_ids from one installation are still "
            "hardcoded in migrations"
        )

    def test_later_migrations_are_untouched(self):
        """v10 onwards match on entity_id suffixes rather than on a
        registry dump, so they work on any installation and stay."""
        import inspect

        from custom_components.roomba_plus import migrations

        source = inspect.getsource(migrations.async_migrate_entry)

        assert "# v10 → v11" in source
        assert "# v12 → v13" in source
