"""Eigene Tests fuer die Dienst-Einstiege und die Beispielkonfigurationen (Auftrag C1, 4.7 und 4.8)."""
import http.client
import http.server
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mqtt_pruefbroker import PUBLISH, SUBSCRIBE, Pruefbroker  # noqa: E402

BASIS = pathlib.Path(__file__).resolve().parents[1]
KONFIG = BASIS / "konfig"


def _lade(name):
    spec = importlib.util.spec_from_file_location(name, BASIS / name / (name + ".py"))
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


lf, sp, bb, ls = _lade("lesefilter"), _lade("spiegel"), _lade("beobachter"), _lade("logsammler")


def _beispiel(name):
    return json.loads((KONFIG / (name + ".beispiel.json")).read_text(encoding="ascii"))


def _warte(bedingung, s=10.0):
    ende = time.monotonic() + s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return bedingung()


def _jsonl(pfad):
    p = pathlib.Path(pfad)
    return [json.loads(z) for z in p.read_text(encoding="ascii").splitlines()] if p.exists() else []


def _freier_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Dienst:
    def __init__(self, name, konfig, tmp_path):
        pfad = tmp_path / (name + ".json")
        pfad.write_text(json.dumps(konfig), encoding="ascii")
        self.proc = subprocess.Popen([sys.executable, str(BASIS / name / (name + ".py")), "--konfig", str(pfad)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def stopp(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        return self.proc.stderr.read()


@pytest.mark.parametrize("name,modul", [("lesefilter", lf), ("spiegel", sp), ("beobachter", bb), ("logsammler", ls)])
def test_beispielkonfig_gueltig(name, modul):
    assert modul.pruefe_konfig(_beispiel(name))


def test_beispiel_dienstpfade():
    for name in ("lesefilter", "spiegel", "beobachter"):
        assert _beispiel(name)["protokoll"].startswith("/var/log/owb-testinstanz/%s/" % name)
    assert _beispiel("logsammler")["ablage"] == "/var/log/owb-testinstanz/logsammler"


class _Station:
    def __init__(self, kennung):
        self.anfragen = []
        aussen = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                aussen.anfragen.append(self.path)
                body = json.dumps({"station": kennung}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stopp(self):
        self.server.shutdown()
        self.server.server_close()


def _hole(port, pfad):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", pfad)
    r = c.getresponse()
    daten = (r.status, r.read())
    c.close()
    return daten


def test_lesefilter_main_zwei_endpunkte(tmp_path):
    a, b = _Station("a"), _Station("b")
    pa, pb = _freier_port(), _freier_port()
    k = _beispiel("lesefilter")
    k["endpunkte"] = [{"lauschen": "127.0.0.1", "port": pa, "ziel": "127.0.0.1", "ziel_port": a.port},
                      {"lauschen": "localhost", "port": pb, "ziel": "127.0.0.1", "ziel_port": b.port}]
    k["protokoll"] = str(tmp_path / "zugriffe.jsonl")
    dienst = _Dienst("lesefilter", k, tmp_path)
    try:
        assert _warte(lambda: len([z for z in _jsonl(k["protokoll"]) if z.get("ereignis") == "start"]) == 2)
        assert _hole(pa, "/api/status?filter=car") == (200, b'{"station": "a"}')
        assert _hole(pb, "/api/status") == (200, b'{"station": "b"}')
        assert _hole(pa, "/api/set?frc=1") == (200, b"{}")
        assert _hole(pb, "/api/set?amp=6") == (200, b"{}")
        assert _hole(pb, "/api/status/../set?frc=1")[0] == 403
    finally:
        dienst.stopp()
        a.stopp()
        b.stopp()
    assert a.anfragen == ["/api/status?filter=car"]
    assert b.anfragen == ["/api/status"]
    zeilen = [z for z in _jsonl(k["protokoll"]) if "entscheidung" in z]
    assert [(z["endpunkt"], z["entscheidung"]) for z in zeilen] == [
        ("127.0.0.1", "status"), ("localhost", "status"), ("127.0.0.1", "set"), ("localhost", "set"),
        ("localhost", "verboten")]


def test_spiegel_main_mit_beispielkonfig(tmp_path):
    q, z = Pruefbroker(), Pruefbroker()
    k = _beispiel("spiegel")
    k["quelle"].update(host="127.0.0.1", port=q.port)
    k["ziel"].update(host="127.0.0.1", port=z.port)
    k["protokoll"] = str(tmp_path / "spiegel.jsonl")
    q.veroeffentliche("openWB/counter/1/get/power", "1500.0")
    q.veroeffentliche("openWB/counter/1/get/serial_number", "geheim")
    dienst = _Dienst("spiegel", k, tmp_path)
    try:
        assert _warte(lambda: z.retained.get("openWB/mqtt/counter/11/get/power") == b"1500.0")
        q.veroeffentliche("openWB/bat/2/get/soc", "55")
        assert _warte(lambda: z.retained.get("openWB/mqtt/bat/12/get/soc") == b"55")
    finally:
        dienst.stopp()
        q.stopp()
        z.stopp()
    assert set(z.retained) == {"openWB/mqtt/counter/11/get/power", "openWB/mqtt/bat/12/get/soc"}
    for v in q.verbindungen:
        assert v.client_id == "owb-spiegel-quelle"
        assert PUBLISH not in [t for t, _ in v.pakete] and v.publish == []
        assert set(v.abos) == set(sp.zuordnung(k))
    for v in z.verbindungen:
        assert SUBSCRIBE not in [t for t, _ in v.pakete]
    assert _jsonl(k["protokoll"])[0]["ereignis"] == "verbunden"


def test_beobachter_main_mit_beispielkonfig(tmp_path):
    a, t = Pruefbroker(), Pruefbroker()
    k = _beispiel("beobachter")
    k["seiten"][0].update(host="127.0.0.1", port=a.port)
    k["seiten"][1].update(host="127.0.0.1", port=t.port)
    k["takt_s"] = 0.2
    k["protokoll"] = str(tmp_path / "beobachter.jsonl")
    a.veroeffentliche("openWB/counter/1/get/fault_state", "0")
    dienst = _Dienst("beobachter", k, tmp_path)
    try:
        assert _warte(lambda: any(z["ereignis"] == "aenderung" for z in _jsonl(k["protokoll"])))
        assert _warte(lambda: len([z for z in _jsonl(k["protokoll"]) if z["ereignis"] == "takt"]) >= 16)
    finally:
        dienst.stopp()
        a.stopp()
        t.stopp()
    for b, seite in ((a, k["seiten"][0]), (t, k["seiten"][1])):
        for v in b.verbindungen:
            assert v.client_id == seite["client_id"]
            assert PUBLISH not in [x for x, _ in v.pakete] and v.publish == []
            assert sorted(v.abos) == sorted(seite["topics"])


def test_logsammler_main_exitcode(tmp_path):
    log = tmp_path / "main.log"
    log.write_bytes(b"zeile 1\n")
    katze = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(open(sys.argv[1], 'rb').read())"]
    k = _beispiel("logsammler")
    k.update(befehl=katze, dateien=[str(log)], ablage=str(tmp_path / "ablage"))
    pfad = tmp_path / "logsammler.json"
    pfad.write_text(json.dumps(k), encoding="ascii")
    skript = str(BASIS / "logsammler" / "logsammler.py")
    r = subprocess.run([sys.executable, skript, "--konfig", str(pfad)], timeout=60)
    assert r.returncode == 0
    assert len(list((tmp_path / "ablage").glob("main-*.txt"))) == 1
    k["dateien"] = [str(tmp_path / "fehlt.log")]
    pfad.write_text(json.dumps(k), encoding="ascii")
    r = subprocess.run([sys.executable, skript, "--konfig", str(pfad)], stderr=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 1


def test_main_ohne_konfig_bricht_ab():
    for name in ("lesefilter", "spiegel", "beobachter", "logsammler"):
        r = subprocess.run([sys.executable, str(BASIS / name / (name + ".py"))], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, env=dict(os.environ))
        assert r.returncode != 0, name
