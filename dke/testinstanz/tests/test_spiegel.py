"""Pruefwerkzeug des Auftraggebers fuer den MQTT-Spiegel (Auftrag C1, Fassung 3). Nicht aendern."""
import copy
import importlib.util
import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mqtt_pruefbroker import CONNECT, DISCONNECT, PINGREQ, PUBLISH, SUBSCRIBE, Pruefbroker  # noqa: E402

_PFAD = pathlib.Path(__file__).resolve().parents[1] / "spiegel" / "spiegel.py"
_spec = importlib.util.spec_from_file_location("spiegel", _PFAD)
sp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sp)

ZUORDNUNGEN = [
    {"quelle": "openWB/counter/7/get/", "ziel": "openWB/mqtt/counter/2/get/",
     "felder": ["power", "imported", "exported"]},
    {"quelle": "openWB/bat/1/get/", "ziel": "openWB/mqtt/bat/3/get/", "felder": ["power", "soc"]},
]
QUELLE_TOPICS = sorted(["openWB/counter/7/get/power", "openWB/counter/7/get/imported",
                        "openWB/counter/7/get/exported", "openWB/bat/1/get/power", "openWB/bat/1/get/soc"])


def _konfig(q, z, tmp_path, frische_s=30.0, intervall=0.1):
    return {"quelle": {"host": "127.0.0.1", "port": q.port, "client_id": "pruef-quelle"},
            "ziel": {"host": "127.0.0.1", "port": z.port, "client_id": "pruef-ziel"},
            "frische_s": frische_s, "pruef_intervall_s": intervall,
            "zuordnungen": copy.deepcopy(ZUORDNUNGEN), "protokoll": str(tmp_path / "spiegel.jsonl")}


def _warte(bedingung, s=5.0):
    ende = time.monotonic() + s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return bedingung()


@pytest.fixture
def broker():
    q, z = Pruefbroker(), Pruefbroker()
    yield q, z
    q.stopp()
    z.stopp()
    for v in q.verbindungen:
        assert v.client_id == "pruef-quelle", ("fremde Verbindung am Quell-Broker", v.client_id)
        assert PUBLISH not in [t for t, _ in v.pakete] and v.publish == []
        assert v.connect_flags & 0x04 == 0 and v.connect_flags & 0x02
        assert set(v.abos) <= set(QUELLE_TOPICS)
    for v in z.verbindungen:
        assert v.client_id == "pruef-ziel", ("fremde Verbindung am Ziel-Broker", v.client_id)
        assert SUBSCRIBE not in [t for t, _ in v.pakete]
        assert v.connect_flags & 0x04 == 0 and v.connect_flags & 0x02


def _verbindung(b, client_id):
    v = [x for x in b.verbindungen if x.client_id == client_id]
    assert len(v) == 1, [x.client_id for x in b.verbindungen]
    return v[0]


def _ereignisse(tmp_path):
    p = tmp_path / "spiegel.jsonl"
    if not p.exists():
        return []
    return [json.loads(z) for z in p.read_text(encoding="ascii").splitlines()]


