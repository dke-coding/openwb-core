"""Pruefwerkzeug des Auftraggebers fuer den Lesefilter (Auftrag C1, Fassung 3). Nicht aendern."""
import http.client
import http.server
import importlib.util
import json
import pathlib
import re
import socket
import threading
import time

import pytest

_PFAD = pathlib.Path(__file__).resolve().parents[1] / "lesefilter" / "lesefilter.py"
_spec = importlib.util.spec_from_file_location("lesefilter", _PFAD)
lf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lf)

STATUS_MUSTER = re.compile(r"/api/status(\?filter=[a-z0-9,]{1,200})?")
ERLAUBTE_KOPFZEILEN = {"host", "accept-encoding", "user-agent", "connection"}

FAELLE = [
    ("GET", "/api/status", ("status", "")),
    ("GET", "/api/status?filter=car", ("status", "filter=car")),
    ("GET", "/api/status?filter=car,amp,acu,psm,frc,nrg,eto,wh,cbl,err,fwv,pha,ama,sse",
     ("status", "filter=car,amp,acu,psm,frc,nrg,eto,wh,cbl,err,fwv,pha,ama,sse")),
    ("GET", "/api/set?frc=1", ("set", None)),
    ("GET", "/api/set?amp=16", ("set", None)),
    ("GET", "/api/set", ("set", None)),
    ("POST", "/api/status", ("verboten", None)),
    ("HEAD", "/api/status", ("verboten", None)),
    ("PUT", "/api/set?frc=1", ("verboten", None)),
    ("GET", "/api/status?", ("verboten", None)),
    ("GET", "/api/status?frc=1", ("verboten", None)),
    ("GET", "/api/status?filter=car&frc=1", ("verboten", None)),
    ("GET", "/api/status?filter=CAR", ("verboten", None)),
    ("GET", "/api/status?filter=car;frc", ("verboten", None)),
    ("GET", "/api/status/", ("verboten", None)),
    ("GET", "/api/status/../set?frc=1", ("verboten", None)),
    ("GET", "/api/./status", ("verboten", None)),
    ("GET", "//api/status", ("verboten", None)),
    ("GET", "/API/status", ("verboten", None)),
    ("GET", "/api/%73tatus", ("verboten", None)),
    ("GET", "/api/status%3Ffilter=car", ("verboten", None)),
    ("GET", "/api/set/", ("verboten", None)),
    ("GET", "/api/setx?frc=1", ("verboten", None)),
    ("GET", "/", ("verboten", None)),
    ("GET", "http://192.0.2.1/api/status", ("verboten", None)),
    ("GET", "/api/status?filter=" + "a" * 201, ("verboten", None)),
]


@pytest.mark.parametrize("methode,pfad,soll", FAELLE)
def test_entscheide(methode, pfad, soll):
    assert lf.entscheide(methode, pfad) == soll


def test_ziel_pfad():
    assert lf.ziel_pfad("") == "/api/status"
    assert lf.ziel_pfad("filter=car") == "/api/status?filter=car"


