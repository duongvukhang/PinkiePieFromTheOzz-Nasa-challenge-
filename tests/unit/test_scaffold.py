"""Scaffold import sanity checks (no business logic)."""


def test_scaffold_packages_importable() -> None:
    """Ensure backend and pure science package roots import cleanly."""
    import app
    import sphx_core

    assert app.__name__ == "app"
    assert sphx_core.__name__ == "sphx_core"
