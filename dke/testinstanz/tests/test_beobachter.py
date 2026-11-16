"""Pruefwerkzeug des Auftraggebers fuer den Beobachter (Auftrag C1, Fassung 3). Nicht aendern."""
import copy
import importlib.util
import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mqtt_pruefbroker import PUBLISH, Pruefbroker  # noqa: E402

_PFAD = pathlib.Path(__file__).resolve().parents[1] / "beobachter" / "beobachter.py"
_spec = importlib.util.spec_from_file_location("beobachter", _PFAD)
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

ANLAGE = ["openWB/counter/7/get/fault_state", "openWB/chargepoint/5/get/imported", "openWB/system/boot_done"]
TEST = ["openWB/counter/2/get/fault_state", "openWB/chargepoint/1/get/imported"]


def _konfig(a, t, tmp_path, takt_s=60):
    return {"seiten": [
        {"name": "anlage", "host": "127.0.0.1", "port": a.port, "client_id": "pruef-anlage", "topics": list(ANLAGE)},
        {"name": "test", "host": "127.0.0.1", "port": t.port, "client_id": "pruef-test", "topics": list(TEST)}],
        "takt_s": takt_s, "protokoll": str(tmp_path / "beobachter.jsonl")}


def _warte(bedingung, s=5.0):
    ende = time.monotonic() + s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.05)
    return bedingung()


def _zeilen(tmp_path):
    p = tmp_path / "beobachter.jsonl"
    return [json.loads(z) for z in p.read_text(encoding="ascii").splitlines()] if p.exists() else []


@pytest.fixture
def broker():
    a, t = Pruefbroker(), Pruefbroker()
    yield a, t
    a.stopp()
    t.stopp()
    for b, cid, liste in ((a, "pruef-anlage", ANLAGE), (t, "pruef-test", TEST)):
        for v in b.verbindungen:
            assert v.client_id == cid
            assert PUBLISH not in [x for x, _ in v.pakete] and v.publish == []
            assert v.connect_flags & 0x04 == 0 and v.connect_flags & 0x02
            assert set(v.abos) <= set(liste)


@pytest.mark.parametrize("aendern", [
    lambda k: k["seiten"][0]["topics"].append("openWB/#"),
    lambda k: k["seiten"][0]["topics"].append("openWB/counter/+/get/power"),
    lambda k: k["seiten"][0]["topics"].append("openWB/system/ip_address"),
    lambda k: k["seiten"][0]["topics"].append("openWB/set/chargepoint/5/set/manual_lock"),
    lambda k: k["seiten"][0]["topics"].append(k["seiten"][0]["topics"][0]),
    lambda k: k["seiten"][1].update(topics=[]),
    lambda k: k["seiten"][1].update(name="anlage"),
    lambda k: k["seiten"].pop(),
    lambda k: k["seiten"][1].update(host="a", port=1883),
    lambda k: k["seiten"][0].update(host=""),
    lambda k: k.update(takt_s=0),
])
def test_konfig_abgelehnt(aendern):
    k = {"seiten": [{"name": "anlage", "host": "a", "topics": list(ANLAGE)},
                    {"name": "test", "host": "t", "topics": list(TEST)}], "takt_s": 60}
    aendern(k)
    with pytest.raises(ValueError):
        bb.pruefe_konfig(k)


