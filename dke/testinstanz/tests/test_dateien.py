"""Eigene Tests ueber alle Dateien unter dke/testinstanz (Auftrag C1, 2 und 4.8)."""
import hashlib
import ipaddress
import os
import pathlib
import re

BASIS = pathlib.Path(__file__).resolve().parents[1]
ERLAUBTE_NETZE = [ipaddress.ip_network("192.0.2.0/24"), ipaddress.ip_network("198.51.100.0/24")]
ERLAUBTE_ADRESSEN = {"127.0.0.1", "0.0.0.0"}
_IPV4 = re.compile(rb"(?<![0-9.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![0-9.])")
ANHANG = {
    "test_lesefilter.py": "96ce0200bbceeec25aa759d14b58afae079b891e705ec0fe36c0ad7f982f4f2c",
    "test_spiegel.py": "2071b71d40726fe7ee539507dc1c77e8263d012b1aae7a0f643eba06c6e0ba1d",
    "test_beobachter.py": "eb4355f9150ab8a5e4e9a93c116179598fe248acefeccec79abbca4b1c375505",
    "mqtt_pruefbroker.py": "6ac861822dbbdcc22b57c418cdc3e5e125e9b8837730a799528af5a550061b5d",
}


def _dateien():
    for wurzel, ordner, namen in os.walk(BASIS):
        ordner[:] = [o for o in ordner if o != "__pycache__"]
        for name in namen:
            yield pathlib.Path(wurzel) / name


def test_kein_cr_und_nur_ascii():
    fehler = []
    for p in _dateien():
        daten = p.read_bytes()
        if b"\r" in daten or any(b > 0x7F for b in daten):
            fehler.append(str(p.relative_to(BASIS)))
    assert fehler == []


def test_keine_log_dateien():
    assert [p.name for p in _dateien() if p.suffix == ".log"] == []


def test_nur_dokumentationsadressen():
    fehler = []
    for p in _dateien():
        for treffer in _IPV4.findall(p.read_bytes()):
            text = treffer.decode()
            try:
                adresse = ipaddress.ip_address(text)
            except ValueError:
                continue
            if text not in ERLAUBTE_ADRESSEN and not any(adresse in n for n in ERLAUBTE_NETZE):
                fehler.append((str(p.relative_to(BASIS)), text))
    assert fehler == []


def test_anhang_wortgleich():
    for name, soll in ANHANG.items():
        assert hashlib.sha256((BASIS / "tests" / name).read_bytes()).hexdigest() == soll, name


def test_gitattributes():
    assert (BASIS / ".gitattributes").read_bytes() == b"* text eol=lf\n"