class _Ladestation:
    """Ersatz-Ladestation: protokolliert jede Anfrage mit Kopfzeilen und Rumpf."""

    def __init__(self, verzoegerung_s=0.0, code=200, typ="application/json"):
        self.anfragen = []
        self.details = []
        aussen = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _alle(self):
                n = int(self.headers.get("Content-Length") or 0)
                rumpf = self.rfile.read(n) if n else b""
                aussen.anfragen.append((self.command, self.path))
                aussen.details.append(({k.lower() for k in self.headers.keys()}, rumpf))
                time.sleep(verzoegerung_s)
                body = b'{"car":1,"eto":4242424}'
                self.send_response(code)
                self.send_header("Content-Type", typ)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def __getattr__(self, name):
                if name.startswith("do_"):
                    return self._alle
                raise AttributeError(name)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stopp(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def aufbau(tmp_path, monkeypatch):
    erzeugt = []
    verbindungen = []
    anfragen = []
    echt_verbinden = socket.create_connection
    echt_request = http.client.HTTPConnection.request

    def verbinden(adresse, *a, **kw):
        verbindungen.append((adresse[0], int(adresse[1])))
        return echt_verbinden(adresse, *a, **kw)

    def request(self, method, url, body=None, headers=None, **kw):
        anfragen.append((self.host, self.port, method, url, body, headers, kw))
        return echt_request(self, method, url, body=body, headers=headers if headers is not None else {}, **kw)

    monkeypatch.setattr(socket, "create_connection", verbinden)
    monkeypatch.setattr(http.client.HTTPConnection, "request", request)

    def mach(verzoegerung_s=0.0, zeitgrenze_s=2.0, ziel_port=None, ziel="127.0.0.1", code=200,
             typ="application/json"):
        st = _Ladestation(verzoegerung_s, code, typ)
        log = tmp_path / "zugriffe.jsonl"
        ep = {"lauschen": "localhost", "port": 0, "ziel": ziel,
              "ziel_port": st.port if ziel_port is None else ziel_port}
        fs = lf.baue_server(ep, zeitgrenze_s, str(log))
        threading.Thread(target=fs.serve_forever, daemon=True).start()
        erzeugt.append((st, fs, ep))
        return st, fs, log

    yield mach
    time.sleep(0.3)
    erlaubt = set()
    for st, fs, ep in erzeugt:
        erlaubt.add(("127.0.0.1", fs.server_address[1]))
        erlaubt.add((ep["ziel"], int(ep["ziel_port"])))
    for st, fs, ep in erzeugt:
        fs.shutdown()
        fs.server_close()
        st.stopp()
        for (methode, pfad), (kopf, rumpf) in zip(st.anfragen, st.details):
            assert methode == "GET" and STATUS_MUSTER.fullmatch(pfad), (methode, pfad)
            assert kopf <= ERLAUBTE_KOPFZEILEN and rumpf == b"", (kopf, rumpf)
    for v in verbindungen:
        assert v in erlaubt, ("Verbindung zu", v)
    ziele = {(ep["ziel"], int(ep["ziel_port"])) for _, _, ep in erzeugt}
    for host, port, methode, url, body, headers, kw in anfragen:
        assert (host, port) in ziele and methode == "GET" and STATUS_MUSTER.fullmatch(url), (host, port, methode, url)
        assert body is None and not headers and not kw, ("Kopfzeilen oder Rumpf an die Ladestation", body, headers, kw)


def _hole(port, methode, pfad, kopf=None, rumpf=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest(methode, pfad, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", "127.0.0.1")
    for k, v in (kopf or {}).items():
        c.putheader(k, v)
    if rumpf is not None:
        c.putheader("Content-Length", str(len(rumpf)))
    c.endheaders(rumpf)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body, r.getheader("Content-Type")


def _roh(port, daten):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(daten)
    antwort = b""
    while True:
        d = s.recv(65536)
        if not d:
            break
        antwort += d
    s.close()
    return antwort


def _zeilen(log):
    return [json.loads(z) for z in log.read_text(encoding="ascii").splitlines()]


def _anfragen(log):
    return [z for z in _zeilen(log) if "entscheidung" in z]


def test_start_ereignis_und_bindung(aufbau):
    st, fs, log = aufbau()
    assert fs.server_address[0] == "127.0.0.1"
    z = _zeilen(log)
    assert len(z) == 1 and z[0]["ereignis"] == "start" and z[0]["endpunkt"] == "localhost"


def test_status_wird_durchgereicht(aufbau):
    st, fs, log = aufbau(code=203, typ="application/json; charset=utf-8")
    code, body, typ = _hole(fs.server_address[1], "GET", "/api/status?filter=car,eto")
    assert (code, body, typ) == (203, b'{"car":1,"eto":4242424}', "application/json; charset=utf-8")
    assert st.anfragen == [("GET", "/api/status?filter=car,eto")]
    z = _anfragen(log)
    assert len(z) == 1
    assert z[0]["entscheidung"] == "status" and z[0]["ziel_status"] == 203
    assert z[0]["methode"] == "GET" and z[0]["pfad"] == "/api/status?filter=car,eto"
    assert z[0]["endpunkt"] == "localhost" and isinstance(z[0]["dauer_ms"], int)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d", z[0]["zeit"])


def test_kopf_und_rumpf_werden_nicht_weitergegeben(aufbau):
    st, fs, log = aufbau()
    code, _, _ = _hole(fs.server_address[1], "GET", "/api/status?filter=car",
                       kopf={"X-Probe": "frc=1", "Authorization": "Basic eA=="}, rumpf=b"frc=1")
    assert code == 200
    assert st.anfragen == [("GET", "/api/status?filter=car")]
    kopf, rumpf = st.details[0]
    assert "x-probe" not in kopf and "authorization" not in kopf and "content-length" not in kopf
    assert rumpf == b""


@pytest.mark.parametrize("pfad", ["/api/set?frc=1", "/api/set?amp=16", "/api/set?psm=2"])
def test_set_wird_beantwortet_nie_weitergeleitet(aufbau, pfad):
    st, fs, log = aufbau()
    code, body, typ = _hole(fs.server_address[1], "GET", pfad)
    assert (code, body, typ) == (200, b"{}", "application/json")
    assert st.anfragen == []
    z = _anfragen(log)
    assert [(e["entscheidung"], e["ziel_status"], e["pfad"]) for e in z] == [("set", None, pfad)]


@pytest.mark.parametrize("methode,pfad", [(m, p) for m, p, s in FAELLE if s[0] == "verboten"
                                          and not p.startswith("http") and m != "HEAD"])
def test_verboten_wird_nie_weitergeleitet(aufbau, methode, pfad):
    st, fs, log = aufbau()
    code, body, _ = _hole(fs.server_address[1], methode, pfad)
    assert (code, body) == (403, b"{}")
    assert st.anfragen == []
    assert [e["entscheidung"] for e in _anfragen(log)] == ["verboten"]


@pytest.mark.parametrize("zeile", [b"TRACE /api/status HTTP/1.0", b"get /api/status HTTP/1.0",
                                   b"FOO /api/set?frc=1 HTTP/1.0", b"GET /api/st\xe4tus HTTP/1.0",
                                   b"HEAD /api/set?frc=1 HTTP/1.0", b"HEAD /api/status HTTP/1.0"])
def test_jede_methode_und_jedes_byte_protokolliert(aufbau, zeile):
    st, fs, log = aufbau()
    antwort = _roh(fs.server_address[1], zeile + b"\r\nHost: x\r\n\r\n")
    assert antwort.split(b" ")[1] == b"403"
    assert st.anfragen == []
    assert [e["entscheidung"] for e in _anfragen(log)] == ["verboten"]


def test_lange_pfade_gekuerzt(aufbau):
    st, fs, log = aufbau()
    pfad = "/api/status?filter=" + "a" * 3000
    code, _, _ = _hole(fs.server_address[1], "GET", pfad)
    assert code == 403 and st.anfragen == []
    assert _anfragen(log)[0]["pfad"] == pfad[:2000]


@pytest.mark.parametrize("daten", [b"QUATSCH\r\n\r\n", b"GET /" + b"a" * 70000 + b" HTTP/1.0\r\n\r\n"])
def test_abweisung_protokolliert(aufbau, daten):
    st, fs, log = aufbau()
    antwort = _roh(fs.server_address[1], daten)
    assert re.match(rb"HTTP/1\.[01] (400|414) ", antwort) or re.search(rb"Error code: (400|414)\b", antwort)
    assert st.anfragen == []
    assert [e["entscheidung"] for e in _anfragen(log)] == ["abgewiesen"]


def test_ziel_nicht_erreichbar_502(aufbau):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    frei = s.getsockname()[1]
    s.close()
    st, fs, log = aufbau(ziel_port=frei)
    code, body, typ = _hole(fs.server_address[1], "GET", "/api/status?filter=car")
    assert (code, body, typ) == (502, b'{"fehler":"ziel"}', "application/json")
    z = _anfragen(log)
    assert z[0]["entscheidung"] == "status" and z[0]["ziel_status"] is None


def test_zeitgrenze_502(aufbau):
    st, fs, log = aufbau(verzoegerung_s=1.5, zeitgrenze_s=0.5)
    t0 = time.monotonic()
    code, _, _ = _hole(fs.server_address[1], "GET", "/api/status?filter=car")
    assert code == 502
    assert time.monotonic() - t0 < 1.4
    assert _anfragen(log)[0]["ziel_status"] is None
    time.sleep(1.5)
    assert st.anfragen == [("GET", "/api/status?filter=car")]


def test_mischfolge_nur_status_beim_ziel(aufbau):
    st, fs, log = aufbau()
    folge = [("GET", "/api/status?filter=car"), ("GET", "/api/set?frc=1"), ("POST", "/api/status"),
             ("GET", "/api/status/../set?frc=1"), ("GET", "/api/status")]
    for m, p in folge:
        _hole(fs.server_address[1], m, p)
    assert st.anfragen == [("GET", "/api/status?filter=car"), ("GET", "/api/status")]
    assert [e["entscheidung"] for e in _anfragen(log)] == ["status", "set", "verboten", "verboten", "status"]
