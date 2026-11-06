# Laufzeit-Image für den DS-560-Daemon (GHCR -> Unraid).
#
# Keine Abhängigkeiten außer Python. Die Konfiguration wird zur Laufzeit als
# /config/config.toml eingehängt, der Zielordner (z. B. Paperless-Consume)
# unter dem Pfad, den output_dir dort nennt. Für die Suche über die MAC
# braucht der Container --network host (ARP-Cache des Hosts).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /app/src

# Kein root nötig: ARP-Sweep per UDP und TCP 1865 gehen ohne Rechte.
# Mit --user lässt sich die UID an den Zielordner anpassen (Unraid: 99:100).
USER nobody

ENTRYPOINT ["ds560d", "--config", "/config/config.toml"]
