#!/usr/bin/env python3
"""Logsammler der openWB-Testinstanz (Gate 1), ein Lauf je Aufruf (systemd-Timer alle 60 s).

Ruft befehl + dateien in EINEM Aufruf auf (Betrieb: ssh ... <lesebefehl>, Test: cat) und haengt nur die
neuen, vollstaendigen Zeilen an <ablage>/main-<JJJJ-MM-TT>.txt (Datum Ortszeit des Laufs) an. Neue Zeilen
erkennt er am Ueberlapp mit den zuletzt abgelegten 200 Zeilen (Zustand <ablage>/zustand.json, als sha256
je Zeile). Ereignisse als JSON-Zeilen in <ablage>/sammler.jsonl. Loescht nur main-<Datum>.txt, die
aelter als aufbewahrung_tage sind.

Nur Standardbibliothek. Aufruf: python3 logsammler.py --konfig <json>
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

ZUSTAND_ZEILEN = 200
MIN_TEILUEBERLAPP = 20
FEHLERTEXT_MAX = 500
_TAGESDATEI = re.compile(r"main-(\d{4}-\d{2}-\d{2})\.txt")
_SCHLUESSEL = {"befehl", "dateien", "ablage", "zeitgrenze_s", "aufbewahrung_tage"}


def pruefe_konfig(k):
    if not isinstance(k, dict):
        raise ValueError("Konfig ist kein Objekt")
    unbekannt = set(k) - _SCHLUESSEL
    if unbekannt:
        raise ValueError("unbekannte Schluessel: " + ", ".join(sorted(unbekannt)))
    for feld in ("befehl", "dateien"):
        liste = k.get(feld)
        if not isinstance(liste, list) or not liste:
            raise ValueError(feld + " fehlt oder leer")
        if not all(isinstance(x, str) and x for x in liste):
            raise ValueError(feld + " enthaelt leere oder ungueltige Eintraege")
    if len(set(k["dateien"])) != len(k["dateien"]):
        raise ValueError("dateien enthaelt Dopplungen")
    if not isinstance(k.get("ablage"), str) or not k["ablage"]:
        raise ValueError("ablage fehlt")
    zeitgrenze = k.get("zeitgrenze_s", 30)
    if isinstance(zeitgrenze, bool) or not isinstance(zeitgrenze, (int, float)) or zeitgrenze <= 0:
        raise ValueError("zeitgrenze_s muss > 0 sein")
    tage = k.get("aufbewahrung_tage", 14)
    if isinstance(tage, bool) or not isinstance(tage, int) or tage < 1:
        raise ValueError("aufbewahrung_tage muss eine ganze Zahl >= 1 sein")
    return k


def _jetzt():
    return datetime.datetime.now().astimezone()


def _ereignis(ablage, jetzt, ereignis, **felder):
    eintrag = {"zeit": jetzt.isoformat(timespec="milliseconds"), "ereignis": ereignis}
    eintrag.update(felder)
    with open(os.path.join(ablage, "sammler.jsonl"), "a", encoding="ascii", newline="\n") as f:
        f.write(json.dumps(eintrag, ensure_ascii=True, separators=(",", ":")) + "\n")


def zeilen(daten):
    """Vollstaendige Zeilen (mit Zeilenende); ein Rest ohne Zeilenende wird verworfen."""
    teile = daten.split(b"\n")
    return [t + b"\n" for t in teile[:-1]]


def _hash(zeile):
    return hashlib.sha256(zeile).hexdigest()


def neue_zeilen(alt, aktuell):
    """alt: Hashes der zuletzt abgelegten Zeilen, aktuell: Hashes der gelesenen Zeilen.

    Rueckgabe (start, ueberlapp): ab Index start sind die Zeilen neu; ueberlapp False heisst Luecke moeglich.
    """
    m = len(alt)
    if m == 0:
        return 0, False
    erste = alt[0]
    for i in range(0, len(aktuell) - m + 1):
        if aktuell[i] == erste and aktuell[i:i + m] == alt:
            return i + m, True
    for p in range(min(m, len(aktuell)), 0, -1):
        if p < min(m, MIN_TEILUEBERLAPP):
            break
        if aktuell[:p] == alt[m - p:]:
            return p, True
    return 0, False


def _lies_zustand(pfad):
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="ascii") as f:
        zustand = json.load(f)
    return list(zustand["zeilen"])


def _schreibe_zustand(pfad, hashes):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as f:
        json.dump({"version": 1, "zeilen": hashes}, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pfad)


def _aufraeumen(ablage, jetzt, tage):
    heute = jetzt.date()
    geloescht = []
    for name in sorted(os.listdir(ablage)):
        treffer = _TAGESDATEI.fullmatch(name)
        if not treffer:
            continue
        try:
            datum = datetime.date.fromisoformat(treffer.group(1))
        except ValueError:
            continue
        if (heute - datum).days > tage:
            os.remove(os.path.join(ablage, name))
            geloescht.append(name)
    if geloescht:
        _ereignis(ablage, jetzt, "aufbewahrung", geloescht=geloescht)


def lauf(k, jetzt=None):
    """Ein Lauf. Rueckgabe: Exit-Code (0 Erfolg, 1 Abruf fehlgeschlagen)."""
    pruefe_konfig(k)
    jetzt = jetzt or _jetzt()
    ablage = k["ablage"]
    os.makedirs(ablage, exist_ok=True)
    try:
        ergebnis = subprocess.run(list(k["befehl"]) + list(k["dateien"]), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=k.get("zeitgrenze_s", 30))
    except subprocess.TimeoutExpired:
        _ereignis(ablage, jetzt, "abruf_fehlgeschlagen", exitcode=None, grund="zeitgrenze")
        return 1
    except OSError as fehler:
        _ereignis(ablage, jetzt, "abruf_fehlgeschlagen", exitcode=None, grund="start",
                  fehlertext=str(fehler)[:FEHLERTEXT_MAX])
        return 1
    if ergebnis.returncode != 0:
        fehlertext = ergebnis.stderr.decode("utf-8", errors="replace")[-FEHLERTEXT_MAX:]
        _ereignis(ablage, jetzt, "abruf_fehlgeschlagen", exitcode=ergebnis.returncode, fehlertext=fehlertext)
        return 1

    gelesen = zeilen(ergebnis.stdout)
    hashes = [_hash(z) for z in gelesen]
    zustand_pfad = os.path.join(ablage, "zustand.json")
    alt = _lies_zustand(zustand_pfad)
    tagesdatei = "main-%s.txt" % jetzt.date().isoformat()
    if not gelesen:
        _ereignis(ablage, jetzt, "abruf", neu=0, datei=tagesdatei)
        _aufraeumen(ablage, jetzt, k.get("aufbewahrung_tage", 14))
        return 0
    if alt is None:
        start = 0
        _ereignis(ablage, jetzt, "erstlauf", zeilen=len(gelesen))
    else:
        start, ueberlapp = neue_zeilen(alt, hashes)
        if not ueberlapp:
            _ereignis(ablage, jetzt, "luecke_moeglich", zeilen=len(gelesen))
    neu = gelesen[start:]
    if neu:
        with open(os.path.join(ablage, tagesdatei), "ab") as f:
            f.write(b"".join(neu))
            f.flush()
            os.fsync(f.fileno())
    _schreibe_zustand(zustand_pfad, hashes[-ZUSTAND_ZEILEN:])
    _ereignis(ablage, jetzt, "abruf", neu=len(neu), datei=tagesdatei)
    _aufraeumen(ablage, jetzt, k.get("aufbewahrung_tage", 14))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Logsammler der openWB-Testinstanz")
    parser.add_argument("--konfig", required=True)
    args = parser.parse_args(argv)
    with open(args.konfig, encoding="utf-8") as f:
        k = json.load(f)
    return lauf(k)


if __name__ == "__main__":
    sys.exit(main())
