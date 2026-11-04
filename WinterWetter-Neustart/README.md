# WinterWetter Pro – Neustart (Entwicklungsversion)

Schlanke Flask-Anwendung ohne Kunden- oder Objektverwaltung.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set WW_ADMIN_KEY=EIN_SICHERES_EIGENES_PASSWORT
set WW_ENABLE_SCHEDULER=1
python app.py
```

Linux/macOS: `source .venv/bin/activate` und Umgebungsvariablen mit `export` setzen.
Danach `http://127.0.0.1:5000` im Browser öffnen.

## Betrieb und Sicherheitsgrenzen

- Dies ist eine **Entwicklungsversion**, noch nicht für rechtsverbindliche Kundennachweise freigegeben.
- Der stündliche Sammler funktioniert **nur solange der Python-Prozess läuft**. Für 24/7-Betrieb wird ein dauerhaft laufender Server benötigt.
- Der Scheduler darf in einer Produktionsumgebung nicht mehrfach parallel gestartet werden (nur ein Worker oder separater Jobrunner).
- Das SQLite-Backup wird in `data/backups/` angelegt; es ist **keine externe/offsite Sicherung**. Diese muss zusätzlich konfiguriert werden.
- Datenquellen können ausfallen oder HTTP 429 liefern. Fehler erscheinen im Status und werden nicht als Erfolg ausgegeben.
- Open-Meteo-Modellwerte sind keine Vor-Ort-Messwerte. Die API-Nutzungsbedingungen sind vor gewerblicher Nutzung zu prüfen; erforderlichenfalls muss ein passender kommerzieller Zugriff eingerichtet werden.
- Die vorläufigen Koordinaten sind **keine verifizierten Gipfelkoordinaten**; über `REMSCHEID_LAT`, `REMSCHEID_LON`, `WERMELSKIRCHEN_LAT`, `WERMELSKIRCHEN_LON` vor dem Betrieb exakt festlegen.
- Der DWD-CAP-Abruf hängt von der tatsächlichen Verfügbarkeit und dem Format der DWD-ZIP-Datei ab. Es werden nur Warnungen mit passendem Gemeindenamen in `areaDesc` zugeordnet; das muss noch gegen amtliche Warngebietskennungen abgesichert werden.
- PDF-Werte bestehen aus Archivdaten. Fehlende Warnungen im Archiv bedeuten **nicht** automatisch keine amtliche Warnung.
- Historische Modellzeiten sind Abrufstände, keine unveränderlichen Messwerte. Prognosen können sich ändern.
- Für den Winterdienst gilt: Warnungen belegen keine tatsächliche Glätte und Wettermodelle keinen ausgeführten Einsatz.

## Tests

```bash
python -m compileall -q app.py
```

Manueller Test des Abrufs (mit gestarteter App):

```bash
curl -X POST http://127.0.0.1:5000/api/collect -H "X-Admin-Key: EIN_SICHERES_EIGENES_PASSWORT"
```

Fehler bei Datenquellen sind ausdrücklich im JSON-Feld `errors` zu prüfen.

## Kostenloses öffentliches Archiv mit GitHub Actions und Pages

Der Workflow `.github/workflows/weather-archive.yml` sammelt täglich um **05:17 und
17:17 UTC** (Winter: 06:17/18:17, Sommer: 07:17/19:17 in Deutschland).
Er kann unter **Actions → Wetterarchiv zweimal täglich → Run workflow** manuell
 gestartet werden. GitHub kann geplante Läufe verzögern; bei öffentlichen Repositories
werden Zeitpläne nach 60 Tagen ohne Repository-Aktivität gegebenenfalls deaktiviert.
Den Workflow dann erneut aktivieren. Es gibt keine Verfügbarkeitsgarantie.

Einrichtung nach Übernahme der Dateien auf den Default-Branch `main`:

1. GitHub Actions im Repository erlauben.
2. Unter **Settings → Pages → Build and deployment → Source** die Option
   **GitHub Actions** auswählen.
3. Den Workflow einmal manuell starten und beide Jobs prüfen.
4. Die öffentliche Adresse dem erfolgreichen `deploy`-Job entnehmen.

Kein eigener Server und kein Admin-Schlüssel sind hierfür nötig. Die normale
Nutzung von Standard-GitHub-Runnern und Pages ist bei öffentlichen Repositories
kostenlos; aktuelle GitHub-Limits gelten. Keine kostenpflichtigen Runner auswählen.
**Alle Archivdaten und PDFs sind öffentlich.** Keine Kundendaten hinzufügen.

Die Anwendung bleibt unverändert lokal nutzbar. `publish.py` ruft dieselben
Sammler und PDF-Routen auf, startet aber keinen Hintergrund-Scheduler. Es erzeugt
`index.html`, `status.json` und monatliche PDFs für alle vorhandenen Archivmonate.
Die statische Seite bietet keinen manuellen API-Abruf; dafür den Workflow starten.

Das SQLite-Archiv wird vor jedem Lauf aus `weather-data` geladen. Der Workflow
ersetzt ausschließlich diesen Datenbranch durch einen vollständigen Snapshot;
es gibt dort keine fortlaufende Git-Historie. Bis zu 14 SQLite-Backups bleiben
im Snapshot. Das ist keine unabhängige externe Sicherung. Den Branch nicht für
Quellcode verwenden oder löschen. Gleichzeitige Workflow-Läufe werden serialisiert.
Bei Abruffehlern werden Teilergebnisse und Fehlerstatus gesichert/veröffentlicht,
aber der Sammeljob wird als fehlgeschlagen markiert. Vorhandene Daten bleiben
verfügbar. Ein fehlender Datenbranch ist nur beim ersten Lauf normal; andere
Git-Zugriffsfehler brechen den Lauf ab.

**Grenzen:** Zwei tägliche Abrufe können zwischenzeitliche DWD-Warnungen verpassen.
PDFs enthalten Modellwerte, keine Messungen. Vorläufige Koordinaten müssen vor
produktiver Nutzung verifiziert werden. Die kostenlose Open-Meteo-API ist für
nichtkommerzielle Nutzung vorgesehen. Für gewerbliche Winterdienst-Dokumentation
müssen die aktuellen Nutzungsbedingungen und gegebenenfalls ein kommerzieller
API-Zugang geklärt werden; kostenloses Hosting löst diese Lizenzfrage nicht.

Lokaler Export (Daten und Ausgabe außerhalb des Checkouts):

```bash
WW_DATA_DIR=/tmp/winterwetter-archive python publish.py --output /tmp/winterwetter-site
# Vorhandene Daten ohne neuen Abruf exportieren:
WW_DATA_DIR=/tmp/winterwetter-archive python publish.py --skip-collect --output /tmp/winterwetter-site
```
