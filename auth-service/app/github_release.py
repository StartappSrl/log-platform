"""
Recupera lo ZIP con l'agent Windows (cartella completa PyInstaller
--onedir: eseguibile + dipendenze) dall'ultima Release GitHub del repo
principale, per servirlo dal pannello. Le stesse funzioni servono anche
all'aggiornamento automatico (agent_update_routes.py), che dalla Release
prende anche il pacchetto Linux e i manifesti firmati.

NOTA: usiamo --onedir (non --onefile) perche' un singolo eseguibile
autoestraente e' notoriamente inaffidabile per i SERVIZI Windows: la
decompressione all'avvio puo' superare il timeout di 30s del Service
Control Manager, causando l'errore 1053 "il servizio non ha risposto in
tempo utile" - riscontrato testando dal vivo un'installazione reale.
"""
import os
import threading
import time

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # es. "StartappSrl/log-platform"
ZIP_ASSET_NAME = "logplatform-agent-windows.zip"
SETUP_ASSET_NAME = "logplatform-agent-setup.exe"
RELEASE_CACHE_SECONDS = 600

_HDRS = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {GITHUB_TOKEN}"}
_release_cache = {"at": 0.0, "data": None}
_release_lock = threading.Lock()


class GitHubReleaseError(Exception):
    pass


def latest_release(max_age: float = RELEASE_CACHE_SECONDS) -> dict:
    """Dati dell'ultima Release, tenuti in memoria per qualche minuto: gli agent
    chiedono spesso e GitHub limita le chiamate."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise GitHubReleaseError("GITHUB_TOKEN o GITHUB_REPO non configurati su auth-service")
    with _release_lock:
        if _release_cache["data"] is not None and time.monotonic() - _release_cache["at"] < max_age:
            return _release_cache["data"]
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers=_HDRS, timeout=15,
        )
    except requests.exceptions.RequestException as e:
        raise GitHubReleaseError(f"impossibile raggiungere GitHub: {e}")
    if not resp.ok:
        raise GitHubReleaseError(f"GitHub releases/latest -> {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    with _release_lock:
        _release_cache.update(at=time.monotonic(), data=data)
    return data


def find_asset(release: dict, name: str) -> dict | None:
    return next((a for a in release.get("assets", []) if a.get("name") == name), None)


def download_asset(asset: dict, timeout: float = 60) -> bytes:
    try:
        asset_resp = requests.get(
            asset["url"],
            headers={**_HDRS, "Accept": "application/octet-stream"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise GitHubReleaseError(f"impossibile scaricare l'asset da GitHub: {e}")
    if not asset_resp.ok:
        raise GitHubReleaseError(f"download asset -> {asset_resp.status_code}: {asset_resp.text[:300]}")
    return asset_resp.content


def get_windows_release_zip_bytes() -> bytes:
    """Scarica l'ultimo logplatform-agent-windows.zip pubblicato come
    Release GitHub (contiene la cartella completa dell'agent, non un
    singolo .exe)."""
    release = latest_release(max_age=0)
    asset = find_asset(release, ZIP_ASSET_NAME)
    if not asset:
        raise GitHubReleaseError(
            f"Nessun asset '{ZIP_ASSET_NAME}' trovato nell'ultima release '{release.get('tag_name')}'. "
            "La build Windows potrebbe non essere ancora stata pubblicata per questa versione."
        )
    return download_asset(asset)


def get_windows_setup_exe_bytes() -> bytes:
    """Ultimo logplatform-agent-setup.exe (installatore unico, uguale per tutti i clienti)."""
    release = latest_release(max_age=0)
    asset = find_asset(release, SETUP_ASSET_NAME)
    if not asset:
        raise GitHubReleaseError(
            f"Nessun '{SETUP_ASSET_NAME}' nell'ultima release '{release.get('tag_name')}': "
            "l'installatore unico esiste dalle versioni successive alla 0.17.13."
        )
    return download_asset(asset, timeout=120)
