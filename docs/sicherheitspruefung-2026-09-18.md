# Sicherheitsprüfung Kassenstürzle — 18.09.2026

Stand: `main` nach dem Merge von Phase B (Benutzerprofile), 97 Tests grün.

Geprüft in vier unabhängigen Durchgängen: Authentifizierung/Sitzungen,
Injection/XSS, Dateien/Uploads/Pfade, Infrastruktur/Geheimnisse/Abhängigkeiten.
Befunde dedupliziert und nachverifiziert. Nicht bestätigte oder überzogene
Aussagen sind unten unter „Korrekturen" festgehalten.

Bedrohungsmodell: Die App ist über einen Reverse Proxy erreichbar, ggf. aus dem
offenen Internet. Angreifer sind (a) Außenstehende ohne Konto, (b) eingeloggte
`member`, die Adminrechte erlangen wollen. Haushaltsmitglieder dürfen
gegenseitig ihre **Finanzdaten** sehen und ändern (so gewollt), aber **nicht**
gegenseitig ihre Konten übernehmen.

---

## Kritisch

### K1 — Gespeichertes XSS über Personen- und Kategorienamen

**Der schwerwiegendste Befund. Heute scharf, ohne Zutun eines Angreifers von außen.**

Ein Personen- oder Kategoriename wird serverseitig nur mit `.strip()` behandelt
(`app/routes/main.py:1410`, `:1487`) und landet ungefiltert in mehreren Senken:

| Ort | Senke | Auslösung |
|---|---|---|
| `app/templates/month.html:344` | `\|safe` auf `translate()`-Ergebnis | **Seitenaufruf, kein Klick** |
| `app/templates/settings.html:698-699` | JS-Template-Literal | Öffnen von `/settings` |
| `app/templates/month.html:630-645, 1038-1117, 1250` | `innerHTML` | jedes Neurendern nach Speichern |
| `app/templates/overview.html:730-735` | `innerHTML` (Chart-Legende) | Öffnen von `/overview` |

Zwei Umgehungen, die man leicht für unmöglich hält:

- `translate()` (`app/translations.py:801`) macht `text.format(**kwargs)` **ohne
  Escaping**, das Ergebnis geht durch `|safe`. Ein Name `<img src=x onerror=…>`
  führt damit bei jedem Mitglied beim bloßen Öffnen des Monats Code aus.
- Jinja escapt `<`, `>`, `"` — aber **nicht** `$`, `{`, `}` und Backtick. Im
  Template-Literal in `settings.html` ist `${…}` deshalb eine lebende
  Substitution. Ein Payload ganz ohne die escapten Zeichen läuft wörtlich.

**Rechteausweitung:** Ein `member` speichert einen Namen, der beim nächsten
Besuch eines **Admins** auf `/settings` das CSRF-Token aus dem DOM liest und
`POST /api/users/<eigene_id>` mit `role=admin` schickt. Damit ist die
admin/member-Grenze vollständig überschritten — der einzige funktionierende
Eskalationspfad, den die Auth-Prüfung gefunden hat.

**Behebung:** Namen nie in Template-Literale oder `innerHTML` interpolieren.
`base.html:244-249` hat bereits einen korrekten `esc()`-Helfer — als
`window.escHtml` exportieren und überall anwenden. Für die `<option>`-Listen
das Muster aus `month.html:1013-1021` (`new Option(name, id)`) übernehmen.
In `month.html:344` das `|safe` entfernen und das Markup aus dem
Übersetzungsstring herausziehen. Zusätzlich `color` serverseitig auf
`^#?[0-9A-Fa-f]{3,8}$` prüfen (`main.py:1487`).

> Der `<option>`-Kontext ist tatsächlich ausnutzbar — im Browser gegengeprüft,
> nicht angenommen. Auch `innerHTML` direkt auf einem `<select>` fügt ein
> lebendes `<img>` ein und feuert.

### K2 — `/api/backup/restore` akzeptiert jede SQLite-Datei

