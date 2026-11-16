# openWB-Testinstanz (Gate 1)

Werkzeuge fuer eine openWB-Testinstanz, die sieben Tage lang neben der Anlage mitlaeuft und dabei an
Ladestationen und Broker der Anlage nur liest. Der Core von openWB bleibt unveraendert (Fork light);
alles hier liegt unter `dke/testinstanz/`.

## Orte

| Ort | Inhalt |
|---|---|
| CT 124 | Lesefilter (je Ladestation ein Endpunkt) |
| CT 125 (Debian 13, Python 3.13, python3-paho-mqtt 2.1.0) | Spiegel, Beobachter, Logsammler |
| VM 119 (Debian 11) | openWB-Testinstanz, installiert mit dem gepinnten Installer |

Adressen, Benutzer und Schluessel stehen nicht im Repo. Die Beispielkonfigurationen unter `konfig/`
nutzen nur Dokumentationsnetze (192.0.2.0/24, 198.51.100.0/24). Die echten Konfigurationen liegen unter
`/etc/owb-testinstanz/` und werden beim Einbau (Auftrag T2) geschrieben.

## Komponenten

- `lesefilter/lesefilter.py` (nur Standardbibliothek): steht zwischen Testinstanz und go-e-Ladestation.
  `GET /api/status` (ohne Query oder `filter=[a-z0-9,]{1,200}`) geht genau einmal an die Ladestation,
  ohne Kopfzeilen und Rumpf des Clients. `GET /api/set` beantwortet der Filter selbst mit `200 {}`,
  alles andere mit `403 {}`. Entschieden wird auf dem rohen Ziel der Anfragezeile. Je Anfrage eine
  JSON-Zeile, auch fuer Abweisungen durch `http.server`.
- `spiegel/spiegel.py`: abonniert benannte Topics `openWB/<typ>/<id>/get/<feld>` am Broker der Anlage
  (nie Veroeffentlichung, kein `openWB/#`) und veroeffentlicht sie retained als
  `openWB/mqtt/<typ>/<id>/get/<feld>` am Broker der Testinstanz (Geraet "MQTT"). Bleibt ein Topic
  laenger als `frische_s` aus, wird der Zielwert einmal geloescht.
- `beobachter/beobachter.py`: liest an beiden Brokern benannte Topics und schreibt Aenderungen von
  `fault_state` und `boot_done` sowie alle `takt_s` den letzten Wert je Topic.
- `logsammler/logsammler.py`: holt je Lauf `main.log.1` und `main.log` der Testinstanz in einem Aufruf
  und haengt nur neue, vollstaendige Zeilen an `main-<JJJJ-MM-TT>.txt` an (Ueberlapp mit den letzten
  200 Zeilen, `luecke_moeglich` wenn keiner gefunden wird). Aufbewahrung 14 Tage.
- `logsammler/owb-log-lesen.sh`: Lesebefehl auf der VM (siehe unten).
- `installer/owb-install-gepinnt.sh`: Installer fuer VM 119, abgeleitet aus `openwb-install.sh`.
- `systemd/`: Einheiten fuer CT 124/125. Benutzer `owb-testinstanz` (kein root), Code unter
  `/opt/owb-testinstanz/`, Konfiguration unter `/etc/owb-testinstanz/`, Schreibrecht nur auf das
  eigene Verzeichnis unter `/var/log/owb-testinstanz/` (`LogsDirectory`, `ProtectSystem=strict`).
  Der Lesefilter bekommt `CAP_NET_BIND_SERVICE` fuer Port 80. Der Logsammler laeuft als oneshot ueber
  `owb-logsammler.timer` alle 60 s.

Tests: `python -m pytest dke/testinstanz/tests -p no:cacheprovider` (Python 3.9 bis 3.13,
`requirements-test.txt`), in der CI als Job `testinstanz` von `dke-ci`.

## Lesebefehl fuer den Logsammler (VM 119)

Nach einem Start der VM (ramdisk ist tmpfs) gibt es bis zur ersten Rotation (5 MB) kein `main.log.1`;
`cat main.log.1 main.log` wuerde dann mit Exit 1 enden und jeder Lauf waere `abruf_fehlgeschlagen`.
Deshalb fuehrt der Schluessel des Logsammlers auf der VM nur `owb-log-lesen.sh` aus (forced command):

- wertet weder Argumente noch `SSH_ORIGINAL_COMMAND` aus, liest immer die feste Liste
  `main.log.1` (wenn vorhanden), dann `main.log` unter `/var/www/html/openWB/ramdisk`;
- fehlendes oder unlesbares `main.log`: Meldung auf stderr, Exit ungleich 0;
- liest nur, schreibt nichts, kein Netz.

