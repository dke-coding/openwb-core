#!/usr/bin/env python3
"""Lesefilter fuer go-e-Ladestationen der openWB-Testinstanz (Gate 1).

Die Testinstanz schreibt je Regelzyklus GET /api/set?... an jede Ladestation. Der Filter steht davor:
- GET /api/status (ohne Query oder mit filter=[a-z0-9,]{1,200}) geht genau einmal an die Ladestation,
  ohne Kopfzeilen und Rumpf des Clients; Statuscode, Rumpf und Content-Type kommen zurueck.
- GET /api/set beantwortet der Filter selbst mit 200 und {} und spricht die Ladestation nicht an.
- alles andere: 403 und {}.
Entschieden wird auf dem rohen Ziel aus der Anfragezeile, nicht auf dem von http.server
normalisierten self.path. Je Anfrage eine JSON-Zeile im Protokoll, geschrieben vor der Antwort.

Nur Standardbibliothek. Dienst: python3 lesefilter.py --konfig <json>
"""
import argparse
import datetime
import http.client
import http.server
import json
import re
import signal
import sys
import threading
import time

PFAD_MAX = 2000
_FILTER = re.compile(r"filter=[a-z0-9,]{1,200}")
_ANTWORT_LEER = b"{}"
_ANTWORT_ZIELFEHLER = b'{"fehler":"ziel"}'
_JSON = "application/json"


def _zeit():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


