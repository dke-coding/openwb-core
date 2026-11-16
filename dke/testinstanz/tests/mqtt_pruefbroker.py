"""Minimaler MQTT-3.1.1-Pruefbroker (Pruefwerkzeug des Auftraggebers, Auftrag C1). Nicht aendern.

Nur fuer Tests: exakte Topic-Treffer (keine Platzhalter), QoS 0 und 1 eingehend, Auslieferung mit QoS 0,
retained Speicher (leere Nutzlast loescht). Protokolliert je Verbindung jedes empfangene Paket mit Typ.
"""
import socket
import struct
import threading

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK = 1, 2, 3, 4, 8, 9, 10, 11
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14


def _laenge(n):
    o = bytearray()
    while True:
        b = n % 128
        n //= 128
        o.append(b | (0x80 if n else 0))
        if not n:
            return bytes(o)


def _str(s):
    b = s.encode() if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


class Verbindung:
    def __init__(self):
        self.client_id = None
        self.connect_flags = None
        self.pakete = []          # (typ, topic oder None)
        self.abos = []            # abonnierte Topic-Filter in Reihenfolge
        self.publish = []         # (topic, nutzlast, retain, qos)


class Pruefbroker:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.retained = {}
        self.verbindungen = []
        self._abonnenten = []     # (Verbindung, socket)
        self._sperre = threading.RLock()
        self._laeuft = True
        threading.Thread(target=self._annehmen, daemon=True).start()

    def stopp(self):
        self._laeuft = False
        try:
            self.sock.close()
        except OSError:
            pass
        with self._sperre:
            for _, s in self._abonnenten:
                try:
                    s.close()
                except OSError:
                    pass

    def _annehmen(self):
        while self._laeuft:
            try:
                s, _ = self.sock.accept()
            except OSError:
                return
            v = Verbindung()
            with self._sperre:
                self.verbindungen.append(v)
                self._abonnenten.append((v, s))
            threading.Thread(target=self._bedienen, args=(v, s), daemon=True).start()

    def _senden(self, s, kopf, rest):
        try:
            s.sendall(bytes([kopf]) + _laenge(len(rest)) + rest)
        except OSError:
            pass

    def _ausliefern(self, topic, nutzlast):
        with self._sperre:
            ziele = [(v, s) for v, s in self._abonnenten if topic in v.abos]
        for v, s in ziele:
            self._senden(s, PUBLISH << 4, _str(topic) + nutzlast)

    def _bedienen(self, v, s):
        puffer = b""
        while self._laeuft:
            try:
                d = s.recv(65536)
            except OSError:
                return
            if not d:
                return
            puffer += d
            while len(puffer) >= 2:
                n, m, i = 0, 1, 1
                while True:
                    if i >= len(puffer):
                        n = None
                        break
                    b = puffer[i]
                    n += (b & 0x7F) * m
                    m *= 128
                    i += 1
                    if not b & 0x80:
                        break
                if n is None or len(puffer) < i + n:
                    break
                kopf, rest, puffer = puffer[0], puffer[i:i + n], puffer[i + n:]
                self._paket(v, s, kopf >> 4, kopf & 0x0F, rest)

    def _paket(self, v, s, typ, flags, rest):
        if typ == CONNECT:
            pl = struct.unpack("!H", rest[:2])[0]
            p = 2 + pl + 1
            v.connect_flags = rest[p]
            p += 1 + 2
            cl = struct.unpack("!H", rest[p:p + 2])[0]
            v.client_id = rest[p + 2:p + 2 + cl].decode()
            v.pakete.append((CONNECT, None))
            self._senden(s, CONNACK << 4, b"\x00\x00")
        elif typ == PUBLISH:
            tl = struct.unpack("!H", rest[:2])[0]
            topic = rest[2:2 + tl].decode()
            qos = (flags >> 1) & 3
            p = 2 + tl
            pid = None
            if qos:
                pid = rest[p:p + 2]
                p += 2
            nutzlast = rest[p:]
            retain = bool(flags & 1)
            v.pakete.append((PUBLISH, topic))
            v.publish.append((topic, nutzlast, retain, qos))
            if retain:
                with self._sperre:
                    if nutzlast:
                        self.retained[topic] = nutzlast
                    else:
                        self.retained.pop(topic, None)
            if qos == 1:
                self._senden(s, PUBACK << 4, pid)
            self._ausliefern(topic, nutzlast)
        elif typ == SUBSCRIBE:
            pid = rest[:2]
            p = 2
            neu = []
            while p < len(rest):
                tl = struct.unpack("!H", rest[p:p + 2])[0]
                neu.append(rest[p + 2:p + 2 + tl].decode())
                p += 2 + tl + 1
            v.pakete.append((SUBSCRIBE, ",".join(neu)))
            v.abos.extend(neu)
            self._senden(s, SUBACK << 4, pid + b"\x00" * len(neu))
            for t in neu:
                with self._sperre:
                    w = self.retained.get(t)
                if w is not None:
                    self._senden(s, (PUBLISH << 4) | 1, _str(t) + w)
        elif typ == UNSUBSCRIBE:
            v.pakete.append((UNSUBSCRIBE, None))
            self._senden(s, UNSUBACK << 4, rest[:2])
        elif typ == PINGREQ:
            v.pakete.append((PINGREQ, None))
            self._senden(s, PINGRESP << 4, b"")
        elif typ == DISCONNECT:
            v.pakete.append((DISCONNECT, None))
        else:
            v.pakete.append((typ, None))

    def trenne(self, client_id):
        """Testseite: alle Verbindungen dieses Clients hart schliessen (Abbruch wie bei Neustart der Anlage)."""
        with self._sperre:
            ziele = [(v, s) for v, s in self._abonnenten if v.client_id == client_id]
            self._abonnenten = [(v, s) for v, s in self._abonnenten if v.client_id != client_id]
        for _, s in ziele:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        return len(ziele)

    def veroeffentliche(self, topic, nutzlast, retain=True):
        """Testseite: Wert so setzen, als kaeme er von der Anlage (ohne eigene Verbindung)."""
        if isinstance(nutzlast, str):
            nutzlast = nutzlast.encode()
        with self._sperre:
            if retain:
                if nutzlast:
                    self.retained[topic] = nutzlast
                else:
                    self.retained.pop(topic, None)
        self._ausliefern(topic, nutzlast)
