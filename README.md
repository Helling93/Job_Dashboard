# Job Watcher

Überwacht konfigurierbare Firmen-Karriereseiten auf neue Stellenangebote,
benachrichtigt per Telegram und veröffentlicht ein Dashboard über GitHub
Pages. Läuft komplett in GitHub Actions - kein eigener Server nötig.

## Wie es funktioniert

1. `main.py` liest `config.yaml` und scraped jede Firma (`scraper.py`,
   requests+BeautifulSoup, optional Playwright für JS-lastige Seiten).
2. `storage.py` vergleicht das Ergebnis mit `data/state.json` (letzter
   Stand), aktualisiert es und ermittelt neue/verschwundene Jobs. Der
   manuelle Status pro Job (`status`-Feld) bleibt dabei erhalten.
3. `notifier.py` schickt bei neuen Jobs (und optional bei Scraper-Fehlern)
   eine Telegram-Nachricht.
4. `dashboard.py` rendert `docs/index.html` aus dem aktuellen Stand.
5. Der GitHub-Actions-Workflow (`.github/workflows/scrape.yml`) macht das
   täglich automatisch: scrapen → `data/state.json` committen → Dashboard
   auf GitHub Pages deployen.

## Setup

### 1. Repo auf GitHub anlegen

Dieses Verzeichnis als privates Repo pushen:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin <deine-repo-url>
git push -u origin main
```

### 2. Telegram-Bot erstellen

1. In Telegram mit **@BotFather** chatten, `/newbot` senden, Namen vergeben.
2. Du bekommst einen **Bot-Token** (Format `123456:ABC-...`).
3. Deine **Chat-ID** herausfinden: Nachricht an deinen neuen Bot schicken,
   dann `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser öffnen
   und `"chat":{"id": ...}` im JSON suchen.

### 3. GitHub Secrets einrichten

Im Repo unter **Settings → Secrets and variables → Actions → New repository
secret**:

| Name | Wert |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot-Token von @BotFather |
| `TELEGRAM_CHAT_ID` | Deine Chat-ID |

Diese Werte stehen **nirgendwo im Code** - der Workflow liest sie nur zur
Laufzeit aus den Secrets (siehe `.github/workflows/scrape.yml`).

### 4. Workflow-Permissions setzen

Unter **Settings → Actions → General → Workflow permissions**:

- **"Read and write permissions"** aktivieren (nötig, damit der Workflow
  `data/state.json` committen und pushen kann).

Die Berechtigung für Pages-Deploy ist bereits direkt im Workflow über den
`permissions:`-Block (`pages: write`, `id-token: write`) gesetzt.

### 5. GitHub Pages aktivieren

Unter **Settings → Pages**:

- **Source**: "GitHub Actions" auswählen (nicht "Deploy from a branch").

Damit übernimmt der Workflow (`upload-pages-artifact` + `deploy-pages`) das
Deployment automatisch bei jedem Lauf.

### 6. Firmen konfigurieren

