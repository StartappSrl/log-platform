#!/usr/bin/env python3
"""
Prepara i pacchetti dell'agent per l'aggiornamento automatico (usato dal workflow
build-windows-agent.yml al momento del rilascio).

  python scripts/rilascio_agent.py versione <X.Y.Z>
      scrive agent/agent_version.py con il numero di versione
  python scripts/rilascio_agent.py linux <X.Y.Z> <uscita.tar.gz>
      crea il pacchetto Linux (solo i file .py che servono su Linux)
  python scripts/rilascio_agent.py firma <file> <linux|windows> <X.Y.Z>
      scrive <file>.manifest.json e <file>.sig (firma Ed25519 del manifesto)
      con la chiave privata in AGENT_SIGNING_KEY (base64 dei 32 byte del seme)

La chiave pubblica corrispondente è in agent/auto_update.py.
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENT_DIR = REPO / "agent"
LINUX_FILES = ("agent.py", "gelf_transport.py", "local_archive.py", "inventory.py", "syslog_relay.py",
               "syslog_parser.py", "auto_update.py", "ed25519_verify.py", "agent_version.py")


def _check_version(v: str) -> str:
    v = v.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise SystemExit(f"versione non valida: {v!r} (atteso X.Y.Z)")
    return v


def version_file(version: str) -> str:
    return ("# Scritto dal workflow di rilascio (scripts/rilascio_agent.py)\n"
            f'AGENT_VERSION = "{_check_version(version)}"\n')


def write_version(version: str) -> None:
    (AGENT_DIR / "agent_version.py").write_text(version_file(version), encoding="utf-8")


def build_linux(version: str, out: Path) -> None:
    _check_version(version)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for name in LINUX_FILES:
            # agent_version.py viene sempre dal numero passato: il pacchetto non può
            # dichiarare una versione diversa da quella del manifesto.
            data = (version_file(version).encode() if name == "agent_version.py"
                    else (AGENT_DIR / name).read_bytes())
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    out.write_bytes(buf.getvalue())


def manifest_for(path: Path, platform: str, version: str) -> bytes:
    data = path.read_bytes()
    return json.dumps({
        "platform": platform,
        "version": _check_version(version),
        "file": path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, sort_keys=True).encode()


def sign(path: Path, platform: str, version: str) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if platform not in ("linux", "windows"):
        raise SystemExit("piattaforma: linux o windows")
    seed_b64 = os.environ.get("AGENT_SIGNING_KEY", "").strip()
    if not seed_b64:
        raise SystemExit("AGENT_SIGNING_KEY non impostata: impossibile firmare il rilascio")
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed_b64))
    manifest = manifest_for(path, platform, version)
    Path(f"{path}.manifest.json").write_bytes(manifest)
    Path(f"{path}.sig").write_bytes(key.sign(manifest))


def main(argv: list) -> None:
    if len(argv) == 2 and argv[0] == "versione":
        write_version(argv[1])
    elif len(argv) == 3 and argv[0] == "linux":
        build_linux(argv[1], Path(argv[2]))
    elif len(argv) == 4 and argv[0] == "firma":
        sign(Path(argv[1]), argv[2], argv[3])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