@pytest.mark.parametrize("aendern", [
    lambda k: k["zuordnungen"][0].update(quelle="openWB/counter/#/"),
    lambda k: k["zuordnungen"][0].update(quelle="openWB/+/7/get/"),
    lambda k: k["zuordnungen"][0]["felder"].append("#"),
    lambda k: k["zuordnungen"][0]["felder"].append("+"),
    lambda k: k["zuordnungen"][0]["felder"].append("a/b"),
    lambda k: k["zuordnungen"][0].update(felder=[]),
    lambda k: k["zuordnungen"][0].update(ziel="openWB/counter/2/get/"),
    lambda k: k["zuordnungen"][0].update(quelle="counter/7/get/"),
    lambda k: k["zuordnungen"][0].update(quelle="openWB/counter/7/get"),
    lambda k: k["zuordnungen"].append(copy.deepcopy(k["zuordnungen"][0])),
    lambda k: k.update(zuordnungen=[]),
    lambda k: k.update(frische_s=0),
    lambda k: k["quelle"].update(host=""),
    lambda k: k["ziel"].update(host=""),
    lambda k: k.update(frische_s=-5),
    lambda k: k["ziel"].update(host="q"),
    lambda k: k["zuordnungen"][0].update(quelle="openWB/system/7/get/"),
    lambda k: k["zuordnungen"][0].update(quelle="openWB/set/counter/7/get/"),
    lambda k: k["zuordnungen"][0].update(quelle="openWB/counter/x/get/"),
    lambda k: k["zuordnungen"][0].update(ziel="openWB/mqtt/counter/2/get"),
    lambda k: k["zuordnungen"][0].update(ziel="openWB/mqtt/+/2/get/"),
    lambda k: k["zuordnungen"][0].update(ziel="openWB/mqtt/chargepoint/2/get/"),
])
def test_konfig_abgelehnt(tmp_path, aendern):
    k = {"quelle": {"host": "q"}, "ziel": {"host": "z"}, "frische_s": 60,
         "zuordnungen": copy.deepcopy(ZUORDNUNGEN)}
    aendern(k)
    with pytest.raises(ValueError):
        sp.pruefe_konfig(k)


def test_zuordnung():
    k = {"zuordnungen": ZUORDNUNGEN}
    assert sp.zuordnung(k) == {
        "openWB/counter/7/get/power": "openWB/mqtt/counter/2/get/power",
        "openWB/counter/7/get/imported": "openWB/mqtt/counter/2/get/imported",
        "openWB/counter/7/get/exported": "openWB/mqtt/counter/2/get/exported",
        "openWB/bat/1/get/power": "openWB/mqtt/bat/3/get/power",
        "openWB/bat/1/get/soc": "openWB/mqtt/bat/3/get/soc"}


def test_spiegelt_und_liest_nur(broker, tmp_path):
    q, z = broker
    q.veroeffentliche("openWB/counter/7/get/power", "1500.0")
    q.veroeffentliche("openWB/counter/7/get/imported", "123456.789")
    q.veroeffentliche("openWB/bat/1/get/soc", "24")
    q.veroeffentliche("openWB/counter/7/get/serial_number", "geheim")
    s = sp.Spiegel(_konfig(q, z, tmp_path))
    s.start()
    try:
        assert _warte(lambda: len(z.retained) == 3)
        assert z.retained == {"openWB/mqtt/counter/2/get/power": b"1500.0",
                              "openWB/mqtt/counter/2/get/imported": b"123456.789",
                              "openWB/mqtt/bat/3/get/soc": b"24"}
        q.veroeffentliche("openWB/counter/7/get/power", "-120.5")
        assert _warte(lambda: z.retained.get("openWB/mqtt/counter/2/get/power") == b"-120.5")
    finally:
        s.stopp()
    assert [e["ereignis"] for e in _ereignisse(tmp_path)][:1] == ["verbunden"]
    vq = _verbindung(q, "pruef-quelle")
    assert {t for t, _ in vq.pakete} <= {CONNECT, SUBSCRIBE, PINGREQ, DISCONNECT}
    assert PUBLISH not in [t for t, _ in vq.pakete] and vq.publish == []
    assert vq.connect_flags & 0x04 == 0
    assert sorted(vq.abos) == QUELLE_TOPICS
    vz = _verbindung(z, "pruef-ziel")
    assert SUBSCRIBE not in [t for t, _ in vz.pakete]
    assert vz.publish and all(retain for _, _, retain, _ in vz.publish)
    assert {t for t, _, _, _ in vz.publish} <= set(sp.zuordnung({"zuordnungen": ZUORDNUNGEN}).values())


