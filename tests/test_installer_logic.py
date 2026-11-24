"""Installatore Windows: configurazione in coda all'exe, cartella d'installazione e permessi."""
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import installer_logic as il  # noqa: E402

EXE = b"MZ" + os.urandom(4096) + b"MEI\014\013\012\013\016" + os.urandom(64)  # finto eseguibile
CONFIG = {"agent.ini": "[agent]\ntenant = acme\n", "ca.pem": b"CA", "acme.pem": b"CERT", "acme-key.pem": b"KEY"}


def test_configurazione_in_coda_all_exe_si_rilegge():
    installer = EXE + il.build_tenant_payload(CONFIG)
    letti = il.read_tenant_payload(installer)
    assert letti["agent.ini"].startswith(b"[agent]")
    assert letti["acme-key.pem"] == b"KEY"
    assert set(letti) == set(CONFIG)


def test_lettura_dal_file(tmp_path):
    f = tmp_path / "setup.exe"
    f.write_bytes(EXE + il.build_tenant_payload(CONFIG))
    assert il.read_tenant_payload_from_file(str(f))["ca.pem"] == b"CA"


def test_installatore_generico_senza_configurazione():
    assert il.read_tenant_payload(EXE) is None


def test_nomi_pericolosi_rifiutati():
    with pytest.raises(ValueError):
        il.build_tenant_payload({"../evil.exe": b"x"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("agent.ini", "x")
        z.writestr("..\\logplatform-agent.exe", "x")
    blob = buf.getvalue()
    falso = EXE + blob + il._TRAILER.pack(len(blob)) + il.PAYLOAD_MAGIC
    with pytest.raises(ValueError):
        il.read_tenant_payload(falso)


def test_lunghezza_falsa_rifiutata():
    with pytest.raises(ValueError):
        il.read_tenant_payload(b"xx" + il._TRAILER.pack(10 ** 9) + il.PAYLOAD_MAGIC)


def test_cartella_predefinita_in_program_files():
    assert il.default_install_dir({"ProgramFiles": r"C:\Program Files"}) == r"C:\Program Files\LogPlatform Agent"
    # anche se l'installatore fosse a 32 bit, va nella Program Files vera
    assert il.default_install_dir({"ProgramFiles": r"C:\Program Files (x86)",
                                   "ProgramW6432": r"C:\Program Files"}) == r"C:\Program Files\LogPlatform Agent"


def test_cartella_della_vecchia_installazione():
    assert il.exe_dir_from_image_path(r'"C:\agent-startapp-windows\logplatform-agent.exe"') == r"C:\agent-startapp-windows"
    assert il.exe_dir_from_image_path(r'"C:\Program Files\LogPlatform Agent\logplatform-agent.exe" x') == \
        r"C:\Program Files\LogPlatform Agent"
    assert il.exe_dir_from_image_path(None) is None


def test_permessi_solo_system_e_amministratori():
    proprietario, permessi, figli = il.build_icacls_lockdown_commands(r"C:\Program Files\LogPlatform Agent")
    assert proprietario[:4] == ["icacls", r"C:\Program Files\LogPlatform Agent", "/setowner", "*S-1-5-32-544"]
    assert "/inheritance:r" in permessi and "/grant:r" in permessi
    assert [a for a in permessi if a.startswith("*S-")] == ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]
    tutti = [a for c in (proprietario, permessi, figli) for a in c]
    assert not any("S-1-5-32-545" in a or "S-1-5-11" in a for a in tutti)  # né Users né Authenticated Users
    assert figli[1] == r"C:\Program Files\LogPlatform Agent" + "\\*" and "/reset" in figli
