"""Transcription : repli GPU → processeur et isolation dans un processus enfant.

Aucun modèle n'est chargé : les corps d'enfant ci-dessous imitent un succès,
une erreur Python et un plantage natif.
"""
from __future__ import annotations

import os

import pytest

from engine.pipeline import transcribe as T


def _child_ok(conn, *args):
    conn.send(("ok", ([("salut", 0.0, 0.4), ("toi", 0.5, 0.9)],
                      {"language": "fr", "device": "cuda"})))
    conn.close()


def _child_error(conn, *args):
    conn.send(("error", "RuntimeError: CUDA failed with error out of memory"))
    conn.close()


def _child_crash(conn, *args):
    os._exit(3)  # mort brutale, sans rien renvoyer (comme un plantage de DLL)


def test_isolated_rapporte_les_mots():
    info = {}
    words = T._isolated("x.mp4", "tiny", "cuda", "float16", None, info, target=_child_ok)
    assert [(w.text, w.start, w.end) for w in words] == [("salut", 0.0, 0.4), ("toi", 0.5, 0.9)]
    assert info == {"language": "fr", "device": "cuda"}


def test_isolated_relaie_une_erreur_python():
    with pytest.raises(RuntimeError, match="out of memory"):
        T._isolated("x.mp4", "tiny", "cuda", "float16", None, None, target=_child_error)


def test_isolated_transforme_un_plantage_en_erreur():
    with pytest.raises(RuntimeError, match="arrêté brutalement"):
        T._isolated("x.mp4", "tiny", "cuda", "float16", None, None, target=_child_crash)


@pytest.fixture
def gpu_en_panne(monkeypatch):
    """GPU détecté, mais la transcription GPU échoue ; le processeur répond."""
    calls = []

    def isolated(path, model, device, ct, lang, info, target=None):
        calls.append((device, ct))
        raise RuntimeError("le moteur de transcription s'est arrêté brutalement")

    def run(path, model, device, ct, lang, info):
        calls.append((device, ct))
        return [T.Word(text="ok", start=0.0, end=0.1)]

    monkeypatch.setattr(T, "resolve_device", lambda d="auto": "cuda" if d != "cpu" else "cpu")
    monkeypatch.setattr(T, "_isolated", isolated)
    monkeypatch.setattr(T, "_run", run)
    return calls


def test_auto_se_replie_sur_le_processeur(gpu_en_panne):
    words = T.transcribe("x.mp4", device="auto")
    assert [w.text for w in words] == ["ok"]
    assert gpu_en_panne == [("cuda", "float16"), ("cpu", "int8")]


def test_cuda_explicite_laisse_remonter_l_erreur(gpu_en_panne):
    with pytest.raises(RuntimeError, match="brutalement"):
        T.transcribe("x.mp4", device="cuda")
    assert gpu_en_panne == [("cuda", "float16")]


def test_cpu_ne_passe_pas_par_le_gpu(gpu_en_panne):
    T.transcribe("x.mp4", device="cpu", compute_type="float16")
    assert gpu_en_panne == [("cpu", "int8")]


@pytest.mark.parametrize("device, requested, expected", [
    ("cuda", "auto", "float16"), ("cpu", "auto", "int8"),
    ("cpu", "float16", "int8"), ("cuda", "int8_float16", "int8_float16"),
])
def test_resolve_compute_type(device, requested, expected):
    assert T.resolve_compute_type(device, requested) == expected