`app/routes/main.py:2615-2668`. Geprüft wird nur: Endung `.db` und „öffnet als
SQLite". Kein Schema-Check, keine Herkunftsprüfung.

Empirisch gegen eine Wegwerf-Datenbank nachgestellt:

- Eine 3 kB große Datei mit einer selbst gewählten `users`-Zeile
  (`role='admin'`) wird mit `200 {"success": true}` übernommen — danach ist das
  echte Adminkonto weg und der Angreifer eingeloggt. `_ensure_admin()`
  „repariert" eine feindliche Datei sogar noch.
- Eine **0 Byte große** Datei namens `evil.db` besteht die Prüfung und leert die
  Datenbank vollständig.

Angriff ist Social Engineering, keine Rechteausweitung von innen: dem Admin eine
„Sicherung zum Einspielen" unterschieben — oder über K3/K1 direkt auslösen.

**Behebung:** Restore aus dem serverseitigen Backup-Verzeichnis per Dateiname
(dieselbe Allowlist wie `api_backup_download`), statt aus einem Upload. Wenn
Uploads bleiben müssen: Schema vor dem Tausch prüfen (Pflichttabellen,
`PRAGMA integrity_check`, keine unerwarteten Views/Trigger) und eine Datei
ablehnen, in deren `users` der aktuell angemeldete Admin fehlt.

### K3 — XSS über ein präpariertes Kontoauszugs-PDF

`app/templates/import_pdf.html:115-121`. `counterpart` und `description` kommen
roh aus der PDF-Textebene (`main.py:2382`), werden nur auf 80/150 Zeichen
gekürzt und dann sowohl als Attributwert als auch als Elementinhalt in
`row.innerHTML` interpoliert.

**Der Angreifer braucht kein Konto.** Es genügt, einem Mitglied eine
„Kontoauszug.pdf" zu mailen. Beim Import feuert der Payload im Origin des
Opfers und kann als dieser Nutzer handeln — CSRF-Token holen, bei einem Admin
ein Konto anlegen oder ein Backup einspielen (K2).

**Behebung:** Zeile mit `document.createElement`/`textContent` bauen, oder alle
drei Interpolationen escapen.

### K4 — Kein `.dockerignore`: `.env` und Echtdaten landen im Image

`Dockerfile:22` macht `COPY . .`, ein `.dockerignore` existiert nicht
(nachgeprüft). Ein `docker build .` aus diesem Verzeichnis backt `.env`
(mit `DB_ENCRYPTION_KEY`), `data/` und `data_/` (echte Datenbank, `.secret_key`,
die Klartext-Kopie aus I1) dauerhaft in eine Image-Schicht — auch wenn eine
spätere Schicht sie „löscht", bleiben sie über `docker history`/`docker save`
lesbar.

**Behebung:** `.dockerignore` mit `.env`, `.env.*`, `data/`, `data_/`, `.git/`,
`*.db*`, `tests/`, `docs/`.

---

## Wichtig

### I1 — Klartext-Vollkopie der Datenbank liegt real auf der Platte

`app/routes/main.py:88-91`: `_migrate_to_encrypted` legt
`<pfad>.unencrypted.bak` an und löscht sie nie. **Nachgeprüft — sie existiert:**
`data_/kassenstuerzle.db.unencrypted.bak`, 160 KB, Modus 644. Damit ist die
Verschlüsselung der Finanzhistorie durch eine Datei im selben Verzeichnis
aufgehoben, die über den Bind-Mount `./data:/app/data` auch im Container liegt.

**Behebung:** Nach erfolgreicher Migration löschen. Die bestehende Datei solltest
du von Hand entfernen, sobald du sie nicht mehr als Rückfallebene brauchst.

### I2 — Geheimnisse mit Modus 644, Container läuft als root

