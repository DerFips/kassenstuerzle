# Changelog

Alle nennenswerten Änderungen an Kassenstürzle. Die Versionsnummern entsprechen den Git-Tags.

## [2.1] – 2026-10-23

Nettovermögen im Profil, Umbuchen zwischen Ausgaben und Einnahmen und ein deutlich transparenterer PDF-Import.

### Hinzugefügt

- **Nettovermögen im Profil.** Jede Person pflegt private Konten für Vermögen und Schulden:
  - 15 Kontotypen. Vermögen: Girokonto, Tagesgeld, Festgeld, Aktiendepot / ETF, Lebensversicherung (Rückkaufswert), Altersvorsorge, Immobilie, Fahrzeug, Bargeld, Sonstiges Vermögen. Schulden: Kredit, Baufinanzierung / Hypothek, Kreditkarte, Dispo, Sonstige Schuld.
  - Salden werden zu frei wählbaren Stichtagen eingetragen. Ein Eintrag pro Konto und Tag, ein erneutes Eintragen am selben Tag überschreibt.
  - Vierte Kennzahl **Nettovermögen** im Profil (Vermögen minus Schulden), dazu Verlaufsliste und Liniendiagramm mit Nettovermögen, Vermögen und Schulden.
  - Beim Rückdatieren zeigen die Saldofelder den Stand zum gewählten Datum, damit unveränderte Konten (z. B. eine Immobilie) im Verlauf nicht auf 0 fallen.
  - Streng persönlich: Jede Person sieht und ändert nur ihre eigenen Konten, auch Admins in der Anwendung nicht. Wird eine Person gelöscht, verschwinden ihre Konten und Salden mit.
- **Umbuchen zwischen Ausgaben und Einnahmen** in der Monatsansicht. Bei eigenen Zeilen verschiebt ein Button die Buchung in die andere Tabelle. Der Betrag wird dabei ohne Vorzeichen übernommen (aus -50 € wird +50 €). Erst wird die neue Zeile gespeichert, nur bei Erfolg das Original gelöscht.
- **PDF-Import: nicht erkannte Zeilen.** Zeilen, die der Import bisher still verworfen hat (unbekannter Buchungstyp, kein Betrag, ungültiges Datum), erscheinen jetzt in einer eigenen Karte:
  - mit Warnung über der Liste und Hinweis auf den Grund,
  - mit Formular zum direkten Übernehmen als normale Buchung (Datum, Beschreibung, Betrag und Art sind soweit möglich vorbelegt),
  - mit **TXT-Export** der Rohzeilen, damit nichts verloren geht.
- `LICENSE` (GNU AGPL v3) und `.env.example` im Repository.

### Behoben

- **Konten speichern verlor eingetippte Salden.** Ein neues Konto mit Betrag kam nach dem Speichern ohne Betrag zurück. "Konten speichern" sichert die eingetippten Salden jetzt mit. Schlägt nur das Speichern der Salden fehl, bleibt die Seite stehen und "Salden speichern" wiederholt den Versuch, ohne Konten doppelt anzulegen.

### Sicherheit und Robustheit

- Neue Endpunkte prüfen die Eigentümerschaft serverseitig. Eingaben werden vollständig vor dem Schreiben validiert: ungültige Konto-IDs, `NaN`, `Infinity`, zu große Beträge und Zukunftsdaten werden abgewiesen statt einen Serverfehler auszulösen. Gespeicherte Salden werden auf zwei Nachkommastellen gerundet.
- Eingetragene Kontonamen und Rohtext aus dem PDF-Import werden nur als Text ausgegeben, nie als HTML.
- Doppelklicks auf die Speichern-Knöpfe der Vermögenskarte werden bis zum Neuladen gesperrt.
- Die neuen Tabellen stehen in der Positivliste der Backup-Wiederherstellung.

### Hinweise zum Update

- `git pull && docker compose up -d --build`. Die neuen Tabellen legt die Anwendung beim Start selbst an, es ist keine Migration von Hand nötig und es gibt keine neuen Umgebungsvariablen.
- Ältere Sicherungen bleiben wiederherstellbar. Sicherungen, die nach dem Update entstehen, enthalten die Vermögensdaten und lassen sich nicht in eine ältere Version zurückspielen.
- Ein Backup und der Server selbst enthalten die Vermögensdaten weiterhin. Der Schutz "nur die eigene Person" gilt innerhalb der Anwendung.

### Bekannte Einschränkungen

