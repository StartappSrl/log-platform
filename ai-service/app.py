"""
ai-service — riceve le notifiche di allarme da Graylog (HTTP notification),
chiede a Claude un triage rapido (severità reale, causa probabile, azione
consigliata) e lo inoltra al webhook del tenant (Slack/Teams/altro), se
configurato in tenant_webhooks.json.

Endpoint: POST /webhook/<tenant>
Header richiesto: X-Webhook-Token: <AI_SERVICE_WEBHOOK_TOKEN>
"""
import json
import logging
import os

import requests
from anthropic import Anthropic
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ai-service")

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
MODEL = os.environ.get("AI_SERVICE_MODEL", "claude-opus-5-5")
WEBHOOK_TOKEN = os.environ["AI_SERVICE_WEBHOOK_TOKEN"]
TENANT_WEBHOOKS_PATH = os.environ.get("TENANT_WEBHOOKS_PATH", "/app/tenant_webhooks.json")

# Timeout sotto i 30 secondi del worker gunicorn: se Claude non risponde in tempo
# il webhook risponde comunque (con "triage non disponibile") invece di far
# uccidere il worker da gunicorn.
client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=25.0, max_retries=1)

# Modelli che accettano il ripiego automatico lato server: se il modello rifiuta
# la richiesta per le sue regole di sicurezza (capita con log che contengono
# comandi o payload d'attacco), l'API la ripete su un altro modello nella stessa
# chiamata. Con un modello piu' vecchio impostato in AI_SERVICE_MODEL il
# parametro non viene inviato.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MODELS_WITH_FALLBACK = {
    "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
}


def _load_tenant_webhooks() -> dict:
    if not os.path.exists(TENANT_WEBHOOKS_PATH):
        return {}
    with open(TENANT_WEBHOOKS_PATH) as f:
        return json.load(f)


def _extract_alert_summary(payload: dict) -> str:
    """Estrae dal payload di notifica Graylog gli elementi utili al triage."""
    event = payload.get("event", {}) or {}
    definition = event.get("event_definition_title") or payload.get("event_definition", {}).get("title")
    message = event.get("message") or ""
    fields = event.get("fields", {}) or {}
    backlog = payload.get("backlog", []) or []
    sample_lines = []
    for entry in backlog[:5]:
        msg = entry.get("message", {})
        sample_lines.append(str(msg.get("message") or msg.get("full_message") or ""))

    return (
        f"Definizione allarme: {definition}\n"
        f"Messaggio evento: {message}\n"
        f"Campi: {json.dumps(fields, ensure_ascii=False)}\n"
        f"Esempi di log correlati (max 5):\n" + "\n".join(f"- {l}" for l in sample_lines)
    )


def _triage_with_claude(tenant: str, summary: str) -> str:
    prompt = f"""Sei un analista SOC. Analizza questo allarme generato da Graylog per il
cliente '{tenant}' e rispondi in italiano, in modo conciso (max 8 righe), con:
1) Severità reale stimata (bassa/media/alta/critica) e perché
2) Causa probabile
3) Azione consigliata immediata
4) Se ritieni sia un falso positivo, dillo esplicitamente

Dati dell'allarme:
{summary}
"""
    extra = {}
    if MODEL in MODELS_WITH_FALLBACK:
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    resp = client.beta.messages.create(
        model=MODEL,
        # Spazio anche per il ragionamento (sempre attivo sui modelli recenti):
        # la lunghezza della risposta la fissa il prompt (max 8 righe).
        max_tokens=16000,
        # Sforzo basso: triage breve e veloce, Graylog aspetta la risposta.
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
        **extra,
    )
    if resp.stop_reason == "refusal":
        return "(triage AI non disponibile: il modello ha rifiutato di analizzare questo allarme)"
    return "".join(block.text for block in resp.content if block.type == "text").strip()


@app.post("/webhook/<tenant>")
def webhook(tenant):
    if request.headers.get("X-Webhook-Token") != WEBHOOK_TOKEN:
        return jsonify(error="non autorizzato"), 401

    payload = request.get_json(force=True, silent=True) or {}
    summary = _extract_alert_summary(payload)

    try:
        triage = _triage_with_claude(tenant, summary)
    except Exception as e:
        log.exception("Errore chiamando Claude")
        triage = f"(triage AI non disponibile: {e})"

    log.info("Triage per tenant=%s:\n%s", tenant, triage)

    webhooks = _load_tenant_webhooks()
    target = webhooks.get(tenant)
    if target:
        try:
            requests.post(target, json={"text": f"[Log Platform] Allarme per {tenant}\n\n{triage}"}, timeout=10)
        except Exception:
            log.exception("Impossibile inoltrare al webhook del tenant %s", tenant)

    return jsonify(ok=True, tenant=tenant, triage=triage)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
