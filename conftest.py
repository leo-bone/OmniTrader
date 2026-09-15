"""Shared pytest fixtures.

Pins pytest's temporary directory to a repo-local `.pytest_tmp/`.

Why: some sandboxed CI runners and locked-down macOS shells refuse to create
pytest's usual base directory under `/var/folders` (they surface it as a
confusing `PermissionError: EEXIST` on every test that asks for `tmp_path`,
even for `mkdir(..., exist_ok=True)`). Pinning the location makes the suite run
identically everywhere, and keeps all test artefacts inside the checkout so they
can be gitignored and cleaned up.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(__file__).parent / ".pytest_tmp"

# start each session clean so a previous crash can't poison new fixtures
if _TMP.exists():
    shutil.rmtree(_TMP, ignore_errors=True)
_TMP.mkdir(parents=True, exist_ok=True)

os.environ["TMPDIR"] = str(_TMP)
# `tempfile.tempdir` wins over TMPDIR in every code path that asks for it
tempfile.tempdir = str(_TMP)
