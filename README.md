# 💰 Kassenstürzle

Persönliche Familien Haushalts-App zum Erfassen von Ausgaben und Einnahmen – mit Monatsübersichten,
Diagrammen, Kategorien, Personen, wiederkehrenden Ausgaben und CSV-Import/Export.

---

## 🚀 Schnellstart mit Docker

### Voraussetzungen
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/)

### Starten

```bash
docker compose up -d
```

Die App ist dann unter **http://localhost:5000** erreichbar.

Die Datenbank wird automatisch im Docker-Volume `kassenstuerzle_data` gespeichert und
bleibt auch nach Container-Neustarts erhalten.

### Stoppen

```bash
docker compose down
```

### Logs anzeigen

```bash
docker compose logs -f
```

---

## 🔧 Lokale Entwicklung (ohne Docker)

```bash
pip install -r requirements.txt
python app.py
```

---

## 📂 Datenbank-Pfad anpassen

Standardmäßig wird die Datenbank unter `./data/kassenstuerzle.db` gespeichert.
Über die Umgebungsvariable `DATABASE_PATH` kann ein eigener Pfad gesetzt werden:

```bash
DATABASE_PATH=/mnt/nas/kassenstuerzle.db python app.py
```

oder in der `docker-compose.yml`:

```yaml
environment:
  - DATABASE_PATH=/app/data/meine-datenbank.db
```

---

## 📦 Funktionen

- 💸 Ausgaben & Einnahmen pro Monat erfassen
- 🏷️ Kategorien mit Farben & Split-Option
- 👥 Mehrere Personen / Haushalte
- 🔄 Wiederkehrende Ausgaben
- 📊 Diagramme: Donut, Trend, Stacked Bar
- 📅 Monatsabschluss (Ausgleichsberechnung)
- 📥 CSV-Import & 📤 CSV-Export
- 🌙 Dark Mode