Nachgeprüft: `.env`, `data/.secret_key`, `data_/.secret_key` sind alle `644`.
Dazu `data/.vapid_private_key.pem`, die Datenbank, alle Backups und alle
hochgeladenen Belege (`app/__init__.py:16-17`, `main.py:2530`, `:2864`, `:3019`).
Das `Dockerfile` hat keine `USER`-Anweisung.

Wer den Datenträger lesen kann, bekommt den Flask-Signierschlüssel — und kann
damit eine Admin-Sitzung **direkt fälschen**, ohne Passwort.

**Behebung:** `os.umask(0o077)` beim Start bzw. `os.chmod(..., 0o600)` an den
vier Erzeugungsstellen, `chmod 0700` aufs Datenverzeichnis, `USER` im Dockerfile.

### I3 — Timing-Orakel am Login verrät gültige Benutzernamen

`app/routes/main.py:880`: `if user and check_password_hash(…) and user['is_active']`.
Bei unbekanntem Benutzer schließt `and` kurz und überspringt scrypt.

Gemessen: existierender Benutzer **36,6 ms**, nicht existierender **0,7 ms** —
Faktor 50, aus der Ferne trivial messbar. Danach gezielt per Lockout (M1)
aussperren oder brute-forcen.

> Das ist nicht der Fehler, den ich in Phase B behoben habe. Dort ging es um die
> Reihenfolge von `is_active`; der `user and`-Kurzschluss blieb bestehen.

**Behebung:** Bei `user is None` einen Dummy-scrypt-Vergleich gegen einen festen
Hash rechnen, damit jeder Login denselben teuren Pfad nimmt.

### I4 — Eine Klartext-Sicherung einspielen legt eine verschlüsselte Instanz dauerhaft lahm

`main.py:2637-2649`: Die Validierung probiert erst `_open_db` (mit `PRAGMA key`),
fällt bei Fehler auf `sqlite3.connect` **ohne** Schlüssel zurück und akzeptiert
die Datei so oder so. Danach passt kein `PRAGMA key` mehr → `file is not a
database` auf **jedem** Request, auch `/login` und `/api/backup/restore` selbst.
Nur noch per Shell-Zugriff auf das Volume zu retten.

Wahrscheinlichster Auslöser ist das eigene Artefakt aus I1.

**Behebung:** Bei gesetztem `_DB_KEY` den schlüssellosen Fallback streichen und
mit 400 ablehnen.

### I5 — Unbegrenzter Beleg-Upload: jedes Mitglied kann die Platte füllen

`main.py:2986-3021`. `api_receipt_upload` ist der einzige Upload-Endpunkt
**ohne** Rate-Limit (`app/__init__.py:47-56`). Jeder Request legt bis zu 20 MB
ab, zufälliger Inhalt ergibt jedes Mal eine neue Datei, und Belege werden nie
aufgeräumt — auch nicht, wenn die zugehörige Ausgabe gelöscht wird.

**Behebung:** Endpunkt ins Rate-Limit aufnehmen, Gesamtgröße deckeln,
verwaiste Belege löschen.

### I6 — CSP mit `'unsafe-inline'` entwertet den wichtigsten XSS-Schutz

`app/__init__.py:131`: `script-src 'self' 'unsafe-inline'`. Damit blockiert die
CSP genau die Payload-Form aus K1/K3 **nicht**. Sie beschränkt nur externe
Skript-Quellen.

**Behebung:** Inline-Skripte auslagern oder auf Nonces umstellen. Das ist die
Absicherung *hinter* K1, nicht ihr Ersatz.

### I7 — `SESSION_COOKIE_SECURE` standardmäßig aus, Port auf allen Interfaces

`app/__init__.py:24` leitet `Secure` aus `HTTPS` ab, `Dockerfile:29` setzt
`HTTPS=0`, `docker-compose.yml` überschreibt es nicht. Gleichzeitig bindet
`docker-compose.yml:5-6` `5000:5000` auf **alle** Host-Interfaces statt auf
`127.0.0.1`. Wer den Port direkt erreicht, bekommt Sitzungscookies ohne
`Secure`-Flag über Klartext-HTTP. `HttpOnly` und `SameSite=Strict` sind korrekt.