def test_frische(broker, tmp_path):
    q, z = broker
    z.veroeffentliche("openWB/mqtt/bat/3/get/soc", "77")
    q.veroeffentliche("openWB/counter/7/get/power", "100")
    s = sp.Spiegel(_konfig(q, z, tmp_path, frische_s=1.0, intervall=0.1))
    s.start()
    try:
        assert _warte(lambda: z.retained.get("openWB/mqtt/counter/2/get/power") == b"100")
        assert "openWB/mqtt/bat/3/get/soc" in z.retained
        assert _warte(lambda: "openWB/mqtt/counter/2/get/power" not in z.retained, 3.0)
        assert _warte(lambda: "openWB/mqtt/bat/3/get/soc" not in z.retained, 3.0)
        time.sleep(0.6)
        leer = [t for t, n, _, _ in _verbindung(z, "pruef-ziel").publish if n == b""]
        assert sorted(leer) == sorted(sp.zuordnung({"zuordnungen": ZUORDNUNGEN}).values())
        ev = _ereignisse(tmp_path)
        assert sorted(e["topic"] for e in ev if e["ereignis"] == "veraltet") == QUELLE_TOPICS
        q.veroeffentliche("openWB/counter/7/get/power", "101")
        assert _warte(lambda: z.retained.get("openWB/mqtt/counter/2/get/power") == b"101")
        assert _warte(lambda: any(e["ereignis"] == "wieder_frisch" and e["topic"] == "openWB/counter/7/get/power"
                                  for e in _ereignisse(tmp_path)))
    finally:
        s.stopp()


def test_frische_mit_uhr():
    t = [100.0]
    k = {"quelle": {"host": "q"}, "ziel": {"host": "z"}, "frische_s": 60, "zuordnungen": copy.deepcopy(ZUORDNUNGEN)}
    s = sp.Spiegel(k, uhr=lambda: t[0])
    gesendet = []
    s.ziel.publish = lambda topic, payload=None, qos=0, retain=False: gesendet.append((topic, payload, retain))

    def _verboten(*a, **kw):
        raise AssertionError("Quell-Client darf nie veroeffentlichen")
    s.quelle.publish = _verboten
    s.start_zeit = 100.0
    s.letzte["openWB/counter/7/get/power"] = 100.0
    t[0] = 160.0
    s.pruefe_frische()
    assert gesendet == []
    t[0] = 160.5
    s.pruefe_frische()
    assert sorted(gesendet) == sorted((v, b"", True) for v in sp.zuordnung(k).values())
    s.pruefe_frische()
    assert len(gesendet) == 5

    class _N:
        topic = "openWB/counter/7/get/power"
        payload = b"5"
    s.bei_quellnachricht(None, None, _N())
    assert gesendet[-1] == ("openWB/mqtt/counter/2/get/power", b"5", True)
    t[0] = 220.5
    s.pruefe_frische()
    assert len(gesendet) == 6
    t[0] = 221.0
    s.pruefe_frische()
    assert gesendet[-1] == ("openWB/mqtt/counter/2/get/power", b"", True) and len(gesendet) == 7


def test_wiederverbindung_abonniert_neu(broker, tmp_path):
    q, z = broker
    q.veroeffentliche("openWB/counter/7/get/power", "1")
    s = sp.Spiegel(_konfig(q, z, tmp_path))
    s.start()
    try:
        assert _warte(lambda: z.retained.get("openWB/mqtt/counter/2/get/power") == b"1")
        assert q.trenne("pruef-quelle") == 1
        assert _warte(lambda: len([v for v in q.verbindungen if v.client_id == "pruef-quelle"]) == 2, 15.0)
        assert _warte(lambda: sorted(q.verbindungen[-1].abos) == QUELLE_TOPICS, 5.0)
        q.veroeffentliche("openWB/counter/7/get/power", "2")
        assert _warte(lambda: z.retained.get("openWB/mqtt/counter/2/get/power") == b"2")
    finally:
        s.stopp()