`config.yaml` bearbeiten und pro Firma die CSS-Selektoren eintragen (siehe
Kommentare in der Datei). Am einfachsten testest du Selektoren lokal:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
playwright install chromium   # nur falls method: playwright genutzt wird
python main.py
```

`docs/index.html` und `data/state.json` werden lokal erzeugt - so lassen
sich Selektoren gegen echte Seiten debuggen, bevor der Workflow scharf
geschaltet wird. `tests/test_scraper.py` zeigt anhand einer Fixture
(`tests/fixtures/sample_company.html`), wie die Selektor-Logik geprüft
werden kann, ohne echte Websites anzufragen.

Aktuell sind acht Firmen als Beispiele eingerichtet und gegen die echten
Seiten getestet:

- **BMW Group** (`method: playwright`): Die Karriereseite ist eine SPA, aber
  Standort-/Kategorie-Filter werden über einen serverseitig gerenderten
  HTML-Fragment-Endpoint aufgelöst (URL inkl. `filterSearch=...` in
  `config.yaml`). Plain `requests` wird vom Akamai-Botschutz der Seite
  blockiert (Timeout) - daher Playwright, das über einen echten
  Browser-Kontext zuverlässig durchkommt.
- **Helsing** (`method: playwright`): Seite ist eigentlich serverseitig
  gerendert (kein JS-Rendering nötig), aber der Bot-Schutz antwortet auf
  wiederholte `requests`-Anfragen kurzfristig mit 429 - Playwright ist hier
  robuster für den täglichen automatisierten Lauf.
- **Rheinmetall (Schweiz / München)** (`method: playwright`, `paginate:
  true`): Nuxt-SPA, Filter laufen über einen JSON-Query-Parameter
  (`filter={"countries":[...]}` bzw. `{"cities":[...]}`), Ergebnisse sind
  über mehrere Seiten à 10 Treffer verteilt - `{page}` im `url:`-Platzhalter
  wird automatisch durchgezählt, bis eine Seite leer bleibt.
- **Audi F1 Team (Hinwil)** (`method: playwright`, `load_more_selector`):
  Next.js-Seite, zeigt initial nur 10 von z.B. 17 Treffern - ein
  "Load more"-Button wird automatisch so oft geklickt, bis alle geladen
  sind.
- **Quantum Systems** (`method: playwright`, `load_more_selector`):
  serverseitig gerendert, aber ebenfalls mit "Show more jobs"-Button statt
  echter Pagination.
- **KNDS** (`method: playwright`, `flatten_shadow_dom: true`): SAP
  SuccessFactors Career Site Builder, gebaut mit Stencil.js Web Components -
  die Job-Liste steckt im Shadow DOM, das normales `page.content()` nicht
  sieht. `flatten_shadow_dom` kopiert Shadow-Root-Inhalte vor dem
  Serialisieren ins Light-DOM. `pageSize=100` in der URL holt alles auf
  einmal (keine Pagination nötig).
- **MBDA** (`method: playwright`, `paginate: true`): serverseitig gerendert
  (Playwright wegen JS-Hydration nötig), 10 Treffer pro Seite.

### Filter

Manche Seiten bieten eigene serverseitige Filter (wie BMWs
`filterSearch`-Parameter in der URL - dort direkt in `url:` eingebaut).
Für Seiten ohne solche Filter (wie Helsing) gibt es pro Firma einen
optionalen `filters:`-Block in `config.yaml`, der NACH dem Scrapen greift:

```yaml
filters:
  location_contains: ["Munich"]              # nur Jobs mit diesem Standort
  title_contains: []                          # nur Jobs mit diesen Wörtern im Titel
  exclude_title_contains: ["Closed Opportunity"]  # Jobs mit diesen Wörtern aussortieren
  category_contains: ["Hardware Engineering"] # nur Jobs mit dieser Kategorie
  exclude_category_contains: []               # Kategorien aussortieren
