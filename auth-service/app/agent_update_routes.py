"""
Aggiornamento automatico degli agent (chiamato dagli agent, non dal browser).

  GET /_authgate/agent-update?platform=windows|linux&current=X.Y.Z
      204 = niente da fare (aggiornamento spento per il cliente, nessuna
            versione firmata, pacchetto non ancora pronto sul portale)
      200 = {"manifest": base64, "signature": base64} dell'ultima Release
  GET /_authgate/agent-update/download?platform=...&version=X.Y.Z
      il pacchetto descritto dal manifesto

Autenticazione: X-Tenant + X-Upload-Token, lo stesso token dei caricamenti
degli archivi. L'interruttore per cliente si gestisce dal pannello.

Il portale fa solo da tramite: i manifesti sono firmati al rilascio con una
chiave che il portale non ha, e l'agent installa solo ciò che è firmato
(vedi agent/auto_update.py). Il pacchetto viene scaricato da GitHub una
volta sola, in sottofondo, e poi servito dal disco.
"""
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import threading
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from . import archive_storage, github_release

agent_update_bp = Blueprint("agent_update", __name__, url_prefix="/_authgate/agent-update")
log = logging.getLogger(__name__)

PACKAGES = {"windows": "logplatform-agent-windows.zip", "linux": "logplatform-agent-linux.tar.gz"}
CACHE_DIR = Path(os.environ.get("AGENT_UPDATE_CACHE_DIR", "/data/agent-updates"))
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_downloading = set()
_downloading_lock = threading.Lock()


def _authenticated_tenant() -> str | None:
    tenant = request.headers.get("X-Tenant", "")
    token = request.headers.get("X-Upload-Token", "")
    if tenant and archive_storage.verify_upload_token(tenant, token):
        return tenant
    return None


def _signed_release(platform: str):
    """(cartella cache, manifesto, firma, manifesto decodificato, asset del pacchetto) oppure None."""
    release = github_release.latest_release()
    version = str(release.get("tag_name", "")).lstrip("v")
    if not _VERSION_RE.match(version):
        return None
    name = PACKAGES[platform]
    pkg = github_release.find_asset(release, name)
    man = github_release.find_asset(release, name + ".manifest.json")
    sig = github_release.find_asset(release, name + ".sig")
    if not (pkg and man and sig):
        return None  # Release senza firma (precedente all'aggiornamento automatico)
    folder = CACHE_DIR / version / platform
    if not (folder / "manifest.json").exists() or not (folder / "manifest.sig").exists():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "manifest.sig").write_bytes(github_release.download_asset(sig))
        (folder / "manifest.json").write_bytes(github_release.download_asset(man))
    manifest_bytes = (folder / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("version") != version or manifest.get("platform") != platform:
        return None
    return folder, manifest_bytes, (folder / "manifest.sig").read_bytes(), manifest, pkg


def _fetch_package(folder: Path, manifest: dict, asset: dict) -> None:
    key = str(folder)
    try:
        data = github_release.download_asset(asset, timeout=600)
        if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise ValueError("il pacchetto su GitHub non corrisponde al manifesto")
        tmp = folder / "package.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, folder / "package")
        # Tiene solo la versione corrente
        for old in CACHE_DIR.iterdir():
            if old.is_dir() and old.name != folder.parent.name:
                shutil.rmtree(old, ignore_errors=True)
    except Exception as e:
        log.warning("aggiornamento agent: download del pacchetto %s non riuscito: %s", key, e)
    finally:
        with _downloading_lock:
            _downloading.discard(key)


def _package_ready(folder: Path, manifest: dict, asset: dict) -> bool:
    if (folder / "package").exists():
        return True
    key = str(folder)
    with _downloading_lock:
        if key in _downloading:
            return False
        _downloading.add(key)
    threading.Thread(target=_fetch_package, args=(folder, manifest, asset), daemon=True).start()
    return False


@agent_update_bp.get("")
def check_update():
    tenant = _authenticated_tenant()
    if not tenant:
        return jsonify(error="token non valido"), 403
    platform = request.args.get("platform", "")
    if platform not in PACKAGES:
        return jsonify(error="piattaforma non valida"), 400
    if not archive_storage.agent_auto_update_enabled(tenant):
        return "", 204
    try:
        found = _signed_release(platform)
    except (github_release.GitHubReleaseError, OSError, ValueError) as e:
        log.warning("aggiornamento agent: release non disponibile: %s", e)
        return "", 204
    if not found:
        return "", 204
    folder, manifest_bytes, signature, manifest, asset = found
    if not _package_ready(folder, manifest, asset):
        return "", 204  # lo sta scaricando: l'agent riproverà al prossimo controllo
    return jsonify(manifest=base64.b64encode(manifest_bytes).decode(),
                   signature=base64.b64encode(signature).decode())


@agent_update_bp.get("/download")
def download_update():
    tenant = _authenticated_tenant()
    if not tenant:
        return jsonify(error="token non valido"), 403
    platform = request.args.get("platform", "")
    version = request.args.get("version", "")
    if platform not in PACKAGES or not _VERSION_RE.match(version):
        return jsonify(error="parametri non validi"), 400
    if not archive_storage.agent_auto_update_enabled(tenant):
        return jsonify(error="aggiornamento automatico spento per questo cliente"), 403
    path = CACHE_DIR / version / platform / "package"
    if not path.is_file():
        return jsonify(error="versione non disponibile"), 404
    return send_file(path, mimetype="application/octet-stream", as_attachment=True,
                     download_name=PACKAGES[platform])
