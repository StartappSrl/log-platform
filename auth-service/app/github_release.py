"""
Recupera lo ZIP con l'agent Windows (cartella completa PyInstaller
--onedir: eseguibile + dipendenze) dall'ultima Release GitHub del repo
principale, per servirlo dal pannello.

NOTA: usiamo --onedir (non --onefile) perche' un singolo eseguibile
autoestraente e' notoriamente inaffidabile per i SERVIZI Windows: la
decompressione all'avvio puo' superare il timeout di 30s del Service
Control Manager, causando l'errore 1053 "il servizio non ha risposto in
tempo utile" - riscontrato testando dal vivo un'installazione reale.
"""
import os

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # es. "StartappSrl/log-platform"
ZIP_ASSET_NAME = "logplatform-agent-windows.zip"

_HDRS = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {GITHUB_TOKEN}"}


class GitHubReleaseError(Exception):
    pass


def get_windows_release_zip_bytes() -> bytes:
    """Scarica l'ultimo logplatform-agent-windows.zip pubblicato come
    Release GitHub (contiene la cartella completa dell'agent, non un
    singolo .exe)."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise GitHubReleaseError("GITHUB_TOKEN o GITHUB_REPO non configurati su auth-service")

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers=_HDRS, timeout=15,
        )
    except requests.exceptions.RequestException as e:
        raise GitHubReleaseError(f"impossibile raggiungere GitHub: {e}")

    if not resp.ok:
        raise GitHubReleaseError(f"GitHub releases/latest -> {resp.status_code}: {resp.text[:300]}")

    release = resp.json()
    asset = next((a for a in release.get("assets", []) if a.get("name") == ZIP_ASSET_NAME), None)
    if not asset:
        raise GitHubReleaseError(
            f"Nessun asset '{ZIP_ASSET_NAME}' trovato nell'ultima release '{release.get('tag_name')}'. "
            "La build Windows potrebbe non essere ancora stata pubblicata per questa versione."
        )

    try:
        asset_resp = requests.get(
            asset["url"],
            headers={**_HDRS, "Accept": "application/octet-stream"},
            timeout=60,
        )
    except requests.exceptions.RequestException as e:
        raise GitHubReleaseError(f"impossibile scaricare l'asset da GitHub: {e}")

    if not asset_resp.ok:
        raise GitHubReleaseError(f"download asset -> {asset_resp.status_code}: {asset_resp.text[:300]}")

    return asset_resp.content
