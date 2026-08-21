"""Root conftest — exists so the suite runs the same way however you invoke it.

Ten test modules do `from tests.conftest import Server` to reuse the localhost
server helper. That import needs the repository root on `sys.path`.

`python -m pytest` puts the working directory there as a side effect of `-m`,
so it worked locally and nowhere else. The `pytest` console script does not,
which is how CI ran it — and every one of those modules failed to import.

pytest prepends the directory of each `conftest.py` it collects, so this file
being here is the fix. It deliberately contains nothing else: the real fixtures
live in `tests/conftest.py`.
"""
