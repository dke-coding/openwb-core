"""Eigene statische Tests der systemd-Einheiten (Auftrag C1, 4.6). Die Einheiten laufen erst in T2."""
import pathlib

import pytest

SYSTEMD = pathlib.Path(__file__).resolve().parents[1] / "systemd"
DAUERDIENSTE = ("lesefilter", "spiegel", "beobachter")


def _lies(name):
    abschnitte, aktuell = {}, None
    for z in (SYSTEMD / name).read_text(encoding="ascii").splitlines():
        if not z.strip() or z.startswith("#"):
            continue
        if z.startswith("["):
            aktuell = abschnitte.setdefault(z.strip("[]"), [])
            continue
        schluessel, _, wert = z.partition("=")
        aktuell.append((schluessel, wert))
    return abschnitte


def _werte(einheit, schluessel):
    return [w for s, w in einheit["Service"] if s == schluessel]


def test_dateien_vollstaendig():
    assert sorted(p.name for p in SYSTEMD.iterdir()) == sorted(
        ["owb-%s.service" % d for d in DAUERDIENSTE] + ["owb-logsammler.service", "owb-logsammler.timer"])


@pytest.mark.parametrize("dienst", DAUERDIENSTE + ("logsammler",))
def test_haertung_und_rechte(dienst):
    e = _lies("owb-%s.service" % dienst)
    assert _werte(e, "User") == ["owb-testinstanz"] and _werte(e, "Group") == ["owb-testinstanz"]
    assert _werte(e, "ProtectSystem") == ["strict"]
    assert _werte(e, "NoNewPrivileges") == ["yes"]
    assert _werte(e, "ProtectHome") == ["yes"]
    assert _werte(e, "LogsDirectory") == ["owb-testinstanz/" + dienst]
    for verboten in ("ReadWritePaths", "StateDirectory", "RuntimeDirectory", "CacheDirectory", "DynamicUser"):
        assert _werte(e, verboten) == [], verboten
    soll = "/usr/bin/python3 /opt/owb-testinstanz/%s/%s.py --konfig /etc/owb-testinstanz/%s.json"
    assert _werte(e, "ExecStart") == [soll % (dienst, dienst, dienst)]
    if dienst == "lesefilter":
        assert _werte(e, "AmbientCapabilities") == ["CAP_NET_BIND_SERVICE"]
        assert _werte(e, "CapabilityBoundingSet") == ["CAP_NET_BIND_SERVICE"]
    else:
        assert _werte(e, "AmbientCapabilities") == []
        assert _werte(e, "CapabilityBoundingSet") == [""]


@pytest.mark.parametrize("dienst", DAUERDIENSTE)
def test_dauerdienst_neustart(dienst):
    e = _lies("owb-%s.service" % dienst)
    assert _werte(e, "Type") == ["simple"]
    assert _werte(e, "Restart") == ["on-failure"] and _werte(e, "RestartSec") == ["10"]
    assert ("WantedBy", "multi-user.target") in e["Install"]


def test_logsammler_oneshot_mit_timer():
    e = _lies("owb-logsammler.service")
    assert _werte(e, "Type") == ["oneshot"]
    assert _werte(e, "Restart") == [] and "Install" not in e
    t = _lies("owb-logsammler.timer")
    assert dict(t["Timer"]) == {"OnBootSec": "60", "OnUnitActiveSec": "60", "AccuracySec": "1s",
                                "Unit": "owb-logsammler.service"}
    assert ("WantedBy", "timers.target") in t["Install"]
