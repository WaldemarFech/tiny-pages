# Tiny Pages

Kleine statische Webseiten ohne Build-Schritt.

| Seite | Inhalt | Live |
| --- | --- | --- |
| `netzfilter-labor/` | Interaktives Modell für Netzspannung und Störimpulse | [Netzfilter-Labor](https://netzfilter.wfech.de/) |
| `deepseek-offpeak/` | Dashboard für Anbieterpreise und Router-Entscheidungen | [OpenRouter-Dashboard](https://wannkannichballern.wfech.de/) |
| `deepseek-offpeak/modelle/` | Modellübersicht und Rankings | [Modellübersicht](https://wannkannichballern.wfech.de/modelle/) |
| `deepseek-offpeak/preis-uhr/` | Preisfenster in Berliner Zeit und UTC | [DeepSeek Preis-Uhr](https://wannkannichballern.wfech.de/preis-uhr/) |

## Lokal öffnen

Im Repository-Stammverzeichnis einen statischen HTTP-Server starten und
[die Übersichtsseite öffnen](http://localhost:8000/).

```sh
python -m http.server --bind 127.0.0.1 8000
```

Das Netzfilter-Labor und die Preis-Uhr funktionieren mit den enthaltenen
Dateien. Das Dashboard und die Modellübersicht erwarten zusätzlich die
JSON-Feeds `router.json` und `models_trending.json` in `deepseek-offpeak/`.
Diese Laufzeitdaten sind von Git ausgeschlossen.

## Tests

```sh
cd deepseek-offpeak
npm test
```

Das optionale Skript `scripts/update_holidays.py` im DeepSeek-Verzeichnis
aktualisiert die Feiertagsliste über die öffentliche Timor-API.