def test_liest_nur_und_schreibt_takt(broker, tmp_path):
    a, t = broker
    a.veroeffentliche("openWB/counter/7/get/fault_state", "0")
    a.veroeffentliche("openWB/chargepoint/5/get/imported", "4242424.0")
    t.veroeffentliche("openWB/chargepoint/1/get/imported", "4242300.0")
    b = bb.Beobachter(_konfig(a, t, tmp_path))
    b.start()
    try:
        assert _warte(lambda: len(b.werte) == 3)
        a.veroeffentliche("openWB/counter/7/get/fault_state", "2")
        a.veroeffentliche("openWB/counter/7/get/fault_state", "2")
        a.veroeffentliche("openWB/system/boot_done", "true")
        assert _warte(lambda: len([z for z in _zeilen(tmp_path) if z["ereignis"] == "aenderung"]) == 3)
        b.schreibe_takt()
    finally:
        b.stopp()
    z = _zeilen(tmp_path)
    aend = [(e["seite"], e["topic"], e["wert"]) for e in z if e["ereignis"] == "aenderung"]
    assert aend == [("anlage", "openWB/counter/7/get/fault_state", "0"),
                    ("anlage", "openWB/counter/7/get/fault_state", "2"),
                    ("anlage", "openWB/system/boot_done", "true")]
    takt = {(e["seite"], e["topic"]): e for e in z if e["ereignis"] == "takt"}
    assert set(takt) == {("anlage", x) for x in ANLAGE} | {("test", x) for x in TEST}
    assert takt[("test", "openWB/chargepoint/1/get/imported")]["wert"] == "4242300.0"
    assert takt[("test", "openWB/counter/2/get/fault_state")]["wert"] is None
    assert takt[("test", "openWB/counter/2/get/fault_state")]["alter_s"] is None
    assert isinstance(takt[("anlage", "openWB/chargepoint/5/get/imported")]["alter_s"], float)
    for v in a.verbindungen:
        assert sorted(v.abos) == sorted(ANLAGE)
    for v in t.verbindungen:
        assert sorted(v.abos) == sorted(TEST)


def test_alter_mit_uhr(tmp_path):
    u = [1000.0]
    k = {"seiten": [{"name": "anlage", "host": "a", "topics": list(ANLAGE)},
                    {"name": "test", "host": "t", "topics": list(TEST)}],
         "takt_s": 60, "protokoll": str(tmp_path / "beobachter.jsonl")}
    b = bb.Beobachter(copy.deepcopy(k), uhr=lambda: u[0])
    for c in b.clients.values():
        def _verboten(*x, **y):
            raise AssertionError("Beobachter darf nie veroeffentlichen")
        c.publish = _verboten
    b.bei_nachricht("anlage", "openWB/chargepoint/5/get/imported", b"1.0")
    b.bei_nachricht("test", "openWB/counter/2/get/fault_state", b"\xff")
    u[0] = 1012.34
    b.schreibe_takt()
    takt = {(e["seite"], e["topic"]): e for e in _zeilen(tmp_path) if e["ereignis"] == "takt"}
    assert takt[("anlage", "openWB/chargepoint/5/get/imported")]["alter_s"] == 12.3
    assert takt[("test", "openWB/counter/2/get/fault_state")]["wert"] is None
    assert takt[("test", "openWB/counter/2/get/fault_state")]["alter_s"] == 12.3
    aend = [e for e in _zeilen(tmp_path) if e["ereignis"] == "aenderung"]
    assert [(e["topic"], e["wert"]) for e in aend] == [("openWB/counter/2/get/fault_state", None)]


def test_wiederverbindung_abonniert_neu(broker, tmp_path):
    a, t = broker
    b = bb.Beobachter(_konfig(a, t, tmp_path))
    b.start()
    try:
        assert _warte(lambda: len(a.verbindungen) == 1 and len(a.verbindungen[0].abos) == 3)
        assert a.trenne("pruef-anlage") == 1
        assert _warte(lambda: len(a.verbindungen) == 2 and sorted(a.verbindungen[1].abos) == sorted(ANLAGE), 15.0)
        a.veroeffentliche("openWB/system/boot_done", "true")
        assert _warte(lambda: ("anlage", "openWB/system/boot_done") in b.werte)
    finally:
        b.stopp()


def test_takt_schleife(broker, tmp_path):
    a, t = broker
    a.veroeffentliche("openWB/chargepoint/5/get/imported", "4242424.0")
    b = bb.Beobachter(_konfig(a, t, tmp_path, takt_s=0.2))
    b.start()
    try:
        soll = 2 * (len(ANLAGE) + len(TEST))
        assert _warte(lambda: len([z for z in _zeilen(tmp_path) if z["ereignis"] == "takt"]) >= soll)
    finally:
        b.stopp()
    takt = [(z["seite"], z["topic"]) for z in _zeilen(tmp_path) if z["ereignis"] == "takt"]
    for paar in [("anlage", x) for x in ANLAGE] + [("test", x) for x in TEST]:
        assert takt.count(paar) >= 2
