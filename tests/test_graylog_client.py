"""
Test di non-regressione per il bug critico di isolamento tenant trovato
durante questa sessione: il parametro 'streams' nell'API di ricerca di
Graylog viene ignorato silenziosamente - va usato 'filter=streams:<ID>'.
Se qualcuno in futuro "semplifica" il codice tornando a 'streams=',
questo test lo blocca prima che arrivi in produzione.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auth-service", "app"))


class FakeRequestCapture:
    """Sostituisce _request per catturare i parametri con cui viene
    chiamata, senza fare vere chiamate di rete."""
    def __init__(self):
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, "params": kwargs.get("params", {})})
        return {"messages": [], "total_results": 0}


def test_search_uses_filter_not_streams_param(monkeypatch):
    """Il bug critico: 'streams=' come parametro GET viene ignorato da
    Graylog senza errore, restituendo i dati di TUTTI i tenant invece di
    uno solo. Deve usare 'filter=streams:<ID>'."""
    import graylog_client as gl

    fake = FakeRequestCapture()
    monkeypatch.setattr(gl, "_request", fake)

    gl.search("stream-id-di-prova", query="*", range_minutes=60, limit=100)

    assert len(fake.calls) == 1
    params = fake.calls[0]["params"]

    assert "streams" not in params, (
        "REGRESSIONE: il parametro 'streams' e' tornato - Graylog lo ignora "
        "silenziosamente, causando la fuga di dati tra tenant diversi (bug gia' "
        "trovato e corretto in produzione)."
    )
    assert params.get("filter") == "streams:stream-id-di-prova", (
        "Il filtro per stream deve essere 'filter=streams:<ID>', l'unico "
        "modo che Graylog rispetta davvero per questo endpoint."
    )