Einbau in T2 etwa mit `install -m 0755 owb-log-lesen.sh /usr/local/bin/owb-log-lesen.sh` (im Repo ohne
Ausfuehrungsrecht). Die Variable `OWB_LOG_VERZEICHNIS` existiert nur fuer die Tests. sshd reicht sie nicht durch;
`AcceptEnv` nicht erweitern und `PermitUserEnvironment` nicht einschalten.

Beispielzeile fuer `~<leser>/.ssh/authorized_keys` auf der VM (Schluessel als Platzhalter):

```
command="/usr/local/bin/owb-log-lesen.sh",restrict ssh-ed25519 <OEFFENTLICHER-SCHLUESSEL> owb-logsammler
```

Der Logsammler-Befehl in der Konfiguration endet weiter mit `cat`; die Dateiliste, die er anhaengt,
wird auf der VM wegen des forced command nicht ausgewertet.

## Gepinnter Installer

Unterschiede zu `openwb-install.sh` (Stand cf803dc8):

- kein Bezug auf `master` und auf `openWB/core`; Klon von `dke-coding/openwb-core` mit
  `--branch deployed-2026-10-28`, Abbruch, wenn `git rev-parse HEAD` nicht
  `cf803dc8b8b7a105697e8537b687908a11b0d020` ist (Pruefung als Benutzer openwb wegen safe.directory);
- `install_packages.sh` kommt aus diesem Klon; vorher werden git, sudo und ca-certificates
  installiert und der Elternordner von `/var/www/html/openWB` angelegt;
- Remote Support: keine Kopie, kein enable, kein start; `systemctl mask openwbRemoteSupport`;
- Proxy: ist `http_proxy` oder `https_proxy` gesetzt, laufen apt (`/etc/apt/apt.conf.d/95owb-testinstanz-proxy`),
  die sudo-Aufrufe (`/etc/sudoers.d/owb-testinstanz-proxy`, env_keep, geprueft mit `visudo -cf`),
  curl, wget, git und pip darueber. Die Proxy-URL wird nicht ausgegeben. Beide Dateien bleiben nach
  der Installation bestehen.
- ein Leerzeichen am Zeilenende (Original-Zeile 114) entfaellt.

## Grenzen und offene Punkte (bis T2 nicht belegt)

- Grenze aus dem Installer: `install_packages.sh` holt weiter Pakete von repo.mosquitto.org und das
  PHP-Paket von openwb.de, `atreboot.sh` fuehrt bei jedem Start pip aus. Das ist kein `master`, aber
  externer Stand.
- Laufzeit ohne Proxy: `openwb2.service` bekommt keine Proxy-Variablen. Ohne direkten Internetzugang
  meldet `runs/setup_network.sh` keine Verbindung, `atreboot.sh` setzt hasInet=0 und laesst
  `install_packages.sh`, pip und den Versionsabruf aus. Dafuer stehen bei jedem Start der openWB die
  Zeilen `ERROR: no internet connection!` und `#### network setup failed!` in `main.log`. Beide sind
  nicht im Format des openWB-Loggers (kein F1). Annahme, ausgeloest in T2.
- Wirkung von mask auf `atreboot.sh` (Zeilen 207-224): die maskierte Einheit ist ein Symlink auf
  `/dev/null`, `[ ! -f ... ]` ist wahr, also laeuft bei jedem Start der Zweig "service missing":
  `cp` schreibt durch den Symlink nach `/dev/null` (die Maske bleibt), `enable` und `start` scheitern
  mit "masked". atreboot hat kein `set -e` und laeuft weiter. Ergebnis: drei Zeilen je Start in
  `main.log`, keine im Format des openWB-Loggers mit ERROR oder CRITICAL, keine beginnt mit Traceback,
  also kein F1. Annahme bis T2 (Wortlaut unter systemd 247, `systemctl is-enabled` bleibt `masked`).
- Zweiter Weg zum Remote Support: `packages/helpermodules/subdata.py` startet bei einer Nachricht auf
  `openWB/set/system/GetRemoteSupport` direkt `runs/start_remote_support.sh` (ssh-Tunnel zu
  remotesupport.openwb.de). mask wirkt dagegen nicht. Massnahme in T2: VM ohne direkte Verbindung nach
  aussen, remotesupport.openwb.de nicht in der Freigabeliste des Proxys (ssh geht ohnehin nicht ueber
  einen HTTP-Proxy).
- Der Klon per Tag ergibt einen detached HEAD; das betrifft nur den Versionsabruf der openWB, der bei
  hasInet=0 nicht laeuft.
- Lesefilter, Spiegel, Beobachter, Logsammler, systemd-Einheiten und Installer sind nur hier getestet,
  nicht an Anlage oder Terra. Schreibsperren, Spiegel, mask und Proxy sind erst als ausgeloester Pfad
  in T2 belegt; der Installer wurde nicht ausgefuehrt.
- Ein Logsammler-Lauf liest bis rund 10 MB (zwei Dateien zu 5 MB); die Last wird in T2 gemessen.
