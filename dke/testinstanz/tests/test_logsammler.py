"""Eigene Tests fuer den Logsammler (Auftrag C1, 4.4)."""
import datetime
import importlib.util
import json
import pathlib
import shutil
import sys
import time

import pytest

_PFAD = pathlib.Path(__file__).resolve().parents[1] / "logsammler" / "logsammler.py"
_spec = importlib.util.spec_from_file_location("logsammler", _PFAD)
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)

MEZ = datetime.timezone(datetime.timedelta(hours=1))
JETZT = datetime.datetime(2026, 11, 17, 12, 0, tzinfo=MEZ)

# cat-Ersatz (cat liegt unter Windows nicht auf dem PATH): gibt die Dateien als Bytes aus, Exit 1 bei
# fehlender Datei; schreibt jeden Aufruf in die Datei aus OWB_TEST_AUFRUFE, wenn gesetzt.
_KATZE_CODE = (
    "import os, sys\n"
    "p = os.environ.get('OWB_TEST_AUFRUFE')\n"
    "if p:\n"
    "    open(p, 'a').write(repr(sys.argv[1:]) + '\\n')\n"
    "for f in sys.argv[1:]:\n"
    "    with open(f, 'rb') as h:\n"
    "        sys.stdout.buffer.write(h.read())\n"
)
KATZE = [sys.executable, "-c", _KATZE_CODE]


def zeile(i, vorsilbe="zeile"):
    return ("2026-11-17 12:00:00,000 - {main:1} - {INFO:MainThread} - %s %06d\n" % (vorsilbe, i)).encode()


def bloecke(von, bis, vorsilbe="zeile"):
    return b"".join(zeile(i, vorsilbe) for i in range(von, bis))


class Quelle:
    """main.log.1 und main.log in tmp_path."""

    def __init__(self, tmp_path):
        self.alt = tmp_path / "main.log.1"
        self.neu = tmp_path / "main.log"
        self.alt.write_bytes(b"")
        self.neu.write_bytes(b"")

    def setze(self, alt, neu):
        self.alt.write_bytes(alt)
        self.neu.write_bytes(neu)


@pytest.fixture
def quelle(tmp_path):
    return Quelle(tmp_path)


def konfig(tmp_path, quelle, **mehr):
    k = {"befehl": list(KATZE), "dateien": [str(quelle.alt), str(quelle.neu)], "ablage": str(tmp_path / "ablage")}
    k.update(mehr)
    return k


def ereignisse(k):
    p = pathlib.Path(k["ablage"]) / "sammler.jsonl"
    return [json.loads(z) for z in p.read_text(encoding="ascii").splitlines()] if p.exists() else []


def tagesdatei(k, jetzt=JETZT):
    return pathlib.Path(k["ablage"]) / ("main-%s.txt" % jetzt.date().isoformat())


def test_erster_lauf(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 10)
    ev = ereignisse(k)
    assert [e["ereignis"] for e in ev] == ["erstlauf", "abruf"]
    assert ev[-1]["neu"] == 10
    assert ev[0]["zeit"].endswith("+01:00")


def test_teilzeile(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 5) + b"halb")
    k = konfig(tmp_path, quelle)
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 5)
    quelle.setze(b"", bloecke(0, 5) + b"halbe zeile\n" + zeile(5))
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 5) + b"halbe zeile\n" + zeile(5)


def test_zweiter_lauf_nur_neue(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    quelle.setze(b"", bloecke(0, 15))
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 15)
    assert ereignisse(k)[-1]["neu"] == 5
    assert "luecke_moeglich" not in [e["ereignis"] for e in ereignisse(k)]


def test_ohne_neue_zeilen(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 10)
    assert ereignisse(k)[-1]["neu"] == 0
    quelle.setze(b"", bloecke(0, 12))
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 12)
    assert "luecke_moeglich" not in [e["ereignis"] for e in ereignisse(k)]


def test_rotation_zwischen_laeufen(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    # main.log waechst auf 13 Zeilen, rotiert nach main.log.1, neues main.log mit 4 Zeilen
    quelle.setze(bloecke(0, 13), bloecke(13, 17))
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 17)
    assert ereignisse(k)[-1]["neu"] == 7
    assert "luecke_moeglich" not in [e["ereignis"] for e in ereignisse(k)]


