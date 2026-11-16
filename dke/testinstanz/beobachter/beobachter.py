#!/usr/bin/env python3
"""Beobachter der openWB-Testinstanz (Gate 1).

Abonniert je Seite ("anlage", "test") benannte Topics und schreibt nur JSON-Zeilen in eine Datei:
- "aenderung" bei neuem oder anderem Wert eines Topics auf /fault_state oder /boot_done,
- "takt" alle takt_s je Seite und Topic mit letztem Wert und Alter.
Veroeffentlicht nie. paho-mqtt 2.1.0 (CallbackAPIVersion.VERSION2).
Dienst: python3 beobachter.py --konfig <json>
"""
import argparse
import datetime
import json
import re
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

_TOPIC = re.compile(r"openWB/(counter|bat|pv|chargepoint)/[0-9]+/get/[a-z_]+|openWB/system/boot_done")
_SEITEN = ("anlage", "test")
_STANDARD_PORT = 1883
_AENDERUNG_ENDUNGEN = ("/fault_state", "/boot_done")


def _zeit():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _ist_text(wert):
    return isinstance(wert, str) and wert != ""


def _port(seite):
    port = seite.get("port", _STANDARD_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
        raise ValueError("ungueltiger port")
    return port


def _text(nutzlast):
    if nutzlast is None:
        return None
    try:
        return bytes(nutzlast).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def pruefe_konfig(k):
    if not isinstance(k, dict):
        raise ValueError("Konfig ist kein Objekt")
    seiten = k.get("seiten")
    if not isinstance(seiten, list) or len(seiten) != 2:
        raise ValueError("seiten muss genau zwei Eintraege haben")
    namen = []
    for seite in seiten:
        if not isinstance(seite, dict):
            raise ValueError("Seite ist kein Objekt")
        namen.append(seite.get("name"))
        if not _ist_text(seite.get("host")):
            raise ValueError("Seite ohne host")
        _port(seite)
        if "client_id" in seite and not _ist_text(seite["client_id"]):
            raise ValueError("client_id ungueltig")
        topics = seite.get("topics")
        if not isinstance(topics, list) or not topics:
            raise ValueError("topics fehlt oder leer")
        if len(set(topics)) != len(topics):
            raise ValueError("Topic doppelt")
        for topic in topics:
            if not isinstance(topic, str) or not _TOPIC.fullmatch(topic):
                raise ValueError("ungueltiges Topic: %r" % (topic,))
    if sorted(namen) != sorted(_SEITEN):
        raise ValueError("Seiten muessen anlage und test heissen")
    if (seiten[0]["host"], _port(seiten[0])) == (seiten[1]["host"], _port(seiten[1])):
        raise ValueError("anlage und test sind derselbe Broker")
    takt = k.get("takt_s", 60)
    if isinstance(takt, bool) or not isinstance(takt, (int, float)) or takt <= 0:
        raise ValueError("takt_s muss > 0 sein")
    if "protokoll" in k and not _ist_text(k["protokoll"]):
        raise ValueError("protokoll ungueltig")
    return k


class Beobachter:
    def __init__(self, k, uhr=time.monotonic):
        self.k = pruefe_konfig(k)
        self.uhr = uhr
        self.takt_s = k.get("takt_s", 60)
        self.protokoll = k.get("protokoll")
        self.seiten = {s["name"]: s for s in k["seiten"]}
        self.werte = {}
        self._sperre = threading.Lock()
        self._log_sperre = threading.Lock()
        self._ende = threading.Event()
        self._takter = None
        self.clients = {}
        for name, seite in self.seiten.items():
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=seite.get("client_id", "owb-beobachter-" + name),
                                 clean_session=True, protocol=mqtt.MQTTv311)
            client.on_connect = self._bei_verbindung_fuer(name)
            client.on_message = self._bei_nachricht_fuer(name)
            self.clients[name] = client

    def _schreibe(self, eintraege):
        text = "".join(json.dumps(e, ensure_ascii=True, separators=(",", ":")) + "\n" for e in eintraege)
        if not text:
            return
        with self._log_sperre:
            if self.protokoll:
                with open(self.protokoll, "a", encoding="ascii", newline="\n") as f:
                    f.write(text)
            else:
                sys.stdout.write(text)
                sys.stdout.flush()

    def _bei_verbindung_fuer(self, name):
        def bei_verbindung(client, userdata, flags, reason_code, properties=None):
            if reason_code.is_failure:
                return
            client.subscribe([(topic, 0) for topic in self.seiten[name]["topics"]])
        return bei_verbindung

    def _bei_nachricht_fuer(self, name):
        def bei_nachricht(client, userdata, msg):
            self.bei_nachricht(name, msg.topic, msg.payload)
        return bei_nachricht

    def bei_nachricht(self, seite, topic, nutzlast):
        nutzlast = bytes(nutzlast)
        with self._sperre:
            vorher = self.werte.get((seite, topic))
            self.werte[(seite, topic)] = (nutzlast, self.uhr())
        if topic.endswith(_AENDERUNG_ENDUNGEN) and (vorher is None or vorher[0] != nutzlast):
            self._schreibe([{"zeit": _zeit(), "ereignis": "aenderung", "seite": seite, "topic": topic,
                             "wert": _text(nutzlast)}])

    def schreibe_takt(self):
        jetzt = self.uhr()
        zeit = _zeit()
        eintraege = []
        with self._sperre:
            for seite in self.k["seiten"]:
                for topic in seite["topics"]:
                    wert = self.werte.get((seite["name"], topic))
                    eintraege.append({
                        "zeit": zeit, "ereignis": "takt", "seite": seite["name"], "topic": topic,
                        "wert": None if wert is None else _text(wert[0]),
                        "alter_s": None if wert is None else round(jetzt - wert[1], 1)})
        self._schreibe(eintraege)

    def _taktschleife(self):
        while not self._ende.wait(self.takt_s):
            self.schreibe_takt()

    def start(self):
        for name, client in self.clients.items():
            seite = self.seiten[name]
            client.connect_async(seite["host"], _port(seite))
            client.loop_start()
        self._ende.clear()
        self._takter = threading.Thread(target=self._taktschleife, daemon=True)
        self._takter.start()

    def stopp(self):
        self._ende.set()
        if self._takter is not None:
            self._takter.join(timeout=5)
        for client in self.clients.values():
            client.disconnect()
            client.loop_stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Beobachter der openWB-Testinstanz")
    parser.add_argument("--konfig", required=True)
    args = parser.parse_args(argv)
    with open(args.konfig, encoding="utf-8") as f:
        k = json.load(f)
    beobachter = Beobachter(k)
    ende = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: ende.set())
    beobachter.start()
    try:
        while not ende.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    beobachter.stopp()
    return 0


if __name__ == "__main__":
    sys.exit(main())
