"""Tests JavaScript du studio (logique de timeline), lancés avec `node --test`."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js introuvable dans le PATH")
def test_logique_de_timeline():
    files = sorted(glob.glob(os.path.join(HERE, "js", "*.test.mjs")))
    assert files
    res = subprocess.run(["node", "--test", *files], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
    failed = [line for line in res.stdout.splitlines() if line.startswith("not ok")]
    assert res.returncode == 0, "\n".join(failed) + "\n" + res.stdout[-3000:] + res.stderr[-2000:]