def test_ein_aufruf_alle_dateien(tmp_path, quelle, monkeypatch):
    aufrufe = tmp_path / "aufrufe.txt"
    monkeypatch.setenv("OWB_TEST_AUFRUFE", str(aufrufe))
    quelle.setze(bloecke(0, 3), bloecke(3, 6))
    k = konfig(tmp_path, quelle)
    assert ls.lauf(k, JETZT) == 0
    assert aufrufe.read_text().splitlines() == [repr([str(quelle.alt), str(quelle.neu)])]
    assert tagesdatei(k).read_bytes() == bloecke(0, 6)


def test_ueberlapp_erste_fundstelle(tmp_path, quelle):
    a = bloecke(0, 5, "a")
    x = bloecke(0, 3, "x")
    quelle.setze(b"", a)
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    quelle.setze(b"", a + x + a)
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == a + x + a
    assert ereignisse(k)[-1]["neu"] == 8


def test_ueberlapp_teilweise(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 50))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    # Anfang des Zustands verdraengt (zweimal rotiert), 25 Zeilen Ueberlapp bleiben
    quelle.setze(bloecke(25, 50), bloecke(50, 60))
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 60)
    assert "luecke_moeglich" not in [e["ereignis"] for e in ereignisse(k)]


def test_kurzer_ueberlapp_ist_luecke(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 50))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    # nach Neustart: die ersten 3 Zeilen gleichen zufaellig dem Ende des Zustands (unter 20 Zeilen)
    neu = bloecke(47, 50) + bloecke(0, 10, "n")
    quelle.setze(b"", neu)
    assert ls.lauf(k, JETZT) == 0
    assert "luecke_moeglich" in [e["ereignis"] for e in ereignisse(k)]
    assert tagesdatei(k).read_bytes() == bloecke(0, 50) + neu


