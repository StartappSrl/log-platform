# Containerizza l'agent Linux (agent.py) per l'esecuzione su NAS che
# supportano Docker: Synology (Container Manager, DSM 7+) e QNAP
# (Container Station). Funziona anche su qualunque altro host Docker.
FROM python:3.12-alpine

WORKDIR /app
COPY agent.py gelf_transport.py ./

# Le seguenti cartelle vanno montate come volumi (vedi
# docker-compose.nas.yml.example):
#   /config      -> agent.ini + certificati (ca.pem, <tenant>.pem, <tenant>-key.pem)
#   /logs        -> log del NAS da monitorare (sola lettura)
VOLUME ["/config", "/logs"]

ENTRYPOINT ["python3", "agent.py", "--config", "/config/agent.ini"]
