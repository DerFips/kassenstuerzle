# 💰 Kassenstürzle

Eine einfache, selbst gehostete Haushalts-App für Paare und WGs – gebaut mit Flask & Bootstrap 5.

---

## 🚀 Features

### 📅 Monatsübersicht
- Ausgaben und Einnahmen pro Monat erfassen
- Pro Person und Kategorie kategorisieren
- Farbcodierte Kategorien mit Farb-Badges
- **Barzahlungs-Markierung** pro Ausgabe (💵-Icon)
- **„Bezahlt für"-Funktion**: Eine Person zahlt vollständig für eine andere (z. B. Einkauf)
- Monats-Navigation (zurück/vor) und Jahresübersicht

### 📊 Kategorie-Übersicht (rechte Seite)
- Zeigt Ausgaben und Einnahmen je Kategorie und Person
- **Geteilte Kategorien (⚖️)**: Kategorien die geteilt werden, werden mit Waage-Icon markiert
- Einnahmen in geteilten Kategorien werden grün mit ↓-Pfeil angezeigt
- Einnahme-only Split-Kategorien erscheinen ebenfalls in der Übersicht

### ⚖️ Abrechnung – Wer schuldet wem?
- Automatische Berechnung des Ausgleichs für geteilte Kategorien
- Berücksichtigt **sowohl Ausgaben als auch Einnahmen** in Split-Kategorien
- Zeigt:
  - **Geteilte Kategorien**: Ausgaben & Einnahmen je Person, fairer Anteil
  - **Bezahlt für**: Vorgestreckte Beträge
  - **Personen-Übersicht**: Netto gezahlt vs. fairer Anteil mit farbigem Badge
  - **Ausgleichsüberweisungen**: Wer muss wem wie viel überweisen
- Abrechnung als „Beglichen" markieren (Checkbox)

### 💼 Geldbeutel / Wallet
- Optionaler persönlicher Geldbeutel pro Person
- Startwert und Startdatum konfigurierbar
- Zeigt aktuelles Guthaben basierend auf Ein- und Ausgaben seit Startdatum

### 📥 Import
#### CSV-Import
- Importiere Daten aus einer CSV-Datei (gleiches Format wie Export)
- Format: `Typ;Jahr;Monat;Tag;Person;Kategorie;Beschreibung;Betrag;Barzahlung`
- Option: Vorhandene Monate überschreiben oder ergänzen

#### PDF-Import (Kontoauszug)
- Importiere Buchungen direkt aus Bank-PDFs
- Unterstützte Banken: **DKB**, ING, Sparkasse, Volksbank, Commerzbank, Postbank u.v.m.
- Mehrstufiger Workflow: Hochladen → Prüfen → Importieren
- Automatische Kategorie-Erkennung anhand von Empfängernamen
- Zugänglich über: **Einstellungen → Import → PDF Import**

### 📤 Export
- Export aller Ausgaben und Einnahmen als CSV
- Filterbar nach Zeitraum

### ⚙️ Einstellungen
- **Personen** verwalten (Name, Geldbeutel-Startdatum, -Startwert)
- **Kategorien** verwalten (Name, Farbe, Split-Funktion ⚖️)
- **Wiederkehrende Ausgaben**: Monatlich, quartalsweise oder jährlich
- **Import & Export** in einer Kachel (CSV & PDF)

### 🌙 Dark Mode
- Vollständiger Dark-Mode-Support (Bootstrap `data-bs-theme`)
- Alle Karten, Tabellen und Karten-Header passen sich automatisch an
- Einschließlich der Abrechnungs-Kachel (zuvor hardcodierte Farben behoben)

---

## 🛠️ Installation

### Voraussetzungen
- Docker & Docker Compose **oder** Python 3.11+

### Mit Docker (empfohlen)

```bash
git clone https://github.com/derfips/kassenstuerzle.git
cd kassenstuerzle
docker compose up -d
```

App läuft dann unter: `http://localhost:5000`

### Ohne Docker

```bash
pip install flask pdfplumber
python app.py
```

---

## 📁 Dateistruktur

```
kassenstuerzle/
├── app.py                  # Flask-Backend, alle Routen & Logik
├── templates/
│   ├── base.html           # Basis-Layout mit Navbar & Dark Mode
│   ├── month.html          # Monatsübersicht (Hauptseite)
│   ├── year.html           # Jahresübersicht
│   ├── settings.html       # Einstellungen (Personen, Kategorien, Import/Export)
│   └── import_pdf.html     # PDF-Import Workflow
├── static/
│   └── favicon.svg
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## 🗄️ Datenbank

SQLite-Datenbank (`kassenstuerzle.db`) mit folgenden Tabellen:

| Tabelle | Inhalt |
|---|---|
| `persons` | Personen mit Geldbeutel-Konfiguration |
| `categories` | Kategorien mit Farbe und Split-Flag |
| `expenses` | Ausgaben (mit optionalem `paid_for_person_id`) |
| `income` | Einnahmen (mit optionalem `category_id` für Split-Berechnung) |
| `month_status` | Abrechnungsstatus je Monat |
| `recurring_expenses` | Wiederkehrende Ausgaben |
| `pdf_category_rules` | Zuordnungsregeln für PDF-Import |

---

## 🔒 Hinweise

- Keine Benutzeranmeldung – für den **Heimnetz-Einsatz** konzipiert
- Datenbank liegt unter `/data/kassenstuerzle.db` im Container (Docker Volume)
- Alle Berechnungen finden serverseitig statt

---

## 📝 Lizenz

MIT License – frei verwendbar und anpassbar.
