"""
Controllo: ai-service con la libreria anthropic vera (nessuna chiamata in rete, le risposte arrivano da un
trasporto simulato). Verifica la richiesta che parte davvero verso l'API (modello, sforzo, ripiego automatico,
nessun parametro di campionamento) e come vengono trattate le risposte: testo normale, rifiuto del modello,
errore dell'API, token del webhook sbagliato.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import REPO  # noqa: E402

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-prova")
os.environ.setdefault("AI_SERVICE_WEBHOOK_TOKEN", "token-prova")
os.environ["TENANT_WEBHOOKS_PATH"] = os.path.join(REPO, "non-esiste.json")
os.environ.pop("AI_SERVICE_MODEL", None)
sys.path.insert(0, os.path.join(REPO, "ai-service"))

import httpx2  # noqa: E402  (dipendenza della libreria anthropic)
from anthropic import Anthropic, DefaultHttpxClient  # noqa: E402

import app as ai  # noqa: E402

falliti = []
richieste = []


def passo(nome, cond, dett=""):
    print(("OK    " if cond else "ERRATO") + " " + nome + (f" | {dett}" if not cond else ""))
    if not cond:
        falliti.append(nome)


def risposta(stop_reason="end_turn", testo="1) Severita': media\n2) Causa: prova"):
    content = [{"type": "thinking", "thinking": "", "signature": "x"}]
    if testo:
        content.append({"type": "text", "text": testo})
    return {
        "id": "msg_prova", "type": "message", "role": "assistant", "model": ai.MODEL,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def client_simulato(status, corpo):
    def handler(request):
        richieste.append(request)
        return httpx2.Response(status, json=corpo)
    return Anthropic(api_key="sk-ant-prova", max_retries=0,
                     http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


ALLARME = {"event": {"event_definition_title": "Accessi falliti", "message": "10 login falliti",
                     "fields": {"host": "PC-01"}},
           "backlog": [{"message": {"message": "Failed password for admin"}}]}
web = ai.app.test_client()

# 1. Risposta normale
ai.client = client_simulato(200, risposta())
r = web.post("/webhook/cliente-prova", json=ALLARME, headers={"X-Webhook-Token": "token-prova"})
passo("risposta 200 dal webhook", r.status_code == 200, r.status_code)
passo("triage = testo del modello (il blocco di ragionamento e' ignorato)",
      r.get_json().get("triage", "").startswith("1) Severita'"), r.get_json())
req = richieste[-1]
body = json.loads(req.content)
passo("endpoint Messages", req.url.path == "/v1/messages", req.url.path)
passo("modello predefinito claude-opus-5-5", body.get("model") == "claude-opus-5-5", body.get("model"))
passo("sforzo basso", body.get("output_config") == {"effort": "low"}, body.get("output_config"))
passo("ripiego automatico attivo", body.get("fallbacks") == "default", body.get("fallbacks"))
passo("beta del ripiego nell'intestazione", ai.FALLBACK_BETA in req.headers.get("anthropic-beta", ""),
      req.headers.get("anthropic-beta"))
passo("nessun parametro di campionamento",
      not any(k in body for k in ("temperature", "top_p", "top_k")), sorted(body))
passo("max_tokens con spazio per il ragionamento", body.get("max_tokens", 0) >= 4000, body.get("max_tokens"))
passo("dati dell'allarme nel prompt", "Failed password for admin" in body["messages"][0]["content"])

# 2. Rifiuto del modello
ai.client = client_simulato(200, risposta(stop_reason="refusal", testo=None))
r = web.post("/webhook/cliente-prova", json=ALLARME, headers={"X-Webhook-Token": "token-prova"})
passo("rifiuto -> messaggio chiaro, nessun errore", r.status_code == 200
      and "rifiutato" in r.get_json().get("triage", ""), r.get_json())

# 3. Errore dell'API (es. chiave sbagliata): il webhook risponde comunque
ai.client = client_simulato(401, {"type": "error", "error": {"type": "authentication_error", "message": "x"}})
r = web.post("/webhook/cliente-prova", json=ALLARME, headers={"X-Webhook-Token": "token-prova"})
passo("errore API -> triage non disponibile", r.status_code == 200
      and r.get_json().get("triage", "").startswith("(triage AI non disponibile"), r.get_json())

# 4. Modello vecchio impostato a mano: niente ripiego (non lo accetterebbe)
ai.MODEL = "claude-sonnet-4-6"
ai.client = client_simulato(200, risposta())
web.post("/webhook/cliente-prova", json=ALLARME, headers={"X-Webhook-Token": "token-prova"})
body = json.loads(richieste[-1].content)
passo("modello vecchio: senza fallbacks", "fallbacks" not in body and body.get("model") == "claude-sonnet-4-6",
      sorted(body))

# 5. Token sbagliato
n = len(richieste)
r = web.post("/webhook/cliente-prova", json=ALLARME, headers={"X-Webhook-Token": "sbagliato"})
passo("token sbagliato -> 401 senza chiamare Claude", r.status_code == 401 and len(richieste) == n, r.status_code)

print()
if falliti:
    print(f"{len(falliti)} controlli falliti")
    sys.exit(1)
print("ai-service: tutti i controlli superati")