- Die **Erkennung** des PDF-Imports ist unverändert. Unbekannte Buchungstypen, Zeilen mit zwei Datumsangaben, Beträge ohne Tausenderpunkt oder mit nachgestelltem Minus werden weiterhin nicht richtig erkannt, nur jetzt sichtbar gemacht. Eine Verbesserung der Erkennung folgt separat.
- In der Vermögenskarte ersetzt ein Datumswechsel bereits getippte Salden, und das Neuladen nach dem Speichern verwirft Eingaben im jeweils anderen Abschnitt.
- Bei quartalsweisen und jährlichen Vorlagen setzt die Oberfläche den Startmonat weiterhin immer auf Januar.

### Intern

- Testsuite von 346 auf 449 Tests.

## [2.0] – 2026-10-11

Benutzerkonten für alle Haushaltsmitglieder, eigene Profilseite und Schutz vor Datenverlust bei gleichzeitiger Nutzung, dazu viele Sicherheitskorrekturen aus einer vollständigen Prüfung.

### Hinzugefügt

- **Benutzerkonten:** Jede Person hat genau ein Konto und umgekehrt, mit den Rollen Admin und Mitglied. Nur Admins legen Personen und Konten an, löschen sie und setzen Passwörter zurück.
- **Profilseite** mit eigenen Summen, Kategorien-Diagramm, Geldbörse, Status der Monatsabrechnungen nach Jahr und der detaillierten Kategorie-Monats-Matrix. Erreichbar über den Benutzernamen, mobil als fünftes Symbol der unteren Leiste.
- **Wiederkehrende Ausgaben sind persönlich:** Jede Person verwaltet ihre eigenen Vorlagen im Profil, auch Admins. Der Button "Wiederkehrende" im Monat fügt nur die eigenen ein.
- **"Wer"-Feld gesperrt:** Bei Ausgaben und Einnahmen ist es immer die angemeldete Person. Für andere zu bezahlen läuft über "Für wen". Der PDF-Import folgt derselben Regel, der CSV-Import bleibt unverändert.
- **Einrichtungsassistent** in den Einstellungen und ein Live-Hinweis, wenn jemand anderes dieselben Daten bearbeitet.
- `README.md` mit Funktionen, Docker-Betrieb, allen Umgebungsvariablen und Entwicklungshinweisen.

### Geändert

- Ausgaben und Einnahmen werden zeilenweise gespeichert, Personen, Kategorien und Vorlagen nur auf ausdrücklichen Wunsch gelöscht. Veraltete Browser-Tabs bekommen eine Meldung.
- SQLite im WAL-Modus mit Wartezeit bei gleichzeitigem Zugriff.
- Login-Sperre pro Benutzername statt pro IP, Rate-Limits greifen jetzt tatsächlich.
- Sicherungen: eigene Rotation für Schutzkopien, Sicherungen derselben Minute überschreiben sich nicht mehr, automatische Kopie vor jeder Schema-Migration.
- Das Personen-Widget in den Einstellungen sieht nur noch der Admin.

### Behoben

- **PDF-Import: Einnahmen wurden als Ausgaben eingestuft.** Ob eine Buchung eine Einnahme oder eine Ausgabe ist, entscheidet jetzt das Vorzeichen des gedruckten Betrags und nicht mehr der Buchungstyp. Banken wie die DKB verwenden Typen wie "Überweisung" und "Dauerauftrag" für ein- und ausgehende Buchungen, eingehende Beträge (z. B. Rückerstattungen) landen deshalb als **Einnahme**, nicht mehr als Ausgabe. Der Import schreibt Einnahmen in die Einnahmen-Tabelle.

### Sicherheit

- Gespeichertes XSS über Personen- und Kategorienamen und im PDF-Import behoben.
- Der Restore-Endpunkt prüft Schema und Herkunft und ist nur für Admins. Klartext-Backups und zu offene Dateirechte sind behoben.
- Content-Security-Policy mit Nonce statt `unsafe-inline`.
- Login: Zeitkanal geschlossen, Startpasswort ist ein Einmalpasswort, Passwort-Hashes mit absurdem Rechenaufwand lassen die App nicht mehr hängen.
- Sitzungscookie standardmäßig Secure, Port nur an `127.0.0.1`, Start ohne Datenbankschlüssel wird verweigert, Container läuft mit fester uid/gid 1000, Beleg-Upload begrenzt, Abhängigkeiten angehoben und festgeschrieben.
- Details: `docs/sicherheitspruefung-2026-09-18.md`.
