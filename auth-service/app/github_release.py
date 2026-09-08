"""
Recupera l'eseguibile Windows dell'agent (logplatform-agent.exe) dall'ultima
Release GitHub del repo principale, per servirlo dal pannello. E' un
binario UNICO uguale per tutti i tenant (a differenza del pacchetto
tar.gz, specifico per cliente con i suoi certificati) - qui lo scarichiamo
dall'API di GitHub usando un token con permesso di lettura sul repo (serve
perche' il repo e' privato).
"""
import os

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # es. "StartappSrl/log-platform"
EXE_ASSET_NAME = "logplatform-agent.exe"

_HDRS = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {GITHUB_TOKEN}"}


class GitHubReleaseError(Exception):
    pass


def get_windows_exe_bytes() -> bytes:
    """Scarica l'ultimo logplatform-agent.exe pubblicato come Release GitHub.
    Solleva GitHubReleaseError con un messaggio chiaro se qualcosa non va
    (repo non configurato, token mancante/scaduto, release non trovata,
    asset non trovato)."""
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
    asset = next((a for a in release.get("assets", []) if a.get("name") == EXE_ASSET_NAME), None)
    if not asset:
        raise GitHubReleaseError(
            f"Nessun asset '{EXE_ASSET_NAME}' trovato nell'ultima release '{release.get('tag_name')}'. "
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
