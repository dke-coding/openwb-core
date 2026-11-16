"""Eigene Tests fuer den gepinnten Installer (Auftrag C1, 4.5), ohne Ausfuehrung des Skripts."""
import difflib
import os
import pathlib
import re
import shutil
import subprocess

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]
ORIGINAL = _REPO / "openwb-install.sh"
SKRIPT = pathlib.Path(__file__).resolve().parents[1] / "installer" / "owb-install-gepinnt.sh"

# Originalzeilen (1-basiert), die geaendert oder entfernt werden duerfen; Einfuegungen nur nach diesen Zeilen
GEAENDERT = {13, 14, 36, 114, 134, 135, 137, 138}
EINFUEGEN_NACH = {1, 4, 40, 41}
COMMIT = "cf803dc8b8b7a105697e8537b687908a11b0d020"


def _zeilen(pfad):
    # splitlines: das Original steht unter Windows (core.autocrlf) mit CRLF im Arbeitsbaum
    return pfad.read_bytes().decode("ascii").splitlines()


def _bash():
    if os.name == "nt":
        p = r"C:\Program Files\Git\bin\bash.exe"
        return p if os.path.exists(p) else None
    return shutil.which("bash")


def _code():
    """Zeilen ohne reine Kommentarzeilen."""
    return [z for z in _zeilen(SKRIPT) if not z.lstrip().startswith("#")]


def _index(muster):
    treffer = [i for i, z in enumerate(_zeilen(SKRIPT)) if re.search(muster, z) and not z.lstrip().startswith("#")]
    assert treffer, ("nicht gefunden", muster)
    return treffer[0]


def test_diff_nur_geplante_zeilen():
    alt, neu = _zeilen(ORIGINAL), _zeilen(SKRIPT)
    geaendert, einfuegen = set(), set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, alt, neu, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            geaendert.update(range(i1 + 1, i2 + 1))
        elif tag == "insert":
            einfuegen.add(i1)
    assert geaendert == GEAENDERT
    assert einfuegen <= EINFUEGEN_NACH


def test_kein_master_kein_upstream():
    text = SKRIPT.read_text(encoding="ascii")
    assert not re.search(r"master", text, re.IGNORECASE)
    assert "openWB/core" not in text


def test_klon_tag_und_commit():
    code = _code()
    assert "OPENWB_REPO=https://github.com/dke-coding/openwb-core.git" in code
    assert "OPENWB_TAG=deployed-2026-10-28" in code
    assert "OPENWB_COMMIT=" + COMMIT in code
    klon = [z for z in code if re.search(r"\bgit clone\b", z) and not z.lstrip().startswith("echo")]
    assert klon == ['\tsudo -u "$OPENWB_USER" git clone "$OPENWB_REPO" --branch "$OPENWB_TAG" "$OPENWBBASEDIR"']
    i = _index(r'OWB_HEAD=\$\(sudo -u "\$OPENWB_USER" git -C "\$OPENWBBASEDIR" rev-parse HEAD\)')
    z = _zeilen(SKRIPT)
    assert z[i + 1] == 'if [ "$OWB_HEAD" != "$OPENWB_COMMIT" ]; then'
    assert z[i + 3] == "\texit 1"


def test_remote_support():
    code = "\n".join(_code())
    assert not re.search(r"(enable|start|restart)\s+openwbRemoteSupport", code)
    assert not re.search(r"\bcp\b[^\n]*openwbRemoteSupport", code)
    assert re.search(r"^systemctl mask openwbRemoteSupport$", code, re.MULTILINE)
    z = _zeilen(SKRIPT)
    i = _index(r'is-enabled openwbRemoteSupport.*!= "masked"')
    assert z[i + 2] == "\texit 1"


def test_reihenfolge():
    reihe = [
        r"^if \(\( \$\(id -u\) != 0 \)\); then$",
        r'^if \[ -n "\$\{http_proxy:-\}\$\{https_proxy:-\}" \]; then$',
        r"^apt-get -q -y install git sudo ca-certificates \|\| exit 1$",
        r'^mkdir -p "\$\(dirname "\$OPENWBBASEDIR"\)" \|\| exit 1$',
        r"git clone",
        r"rev-parse HEAD",
        r'^bash "\$\{OPENWBBASEDIR\}/runs/install_packages.sh" \|\| exit 1$',
        r"^systemctl mask openwbRemoteSupport$",
        r"^systemctl start openwb2$",
    ]
    stellen = [_index(m) for m in reihe]
    assert stellen == sorted(stellen), stellen


def test_proxy():
    code = _code()
    text = "\n".join(code)
    assert "/etc/apt/apt.conf.d/95owb-testinstanz-proxy" in text
    assert "Acquire::http::Proxy" in text and "Acquire::https::Proxy" in text
    assert ("\techo 'Defaults env_keep += \"http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY\"' "
            '>"$OWB_PROXY_SUDO"') in code
    assert '\tif ! visudo -cf "$OWB_PROXY_SUDO" >/dev/null; then' in code
    assert "export http_proxy https_proxy HTTP_PROXY HTTPS_PROXY" in text
    # die Proxy-URL kann Zugangsdaten enthalten: nie ausgeben
    for z in code:
        if re.match(r"\s*(echo|printf)\b", z) and ">" not in z:
            assert not re.search(r"\$\{?(http|https|no)_proxy", z, re.IGNORECASE), z


@pytest.mark.skipif(_bash() is None, reason="bash nicht gefunden")
def test_bash_n():
    r = subprocess.run([_bash(), "-n", str(SKRIPT)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    assert r.returncode == 0, r.stderr


def test_keine_cr_ascii():
    daten = SKRIPT.read_bytes()
    assert b"\r" not in daten
    assert all(b < 0x80 for b in daten)
    assert daten.startswith(b"#!/bin/bash\n")


def test_root_abbruch():
    z = _zeilen(SKRIPT)
    i = _index(r"^if \(\( \$\(id -u\) != 0 \)\); then$")
    assert z[i:i + 4] == ["if (( $(id -u) != 0 )); then",
                          '\techo "this script has to be run as user root or with sudo"', "\texit 1", "fi"]
    assert i < _index(r"^apt-get")