class Protokoll:
    """Haengt JSON-Zeilen (ASCII) an eine Datei an, eine Sperre fuer alle Threads."""

    def __init__(self, pfad):
        self.pfad = pfad
        self._sperre = threading.Lock()

    def schreibe(self, eintrag):
        zeile = json.dumps(eintrag, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._sperre:
            with open(self.pfad, "a", encoding="ascii", newline="\n") as f:
                f.write(zeile)
                f.flush()


def entscheide(methode, roh_pfad):
    """Entscheidung auf dem rohen Ziel: ("status", query), ("set", None) oder ("verboten", None)."""
    if methode != "GET":
        return ("verboten", None)
    pfad, trenner, query = roh_pfad.partition("?")
    if pfad == "/api/set":
        return ("set", None)
    if pfad == "/api/status":
        if not trenner:
            return ("status", "")
        if _FILTER.fullmatch(query):
            return ("status", query)
    return ("verboten", None)


def ziel_pfad(query):
    return "/api/status?" + query if query else "/api/status"


def _hole_status(endpunkt, zeitgrenze_s, query):
    """Genau eine Anfrage an die Ladestation. Rueckgabe (code, rumpf, content_type) oder None bei Fehler."""
    verbindung = http.client.HTTPConnection(endpunkt["ziel"], int(endpunkt.get("ziel_port", 80)),
                                            timeout=zeitgrenze_s)
    try:
        verbindung.request("GET", ziel_pfad(query))
        antwort = verbindung.getresponse()
        rumpf = antwort.read()
        return antwort.status, rumpf, antwort.getheader("Content-Type")
    except (OSError, http.client.HTTPException):
        return None
    finally:
        verbindung.close()


def _roh_ziel(handler):
    """Zweites Wort der Anfragezeile, wie empfangen (iso-8859-1 wie http.server)."""
    roh = getattr(handler, "raw_requestline", b"") or b""
    worte = str(roh, "iso-8859-1").rstrip("\r\n").split()
    return worte[1] if len(worte) >= 2 else ""


def _baue_handler(endpunkt, zeitgrenze_s, protokoll):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "owb-lesefilter"
        sys_version = ""

        def log_message(self, format, *args):
            pass

        def _zeile(self, entscheidung, ziel_status, beginn, methode=None):
            protokoll.schreibe({
                "zeit": _zeit(),
                "endpunkt": endpunkt["lauschen"],
                "methode": methode,
                "pfad": _roh_ziel(self)[:PFAD_MAX],
                "entscheidung": entscheidung,
                "ziel_status": ziel_status,
                "dauer_ms": int((time.monotonic() - beginn) * 1000),
            })

        def _antworte(self, code, rumpf, typ):
            self.send_response(code)
            if typ is not None:
                self.send_header("Content-Type", typ)
            self.send_header("Content-Length", str(len(rumpf)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(rumpf)

        def _alle(self):
            beginn = time.monotonic()
            methode = self.command
            entscheidung, query = entscheide(methode, _roh_ziel(self))
            if entscheidung == "status":
                ergebnis = _hole_status(endpunkt, zeitgrenze_s, query)
                if ergebnis is None:
                    self._zeile("status", None, beginn, methode)
                    self._antworte(502, _ANTWORT_ZIELFEHLER, _JSON)
                else:
                    code, rumpf, typ = ergebnis
                    self._zeile("status", code, beginn, methode)
                    self._antworte(code, rumpf, typ)
            elif entscheidung == "set":
                self._zeile("set", None, beginn, methode)
                self._antworte(200, _ANTWORT_LEER, _JSON)
            else:
                self._zeile("verboten", None, beginn, methode)
                self._antworte(403, _ANTWORT_LEER, _JSON)

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self._alle
            raise AttributeError(name)

        def send_error(self, code, message=None, explain=None):
            self._zeile("abgewiesen", None, time.monotonic(), getattr(self, "command", None) or None)
            super().send_error(code, message, explain)

    return Handler


def baue_server(endpunkt, zeitgrenze_s, protokoll_pfad):
    """Server fuer einen Endpunkt, gebunden an (lauschen, port). Der Aufrufer startet serve_forever."""
    protokoll = protokoll_pfad if isinstance(protokoll_pfad, Protokoll) else Protokoll(protokoll_pfad)
    handler = _baue_handler(endpunkt, zeitgrenze_s, protokoll)
    server = http.server.ThreadingHTTPServer((endpunkt["lauschen"], int(endpunkt["port"])), handler)
    protokoll.schreibe({"zeit": _zeit(), "endpunkt": endpunkt["lauschen"], "ereignis": "start"})
    return server


def pruefe_konfig(k):
    if not isinstance(k, dict):
        raise ValueError("Konfig ist kein Objekt")
    endpunkte = k.get("endpunkte")
    if not isinstance(endpunkte, list) or not endpunkte:
        raise ValueError("endpunkte fehlt oder leer")
    gesehen = set()
    for ep in endpunkte:
        if not isinstance(ep, dict):
            raise ValueError("Endpunkt ist kein Objekt")
        for feld in ("lauschen", "ziel"):
            if not isinstance(ep.get(feld), str) or not ep[feld]:
                raise ValueError("Endpunkt ohne " + feld)
        for feld, pflicht in (("port", True), ("ziel_port", False)):
            if feld not in ep and not pflicht:
                continue
            wert = ep.get(feld)
            if isinstance(wert, bool) or not isinstance(wert, int) or not 0 <= wert <= 65535:
                raise ValueError("Endpunkt mit ungueltigem " + feld)
        schluessel = (ep["lauschen"], ep["port"])
        if ep["port"] and schluessel in gesehen:
            raise ValueError("Endpunkt doppelt")
        gesehen.add(schluessel)
    zeitgrenze = k.get("zeitgrenze_s")
    if isinstance(zeitgrenze, bool) or not isinstance(zeitgrenze, (int, float)) or zeitgrenze <= 0:
        raise ValueError("zeitgrenze_s muss > 0 sein")
    if not isinstance(k.get("protokoll"), str) or not k["protokoll"]:
        raise ValueError("protokoll fehlt")
    return k


def starte(k):
    """Je Endpunkt ein Server, jeder in einem eigenen Thread. Rueckgabe: Liste der Server."""
    pruefe_konfig(k)
    protokoll = Protokoll(k["protokoll"])
    server = []
    try:
        for ep in k["endpunkte"]:
            server.append(baue_server(ep, k["zeitgrenze_s"], protokoll))
    except Exception:
        for s in server:
            s.server_close()
        raise
    for s in server:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lesefilter fuer go-e-Ladestationen")
    parser.add_argument("--konfig", required=True)
    args = parser.parse_args(argv)
    with open(args.konfig, encoding="utf-8") as f:
        k = json.load(f)
    server = starte(k)
    ende = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: ende.set())
    try:
        while not ende.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    for s in server:
        s.shutdown()
        s.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