```

Alle Vergleiche sind case-insensitive Teilstring-Treffer. Welche Werte für
`category_contains` sinnvoll sind, findet man am einfachsten heraus, indem
man die Firma zunächst ohne `filters` laufen lässt und im Dashboard schaut,
welche Kategorien (sichtbar in der Job-Unterzeile) tatsächlich vorkommen.

### 7. Workflow aktivieren

Standardmäßig läuft der Scan täglich um 8 Uhr (Cron in `scrape.yml`, siehe
Hinweis zu Zeitzonen unten). Manuell auslösen: **Actions-Tab → Job Scraper
→ Run workflow**.

## Status pro Job ändern

Jeder Job hat vier Status-Buttons direkt im Dashboard: **Neu → Interessant →
Beworben**, sowie **Kein Interesse**. Ein Klick speichert sofort im Browser
(localStorage) - der Job wird dabei live umsortiert; "Kein Interesse"
blendet ihn aus der Liste aus (über "🙈 ... anzeigen" pro Firma jederzeit
wieder einblendbar, z.B. um sich umzuentscheiden). Jede Firmen-Sektion lässt
sich per Klick auf den Titel einklappen.

Verschwundene Stellen (Historie) werden nur noch angezeigt, wenn der Status
`beworben` ist - alles andere, das offline gegangen ist, interessiert nicht
mehr und wird nicht aufgelistet.

### Geräteübergreifende Synchronisation

Ohne weitere Einrichtung bleibt der Status **nur im aktuellen Browser**
gespeichert (localStorage) - praktisch für den Alltag, aber nicht sichtbar,
wenn du das Dashboard auf einem anderen Gerät öffnest.

Für geräteübergreifenden Abgleich: Oben im Dashboard ein GitHub-Token
hinterlegen (Feld "GitHub-Token"). Ab dann schreibt jeder Klick zusätzlich
direkt per GitHub-API einen Commit nach `data/state.json` im Repo - das
Token muss dafür auf jedem Gerät/Browser einmal eingegeben werden, in dem du
den Status siehst/änderst.

**Token erstellen** (GitHub → Settings → Developer settings → Fine-grained
tokens → Generate new token):
- **Repository access**: nur dieses eine Repo auswählen
- **Permissions**: **Contents: Read and write** (sonst nichts)
- Ablaufdatum setzen (z. B. 90 Tage) - danach neu generieren und im
  Dashboard erneuern

Das Token wird ausschließlich im `localStorage` deines Browsers gespeichert
und nur direkt von dort an `api.github.com` gesendet - es läuft über keinen
Zwischenserver. Trotzdem: Bei einem geteilten/öffentlichen Rechner das Token
danach über "Token entfernen" wieder löschen.

Falls kein Token hinterlegt ist oder die Synchronisation fehlschlägt (z. B.
Token abgelaufen), bleibt die Änderung trotzdem lokal im Browser erhalten -
nur eben nicht geräteübergreifend sichtbar.

**Alternativen ohne Dashboard**: `data/state.json` direkt im
GitHub-Web-Editor bearbeiten (Feld `"status"` beim jeweiligen Job), oder
lokal `python scripts/set_status.py <job_id> <status>` gefolgt von
`git add data/state.json && git commit && git push`.

## Sichtbarkeit des Dashboards (privates Repo!)

Wichtig: Bei einem **privaten Repo auf GitHub Free** ist die über GitHub
Pages deployte Seite trotzdem **öffentlich im Internet erreichbar** (jeder
mit der URL kann sie sehen, sie taucht aber nicht in der Repo-Ansicht auf
und wird i. d. R. nicht von Suchmaschinen indexiert - `robots.txt`-Meta-Tag
ist im Dashboard bereits gesetzt).

Optionen, falls das für dich relevant ist (Jobsuche-Status ist sensibel):

1. **GitHub Pro/Team/Enterprise**: Unter Settings → Pages gibt es dort die
   Option "Private Pages" - nur Repo-Collaborator können zugreifen. Einfachste
   Lösung, falls du ohnehin einen bezahlten Plan hast.
2. **Zugriffsschutz selbst bauen** (GitHub Free): z. B. Cloudflare Access /
   Cloudflare Pages mit Passwortschutz vor die GitHub-Pages-URL schalten,
   oder die Pages-URL nicht veröffentlichen (Security-through-obscurity -
   kein echter Schutz, da öffentlich erreichbar).
3. **Ganz vermeiden**: Statt GitHub Pages `docs/index.html` nur lokal öffnen
   (Workflow-Schritt "Dashboard hochladen/deployen" entfernen) und dir nur
   die Telegram-Benachrichtigungen schicken lassen.

Sag Bescheid, falls du Option 2 (Zugriffsschutz) konkret umgesetzt haben
möchtest - das ist bewusst nicht vorimplementiert, da es von deinem Setup
(Cloudflare-Account vorhanden? GitHub-Plan?) abhängt.

## Cron & Zeitzone

GitHub-Actions-Cron läuft in UTC. `0 6 * * *` entspricht 8:00 Uhr in
Zürich/München **während der Sommerzeit (UTC+2)**. Im Winter (UTC+1) läuft
der Scan dann um 7:00 Uhr. Falls exakt 8:00 Uhr ganzjährig wichtig ist,
zwei Cron-Einträge nutzen (`0 6 * 3-10 *` und `0 7 * 11,12,1,2 *`) oder die
kleine Abweichung zweimal im Jahr in Kauf nehmen.

## Fehlerbehandlung

Wenn eine Firmenseite nicht erreichbar ist oder sich die Struktur geändert
hat (Selektor findet nichts), wird das geloggt, die Firma übersprungen und
der Lauf läuft mit den übrigen Firmen weiter. Bei `notify_on_error: true`
in `config.yaml` (Standard) kommt zusätzlich eine Telegram-Warnung mit der
betroffenen Firma und Fehlermeldung.

## Projektstruktur

```
config.yaml           Firmenliste + Einstellungen
scraper.py             HTML laden (requests/playwright) + Jobs extrahieren
storage.py              data/state.json laden/speichern, Diff-Logik
notifier.py             Telegram-Benachrichtigungen
dashboard.py             docs/index.html aus state.json rendern
templates/dashboard.html.j2   Jinja2-Template fürs Dashboard
main.py                 Orchestriert einen kompletten Lauf
scripts/set_status.py   CLI zum Ändern des Job-Status
data/state.json          Persistenter Scan-Stand (wird von Actions committed)
docs/index.html           Generiertes Dashboard (GitHub Pages Root)
.github/workflows/scrape.yml   Täglicher Cron-Workflow
tests/                   Unit-Tests für die Scraper-Selektorlogik
```
