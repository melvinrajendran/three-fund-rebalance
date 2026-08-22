import pytest


@pytest.fixture(autouse=True)
def fixed_terminal_width(monkeypatch):
    """Pin the terminal to 80 columns for every test.

    Output widths follow the terminal now, and `shutil.get_terminal_size`
    reads the real one when $COLUMNS is unset -- so without this, assertions
    about wrapping would pass in CI and fail for anyone running pytest in a
    wide window. Tests that are *about* width override it with their own
    `monkeypatch.setenv`, which wins because it runs after this.
    """
    monkeypatch.setenv("COLUMNS", "80")
