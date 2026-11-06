#!/bin/bash
# Pre-Consume-Skript für Paperless-ngx: entfernt leere Seiten aus Scans des Daemons.
#
# Vorlage: Paperless-ngx-Wiki, „Pre-Consume Script Examples" -> „Removing Blank Pages"
# (Ghostscript ink_cov + qpdf). Abweichung: entschieden wird nach dem Schwarzanteil K,
# nicht nach der CMYK-Summe. Bei Farbscans misst ink_cov den Papierton mit; leere
# Seiten kamen beim DS-560 auf ~20 % Summe, aber nur ~0,02 % K (Text: ~1,9 % K).
#
# Einbinden: PAPERLESS_PRE_CONSUME_SCRIPT=/pfad/zu/remove-blank-pages.sh
# Schwelle anpassbar über BLANK_PAGE_MAX_K (Prozent Schwarz, Standard 0.2).
#
# Schutzregeln:
# - nur Dateien des Daemons (scan_*.pdf), alles andere bleibt unangetastet;
# - nie alle Seiten entfernen;
# - nur DOCUMENT_WORKING_PATH ändern (siehe Paperless-Doku zu Pre-Consume-Skripten).
set -euo pipefail
export LC_ALL=C

IN="${DOCUMENT_WORKING_PATH:?DOCUMENT_WORKING_PATH fehlt}"
NAME="$(basename "${DOCUMENT_SOURCE_PATH:-$IN}")"
MAX_K="${BLANK_PAGE_MAX_K:-0.2}"

case "$NAME" in
  scan_*.pdf) ;;
  *) exit 0 ;;
esac

TYPE="$(file -b "$IN")"
if [ "${TYPE%%,*}" != "PDF document" ]; then
  echo "remove-blank-pages: $NAME übersprungen (kein PDF: $TYPE)"
  exit 0
fi

PAGES="$(pdfinfo "$IN" | awk '/^Pages:/ {print $2}')"

# ink_cov gibt pro Seite eine Zeile „C M Y K CMYK OK" aus.
KEEP="$(gs -q -o - -sDEVICE=ink_cov "$IN" | awk -v max="$MAX_K" -v name="$NAME" '
  /CMYK/ {
    n++
    keep = ($4 > max)
    printf "remove-blank-pages: %s Seite %d K=%.3f %s\n", name, n, $4,
           keep ? "behalten" : "leer, entfernt" > "/dev/stderr"
    if (keep) { printf "%s%d", sep, n; sep = "," }
  }')"

if [ -z "$KEEP" ]; then
  echo "remove-blank-pages: $NAME alle $PAGES Seiten leer — unverändert gelassen"
  exit 0
fi
if [ "$(echo "$KEEP" | tr ',' '\n' | wc -l)" -eq "$PAGES" ]; then
  exit 0
fi

qpdf "$IN" --warning-exit-0 --replace-input --pages . "$KEEP" --
echo "remove-blank-pages: $NAME behalten: Seiten $KEEP von $PAGES"
