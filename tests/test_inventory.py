"""
Test di non-regressione per il rilevamento di Windows 11: Python
platform.platform() riporta 'Windows-10' anche su Windows 11 (per
compatibilita' storica) - la build (>=22000) e' l'unico modo affidabile
per distinguerli. Bug trovato dal vivo su un PC reale (build 26200).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))


class FakeVersionInfo:
    def __init__(self, major, minor, build):
        self.major = major
        self.minor = minor
        self.build = build


def test_windows_11_detected_correctly(monkeypatch):
    import inventory

    monkeypatch.setattr(sys, "getwindowsversion", lambda: FakeVersionInfo(10, 0, 26200), raising=False)
    result = inventory._windows_os_info()
    assert "Windows 11" in result["name"], (
        f"REGRESSIONE: la build 26200 (Windows 11 reale) non viene piu' "
        f"riconosciuta come tale (risultato: {result['name']!r})"
    )


def test_windows_10_still_detected_correctly(monkeypatch):
    import inventory

    monkeypatch.setattr(sys, "getwindowsversion", lambda: FakeVersionInfo(10, 0, 19045), raising=False)
    result = inventory._windows_os_info()
    assert "Windows 10" in result["name"]
    assert "Windows 11" not in result["name"]


def test_windows_11_boundary_exact():
    """22000 e' la prima build ufficiale di Windows 11 - il confine
    esatto deve andare dalla parte giusta."""
    import inventory
    import sys as _sys

    _sys.getwindowsversion = lambda: FakeVersionInfo(10, 0, 22000)
    assert "Windows 11" in inventory._windows_os_info()["name"]

    _sys.getwindowsversion = lambda: FakeVersionInfo(10, 0, 21999)
    assert "Windows 10" in inventory._windows_os_info()["name"]
