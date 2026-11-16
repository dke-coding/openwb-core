#!/usr/bin/env python3
"""MQTT-Spiegel der openWB-Testinstanz (Gate 1).

Liest benannte Topics am Broker der Anlage (nur Abonnement, nie Veroeffentlichung) und veroeffentlicht die
Nutzlast unveraendert und retained am Broker der Testinstanz unter openWB/mqtt/<typ>/<id>/get/<feld>
(Geraet "MQTT" der openWB). Bleibt ein Quell-Topic laenger als frische_s aus, loescht der Spiegel den
retained Wert am Ziel einmal (leere Nutzlast), damit die Testinstanz keine alten Werte sieht.

paho-mqtt 2.1.0 (CallbackAPIVersion.VERSION2). Dienst: python3 spiegel.py --konfig <json>
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

_QUELLE = re.compile(r"openWB/(counter|bat|pv|chargepoint)/[0-9]+/get/")
_ZIEL = re.compile(r"openWB/mqtt/(counter|bat|pv)/[0-9]+/get/")
_STANDARD_PORT = 1883


def _zeit():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _ist_text(wert):
    return isinstance(wert, str) and wert != ""


def _port(seite):
    port = seite.get("port", _STANDARD_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port <= 65535:
        raise ValueError("ungueltiger port")
    return port


def pruefe_konfig(k):
    if not isinstance(k, dict):
        raise ValueError("Konfig ist kein Objekt")
    for name in ("quelle", "ziel"):
        seite = k.get(name)
        if not isinstance(seite, dict) or not _ist_text(seite.get("host")):
            raise ValueError(name + ".host fehlt")
        if "client_id" in seite and not _ist_text(seite["client_id"]):
            raise ValueError(name + ".client_id ungueltig")
    if (k["quelle"]["host"], _port(k["quelle"])) == (k["ziel"]["host"], _port(k["ziel"])):
        raise ValueError("quelle und ziel sind derselbe Broker")
    frische = k.get("frische_s")
    if isinstance(frische, bool) or not isinstance(frische, (int, float)) or frische <= 0:
        raise ValueError("frische_s muss > 0 sein")
    intervall = k.get("pruef_intervall_s", 5)
    if isinstance(intervall, bool) or not isinstance(intervall, (int, float)) or intervall <= 0:
        raise ValueError("pruef_intervall_s muss > 0 sein")
    if "protokoll" in k and not _ist_text(k["protokoll"]):
        raise ValueError("protokoll ungueltig")
    zuordnungen = k.get("zuordnungen")
    if not isinstance(zuordnungen, list) or not zuordnungen:
        raise ValueError("zuordnungen fehlt oder leer")
    gesehen = set()
    for z in zuordnungen:
        if not isinstance(z, dict):
            raise ValueError("Zuordnung ist kein Objekt")
        if not isinstance(z.get("quelle"), str) or not _QUELLE.fullmatch(z["quelle"]):
            raise ValueError("ungueltige Quelle: %r" % (z.get("quelle"),))
        if not isinstance(z.get("ziel"), str) or not _ZIEL.fullmatch(z["ziel"]):
            raise ValueError("ungueltiges Ziel: %r" % (z.get("ziel"),))
        felder = z.get("felder")
        if not isinstance(felder, list) or not felder:
            raise ValueError("felder fehlt oder leer")
        for feld in felder:
            if not _ist_text(feld) or any(c in feld for c in "/#+"):
                raise ValueError("ungueltiges Feld: %r" % (feld,))
            topic = z["quelle"] + feld
            if topic in gesehen:
                raise ValueError("Quell-Topic doppelt: " + topic)
            gesehen.add(topic)
    return k


def zuordnung(k):
    """Quell-Topic (quelle + feld) -> Ziel-Topic (ziel + feld)."""
    return {z["quelle"] + feld: z["ziel"] + feld for z in k["zuordnungen"] for feld in z["felder"]}


def _client(client_id):
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True,
                       protocol=mqtt.MQTTv311)


class Spiegel:
    def __init__(self, k, uhr=time.monotonic):
        self.k = pruefe_konfig(k)
        self.uhr = uhr
        self.abbild = zuordnung(k)
        self.frische_s = k["frische_s"]
        self.pruef_intervall_s = k.get("pruef_intervall_s", 5)
        self.protokoll = k.get("protokoll")
        self.letzte = {}
        self.veraltet = set()
        self.start_zeit = uhr()
        self._sperre = threading.Lock()
        self._log_sperre = threading.Lock()
        self._ende = threading.Event()
        self._pruefer = None
        self.quelle = _client(k["quelle"].get("client_id", "owb-spiegel-quelle"))
        self.quelle.on_connect = self._bei_quellverbindung
        self.quelle.on_message = self.bei_quellnachricht
        self.ziel = _client(k["ziel"].get("client_id", "owb-spiegel-ziel"))

    def _ereignis(self, ereignis, topic=None):
        zeile = json.dumps({"zeit": _zeit(), "ereignis": ereignis, "topic": topic}, ensure_ascii=True,
                           separators=(",", ":")) + "\n"
        with self._log_sperre:
            if self.protokoll:
                with open(self.protokoll, "a", encoding="ascii", newline="\n") as f:
                    f.write(zeile)
            else:
                sys.stdout.write(zeile)
                sys.stdout.flush()

    def _bei_quellverbindung(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            return
        client.subscribe([(topic, 0) for topic in sorted(self.abbild)])
        self._ereignis("verbunden")

    def bei_quellnachricht(self, client, userdata, msg):
        ziel_topic = self.abbild.get(msg.topic)
        if ziel_topic is None:
            return
        self.ziel.publish(ziel_topic, msg.payload, qos=0, retain=True)
        with self._sperre:
            self.letzte[msg.topic] = self.uhr()
            war_veraltet = msg.topic in self.veraltet
            self.veraltet.discard(msg.topic)
        if war_veraltet:
            self._ereignis("wieder_frisch", msg.topic)

    def pruefe_frische(self):
        jetzt = self.uhr()
        neu_veraltet = []
        with self._sperre:
            for topic in sorted(self.abbild):
                if topic in self.veraltet:
                    continue
                if jetzt - self.letzte.get(topic, self.start_zeit) > self.frische_s:
                    self.veraltet.add(topic)
                    neu_veraltet.append(topic)
        for topic in neu_veraltet:
            self.ziel.publish(self.abbild[topic], b"", qos=0, retain=True)
            self._ereignis("veraltet", topic)

    def _pruefzyklus(self):
        while not self._ende.wait(self.pruef_intervall_s):
            self.pruefe_frische()

    def start(self):
        self.start_zeit = self.uhr()
        self.ziel.connect(self.k["ziel"]["host"], _port(self.k["ziel"]))
        self.ziel.loop_start()
        self.quelle.connect(self.k["quelle"]["host"], _port(self.k["quelle"]))
        self.quelle.loop_start()
        self._ende.clear()
        self._pruefer = threading.Thread(target=self._pruefzyklus, daemon=True)
        self._pruefer.start()

    def stopp(self):
        self._ende.set()
        if self._pruefer is not None:
            self._pruefer.join(timeout=5)
        for client in (self.quelle, self.ziel):
            client.disconnect()
            client.loop_stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="MQTT-Spiegel der openWB-Testinstanz")
    parser.add_argument("--konfig", required=True)
    args = parser.parse_args(argv)
    with open(args.konfig, encoding="utf-8") as f:
        k = json.load(f)
    spiegel = Spiegel(k)
    ende = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: ende.set())
    spiegel.start()
    try:
        while not ende.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    spiegel.stopp()
    return 0


if __name__ == "__main__":
    sys.exit(main())