**Behebung:** Portmapping auf `127.0.0.1:5000:5000`, `HTTPS=1` setzen.

### I8 — Bootstrap-Passwort wird im Klartext geloggt und läuft nie ab

`main.py:382-392`: Ohne `ADMIN_PASSWORD` wird ein Zufallspasswort erzeugt und
auf WARNING-Level **vollständig geloggt** — landet also im Docker-Log. Der
zugehörige `INSERT` setzt `must_change_password` nicht, die Spalte
defaultet auf `0` (`:485`). Das geloggte Passwort bleibt damit unbegrenzt gültig.

**Behebung:** `must_change_password=1` beim Bootstrap-Insert setzen.

### I9 — Stille Klartext-Rückfallebene: die Warnung ist toter Code

`app/__init__.py:96` ruft `_migrate_to_encrypted` nur auf, wenn
`_SQLCIPHER_AVAILABLE` bereits wahr ist. Die einzige Warnung für den
Gegenfall (`main.py:71`, „DB bleibt unverschlüsselt!") kann von dieser
Aufrufstelle aus **nie** feuern. `_open_db` überspringt `PRAGMA key` ebenfalls
kommentarlos.

**Nicht akut** (siehe Korrekturen), aber: scheitert bei einem Rebuild die
Installation von `sqlcipher3`, läuft die App still im Klartext weiter, während
`DB_ENCRYPTION_KEY` gesetzt ist.

**Behebung:** Beim Start hart prüfen — Schlüssel gesetzt, Treiber fehlt →
ERROR loggen oder Start verweigern.

### I10 — Transitive Abhängigkeiten ungepinnt, Pillow veraltet

`requirements.txt` pinnt nur die obersten Pakete. Werkzeug, Jinja2, cryptography
& Co. werden bei jedem Build neu aufgelöst (aktuell installiert: `Jinja2 3.1.6`,
`Werkzeug 3.1.8` — beide stehen nirgends in den Requirements). `Pillow==10.0.1`
(Sept. 2023) verarbeitet direkt Angreifer-kontrollierte Bytes
(`main.py:2776-2783`) und hat seither Speicherfehler-Korrekturen erhalten.

**Behebung:** Lock-Datei (`pip-compile`/`uv pip compile`), Pillow anheben.

---

## Gering

| # | Befund | Ort |
|---|---|---|
| M1 | Lockout als gezielter DoS; IP-Bremse hinter Proxy wirkungslos (kein `ProxyFix`, alle Clients dieselbe IP) | `main.py:855, 902-909` |
| M2 | Label `pre-restore` selbst vergebbar → verdrängt die echten Schutzkopien (**meine Regression von heute**) | `main.py:2578` |
| M3 | iCal-Injection: Personenname mit Zeilenumbruch schleust `VEVENT`-Blöcke ein | `main.py:3165` |
| M4 | Offener Redirect über `Referer` | `main.py:2508` |
| M5 | Ungeschützte `int()`/`float()` → 500 (alle hinter Login, kein Pre-Auth-DoS) | `main.py:1306, 1727, 1024, 1212, 2449` |
| M6 | CSV-Import gibt rohen SQLite-Fehlertext zurück (Tabellen-/Spaltennamen) | `main.py:1590` |
| M7 | Jedes Mitglied kann jeden Beleg löschen (nicht auf Eigentümer eingeschränkt) | `main.py:3044` |
| M8 | Benutzernamen case-sensitiv → `Admin` neben `admin` möglich | `main.py:366, 872` |
| M9 | `/logout` ist GET und CSRF-ausgenommen → Fremdseite kann ausloggen | `main.py:156, 920` |
| M10 | `showToast` nutzt `innerHTML` (heute nur Konstanten, latent) | `month.html:1152` |
| M11 | Restore serialisiert nicht gegen die anderen 3 Threads | `main.py:2651` |
| M12 | Kein Break-Glass: einziger Admin vergisst Passwort → dauerhaft ausgesperrt | `main.py:1113` |
| M13 | Totes Template `scan_receipt.html` zeigt auf nicht existierende Endpunkte | `scan_receipt.html:236, 300` |
| M14 | Kein HSTS, Basis-Image nicht per Digest gepinnt | `app/__init__.py:118`, `Dockerfile:1` |

---

## Korrekturen an den Einzelberichten

- **Die Produktivdaten sind verschlüsselt.** Ein Bericht schloss aus „`sqlcipher3`
  fehlt im Dev-venv" auf ein akutes Klartextproblem. Das ist falsch:
  `requirements.txt:9` pinnt `sqlcipher3==0.5.4`, das `Dockerfile` installiert
  `libsqlcipher0`. Im ausgelieferten Image ist die Verschlüsselung aktiv. Real
  ist nur der Mechanismus (I9) — und die Klartext-Kopie aus I1.
- Der PDF-Import-XSS wurde von zwei Prüfungen unabhängig gefunden (hier K3).
- Die 644-Rechte und die Klartext-Kopie ebenso (I1/I2).

## Geprüft und für solide befunden

- **Autorisierung:** Alle 49 Routen einzeln durchgegangen. Jede Admin-Fähigkeit
  ist `@admin_required`, keine nur per UI versteckt; Decorator-Reihenfolge
  überall korrekt. Ein `member` erreicht über die API keinen Admin-Endpunkt —
  der einzige funktionierende Eskalationspfad ist K1.
- **CSRF:** Sitzungsgebundenes Token, `compare_digest`, global in
  `before_request` für **alle** POST/PUT/PATCH/DELETE erzwungen, unabhängig vom
  Auth-Decorator. Ausnahmen nur Login/Logout/Service-Worker.
- **SQL-Injection:** Nirgends. Jede Query bindet Parameter; das einzige
  f-String-SQL (`main.py:1093`) setzt sich ausschließlich aus Literalen zusammen.
- **`SECRET_KEY`:** Pro Installation zufällig, persistent, gitignored — nicht
  hartkodiert.
- **`session_epoch`:** Bei jedem Request frisch aus der DB gelesen; jede
  Rechteminderung wirft laufende Sitzungen raus.
- **Pfad-Traversal:** Backup-Download/-Delete und Beleg-Zugriff sind dicht —
  mehrere Kodierungen durchprobiert, alle 404.
- **Git-Historie:** Über alle Commits hinweg wurde nie ein `.env`, eine
  Datenbank, ein `.secret_key` oder ein Backup committet.
- **Belegupload:** Endungs-Allowlist **und** Magic-Byte-Prüfung, Speicherung
  unter MD5-Name, `nosniff` — ein HTML/JS-Polyglot wird nicht als `text/html`
  ausgeliefert.
- **Suche:** `_highlight` escapt vor dem Regex und fügt nur bereits escapten
  Text wieder ein — das `Markup()` dort ist gerechtfertigt.
- **Aktivitäts-Banner** (`base.html:239-266`): die eine Stelle, die es richtig
  macht — mit Kommentar, warum.

---

## Empfohlene Reihenfolge

1. **K1** — einziger funktionierender Weg von `member` zu `admin`, und er feuert
   heute bei jedem Seitenaufruf. Zuerst.
2. **K4** und **I1/I2** — Dateirechte, `.dockerignore`, Klartextkopie. Klein,
   und sie schützen den Schlüssel, mit dem alles andere fälschbar wäre.
3. **K2** und **I4** — Restore absichern. Beide unumkehrbar.
4. **K3** — PDF-Import escapen.
5. **I3, I5, I7, I8** — Timing, Rate-Limit, Cookie/Port, Bootstrap-Passwort.
6. **I6** — CSP-Nonces als Absicherung hinter K1.
7. Rest nach Aufwand.