def test_luecke(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    quelle.setze(b"", bloecke(0, 5, "n"))
    assert ls.lauf(k, JETZT) == 0
    assert [e["ereignis"] for e in ereignisse(k)][-2:] == ["luecke_moeglich", "abruf"]
    assert tagesdatei(k).read_bytes() == bloecke(0, 10) + bloecke(0, 5, "n")


def _unveraendert(k):
    ablage = pathlib.Path(k["ablage"])
    return {p.name: p.read_bytes() for p in ablage.iterdir() if p.name != "sammler.jsonl"}


def test_fehlschlag(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    vorher = _unveraendert(k)
    k["befehl"] = [sys.executable, "-c",
                   "import sys; sys.stdout.write('neu\\n'); sys.stderr.write('kaputt'); sys.exit(3)"]
    assert ls.lauf(k, JETZT) == 1
    assert _unveraendert(k) == vorher
    e = ereignisse(k)[-1]
    assert e["ereignis"] == "abruf_fehlgeschlagen" and e["exitcode"] == 3 and "kaputt" in e["fehlertext"]


def test_fehlende_datei_ist_fehlschlag(tmp_path, quelle):
    quelle.alt.unlink()
    quelle.neu.write_bytes(bloecke(0, 3))
    k = konfig(tmp_path, quelle)
    assert ls.lauf(k, JETZT) == 1
    assert ereignisse(k)[-1]["ereignis"] == "abruf_fehlgeschlagen"
    assert not tagesdatei(k).exists() and not (pathlib.Path(k["ablage"]) / "zustand.json").exists()


def test_zeitgrenze(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    vorher = _unveraendert(k)
    k["befehl"] = [sys.executable, "-c", "import sys, time; sys.stdout.write('neu\\n'); time.sleep(10)"]
    k["zeitgrenze_s"] = 0.5
    t0 = time.monotonic()
    assert ls.lauf(k, JETZT) == 1
    assert time.monotonic() - t0 < 3.0
    assert _unveraendert(k) == vorher
    e = ereignisse(k)[-1]
    assert (e["ereignis"], e["exitcode"], e["grund"]) == ("abruf_fehlgeschlagen", None, "zeitgrenze")


def test_zustand_nach_anhaengen(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    zustand = (pathlib.Path(k["ablage"]) / "zustand.json").read_bytes()
    morgen = JETZT + datetime.timedelta(days=1)
    tagesdatei(k, morgen).mkdir()  # Anhaengen muss scheitern
    quelle.setze(b"", bloecke(0, 15))
    with pytest.raises(OSError):
        ls.lauf(k, morgen)
    assert (pathlib.Path(k["ablage"]) / "zustand.json").read_bytes() == zustand


def test_zustand_atomar(tmp_path, quelle, monkeypatch):
    aufrufe = []
    echt = ls.os.replace

    def spion(quelle_pfad, ziel_pfad):
        aufrufe.append((str(quelle_pfad), str(ziel_pfad)))
        return echt(quelle_pfad, ziel_pfad)
    monkeypatch.setattr(ls.os, "replace", spion)
    quelle.setze(b"", bloecke(0, 10))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    ziel = str(pathlib.Path(k["ablage"]) / "zustand.json")
    assert aufrufe == [(ziel + ".tmp", ziel)]
    assert not pathlib.Path(ziel + ".tmp").exists()
    assert len(json.loads(pathlib.Path(ziel).read_text())["zeilen"]) == 10


def test_zustand_200_zeilen(tmp_path, quelle):
    quelle.setze(b"", bloecke(0, 450))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, JETZT)
    zustand = json.loads((pathlib.Path(k["ablage"]) / "zustand.json").read_text())["zeilen"]
    assert zustand == [ls._hash(zeile(i)) for i in range(250, 450)]


def test_tageswechsel(tmp_path, quelle):
    abends = datetime.datetime(2026, 11, 17, 23, 59, tzinfo=MEZ)
    morgens = datetime.datetime(2026, 11, 18, 0, 1, tzinfo=MEZ)
    quelle.setze(b"", bloecke(0, 5))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, abends)
    quelle.setze(b"", bloecke(0, 8))
    ls.lauf(k, morgens)
    assert tagesdatei(k, abends).read_bytes() == bloecke(0, 5)
    assert tagesdatei(k, morgens).read_bytes() == bloecke(5, 8)


def test_datum_ortszeit(tmp_path, quelle):
    nach_mitternacht = datetime.datetime(2026, 11, 17, 0, 30, tzinfo=MEZ)  # UTC: 16.11.
    quelle.setze(b"", bloecke(0, 2))
    k = konfig(tmp_path, quelle)
    ls.lauf(k, nach_mitternacht)
    assert sorted(p.name for p in pathlib.Path(k["ablage"]).glob("main-*.txt")) == ["main-2026-11-17.txt"]


def test_aufbewahrung(tmp_path, quelle):
    ablage = tmp_path / "ablage"
    ablage.mkdir()
    for name in ("main-2026-11-02.txt", "main-2026-11-03.txt", "main-2026-10-01.txt", "main-notiz.txt",
                 "main-2026-13-45.txt", "main-2026-10-01.txt.bak", "anders-2026-10-01.txt"):
        (ablage / name).write_bytes(b"x\n")
    quelle.setze(b"", bloecke(0, 2))
    k = konfig(tmp_path, quelle)
    assert ls.lauf(k, JETZT) == 0
    rest = sorted(p.name for p in ablage.iterdir())
    assert rest == sorted(["main-2026-11-03.txt", "main-notiz.txt", "main-2026-13-45.txt",
                           "main-2026-10-01.txt.bak", "anders-2026-10-01.txt", "main-2026-11-17.txt",
                           "sammler.jsonl", "zustand.json"])
    auf = [e for e in ereignisse(k) if e["ereignis"] == "aufbewahrung"]
    assert auf[-1]["geloescht"] == ["main-2026-10-01.txt", "main-2026-11-02.txt"]


@pytest.mark.parametrize("aendern", [
    lambda k: k.pop("befehl"),
    lambda k: k.pop("dateien"),
    lambda k: k.pop("ablage"),
    lambda k: k.update(befehl=[]),
    lambda k: k.update(befehl=["ssh", ""]),
    lambda k: k.update(dateien=[]),
    lambda k: k.update(dateien=["a", "a"]),
    lambda k: k.update(zeitgrenze_s=0),
    lambda k: k.update(aufbewahrung_tage=0),
    lambda k: k.update(aufbewahrung_tage=1.5),
    lambda k: k.update(unbekannt=1),
])
def test_konfig_abgelehnt(aendern):
    k = {"befehl": ["cat"], "dateien": ["a", "b"], "ablage": "x"}
    ls.pruefe_konfig(dict(k))
    aendern(k)
    with pytest.raises(ValueError):
        ls.pruefe_konfig(k)


@pytest.mark.skipif(shutil.which("cat") is None, reason="cat nicht auf dem PATH (Windows)")
def test_echtes_cat(tmp_path, quelle):
    quelle.setze(bloecke(0, 3), bloecke(3, 6))
    k = konfig(tmp_path, quelle, befehl=["cat"])
    assert ls.lauf(k, JETZT) == 0
    assert tagesdatei(k).read_bytes() == bloecke(0, 6)
