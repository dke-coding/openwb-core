"""Eigene Tests fuer den Lesebefehl owb-log-lesen.sh (Auftrag C1, Entscheidung D-2 A2)."""
import datetime
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

_BASIS = pathlib.Path(__file__).resolve().parents[1]
SKRIPT = _BASIS / "logsammler" / "owb-log-lesen.sh"
_spec = importlib.util.spec_from_file_location("logsammler", _BASIS / "logsammler" / "logsammler.py")
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)


def _bash():
    if os.name == "nt":
        p = r"C:\Program Files\Git\bin\bash.exe"
        return p if os.path.exists(p) else None
    return shutil.which("bash")


BASH = _bash()
braucht_bash = pytest.mark.skipif(BASH is None, reason="bash nicht gefunden")


def _lauf(verzeichnis, *argumente, umgebung=None):
    env = dict(os.environ)
    env["OWB_LOG_VERZEICHNIS"] = pathlib.Path(verzeichnis).as_posix()
    env.update(umgebung or {})
    return subprocess.run([BASH, str(SKRIPT)] + list(argumente), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=30)


@braucht_bash
def test_beide_dateien_in_reihenfolge(tmp_path):
    (tmp_path / "main.log.1").write_bytes(b"alt 1\nalt 2\n")
    (tmp_path / "main.log").write_bytes(b"neu 1\n")
    r = _lauf(tmp_path)
    assert (r.returncode, r.stdout) == (0, b"alt 1\nalt 2\nneu 1\n")


@braucht_bash
def test_fehlendes_main_log_1_wird_uebergangen(tmp_path):
    (tmp_path / "main.log").write_bytes(b"neu 1\n")
    r = _lauf(tmp_path)
    assert (r.returncode, r.stdout) == (0, b"neu 1\n")


@braucht_bash
def test_fehlendes_main_log_exit_ungleich_0(tmp_path):
    (tmp_path / "main.log.1").write_bytes(b"alt 1\n")
    r = _lauf(tmp_path)
    assert r.returncode != 0
    assert r.stdout == b""
    assert b"main.log" in r.stderr


@braucht_bash
def test_argumente_und_original_command_ignoriert(tmp_path):
    (tmp_path / "main.log").write_bytes(b"neu 1\n")
    probe = tmp_path / "anders" / "probe.txt"
    probe.parent.mkdir()
    probe.write_bytes(b"GEHEIM\n")
    r = _lauf(tmp_path, probe.as_posix(), umgebung={"SSH_ORIGINAL_COMMAND": "cat " + probe.as_posix()})
    assert (r.returncode, r.stdout) == (0, b"neu 1\n")
    assert b"GEHEIM" not in r.stderr


@braucht_bash
def test_bash_n():
    r = subprocess.run([BASH, "-n", str(SKRIPT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    assert r.returncode == 0, r.stderr


def test_nur_lesen_statisch():
    text = SKRIPT.read_text(encoding="ascii")
    code = [z for z in text.splitlines() if z.strip() and not z.lstrip().startswith("#")]
    assert text.startswith("#!/bin/bash\n")
    assert "/var/www/html/openWB/ramdisk" in text
    for z in code:
        # einzige Umleitung: Meldung nach stderr
        assert ">" not in z.replace(">&2", ""), z
        assert not re.search(r"\b(rm|mv|cp|tee|touch|dd|curl|wget|ssh|scp|nc|sed|chmod|chown)\b", z), z
        assert "SSH_ORIGINAL_COMMAND" not in z and "$@" not in z and not re.search(r"\$[1-9*]", z), z


@braucht_bash
def test_logsammler_mit_lesebefehl_ohne_main_log_1(tmp_path, monkeypatch):
    ramdisk = tmp_path / "ramdisk"
    ramdisk.mkdir()
    (ramdisk / "main.log").write_bytes(b"zeile 1\nzeile 2\n")
    monkeypatch.setenv("OWB_LOG_VERZEICHNIS", ramdisk.as_posix())
    k = {"befehl": [BASH, str(SKRIPT)],
         "dateien": ["/var/www/html/openWB/ramdisk/main.log.1", "/var/www/html/openWB/ramdisk/main.log"],
         "ablage": str(tmp_path / "ablage")}
    jetzt = datetime.datetime(2026, 11, 17, 12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))
    assert ls.lauf(k, jetzt) == 0
    ev = [json.loads(z)["ereignis"] for z in (tmp_path / "ablage" / "sammler.jsonl").read_text().splitlines()]
    assert "abruf" in ev and "abruf_fehlgeschlagen" not in ev
    assert (tmp_path / "ablage" / "main-2026-11-17.txt").read_bytes() == b"zeile 1\nzeile 2\n"
