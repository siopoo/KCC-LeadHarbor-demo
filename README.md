# KCC LeadHarbor

KCC LeadHarbor is a small Python MVP for discovering public business listings,
enriching them from company websites, scoring them against a keyword, and
exporting the result to CSV.

The local Web dashboard keeps keyword/map discovery and association-directory
imports as two separate workflows. Discovery adapters support OpenStreetMap,
Brave Web Search, the official RCA PDF member directory, public association pages,
and association CSV files. Website crawling stays on the company's domain,
checks `robots.txt`, uses a configurable delay, and only reads public pages.

Completed association sources move into the dashboard's **Imported
associations** module, which shows completed/known source progress and imported
member counts. Completed presets no longer remain in the new-import selector;
they can be refreshed from their imported-association row.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py crawl --keyword "plastic packaging" --location "Germany" --limit 20 --output leads.csv
```

### Retail contractors and builders

Import the official Retail Contractors Association member directory, including
published contact name, phone, email, market, and website fields:

```powershell
python main.py crawl --source association --keyword "retail construction" --location "United States" --association rca --limit 100 --output retail-contractors.csv
```

Enable keyword web search with a Brave Search API key:

```powershell
$env:BRAVE_SEARCH_API_KEY="your-key"
python main.py crawl --source search --keyword "retail contractor" --location "Texas" --limit 50 --output texas-contractors.csv
```

Search with custom queries:

```powershell
python main.py crawl --source search --keyword "retail construction" --location "Florida" --search-query 'retail general contractor Florida' --search-query 'commercial retail builder Florida' --output florida-contractors.csv
```

Import another association's public member page or a prepared CSV:

```powershell
python main.py crawl --source association --keyword "builder" --location "United States" --association-url "https://association.example/members" --association-csv members.csv --output association-leads.csv
```

Accepted CSV headings include `company`, `company_name`, or `name`, plus optional
`website`, `email`, `phone`, and `address`.

Useful options:

```text
--pages-per-site 4    Maximum pages read from each company website
--delay 1.0           Delay between website requests in seconds
--no-website-crawl    Only collect public listing data
```

Run offline tests:

```powershell
python -m unittest discover -s tests -v
```

## Local Web app

Start the browser-based local dashboard:

```powershell
python web_app.py
```

Then visit `http://127.0.0.1:8765`. Companies and crawl tasks are stored in
`data/leadharbor.db` during development.

On the company database page, choose **Import existing database** to upload a
CSV, XLSX, or SQLite (`.db`) file (up to 10 MB). LeadHarbor previews every
record before import:

- duplicates are identified by website, company name, email, or phone;
- incomplete records show the exact missing fields;
- new companies are created, while duplicates only fill blank fields and never
  overwrite existing non-empty values.

Both English and Chinese column headings are accepted, including the headings
used by the built-in CSV export.

Each company row can expand to show its full scoring breakdown, the evidence
that triggered every rule, discovery sources, source URLs, matched keywords,
and the latest collection time. Repeated discoveries merge their provenance
instead of overwriting earlier sources. CSV exports include the same score
breakdown and provenance fields for offline review.

Data reliability tools include complete SQLite backup/restore from Settings,
live task progress with cooperative cancellation and one-click retry, duplicate
company review with lossless record merging, and separate validity markers for
email and phone contacts. A safety backup is created automatically before every
database restore.

Build the Windows desktop executable:

```powershell
.\build.ps1
```

The output is `dist/KCC-LeadHarbor.exe`. When packaged, its database and task
exports are stored under `%LOCALAPPDATA%\KCC LeadHarbor`.

### Linux package

Run the native build on a Linux computer:

```bash
chmod +x build-linux.sh
./build-linux.sh
```

The distributable archive is `dist-linux/KCC-LeadHarbor-Linux.tar.gz`. Extract
it and run `KCC-LeadHarbor-Linux/KCC-LeadHarbor`. Application data is stored in
`$XDG_DATA_HOME/KCC LeadHarbor`, or `~/.local/share/KCC LeadHarbor` by default.

### macOS application

Run the native build on a Mac:

```bash
chmod +x build-macos.sh
./build-macos.sh
```

The distributable archive is named for the current CPU architecture, for
example `dist-macos/KCC-LeadHarbor-macOS-arm64.zip`, and contains
`KCC-LeadHarbor.app`. Application data is stored in
`~/Library/Application Support/KCC LeadHarbor`.

PyInstaller applications must be built on their target operating system. The
included GitHub Actions workflows run the test suite and build Linux plus two
native macOS packages. `Build macOS Demo` produces separate Apple Silicon and
Intel artifacts on `macos-15` and `macos-15-intel`. Run it manually from the
Actions page or push a tag beginning with `v`, such as `v0.1.0`.

Each package is built natively on its matching runner. PyInstaller architecture
flags are intentionally not passed alongside the `.spec` file because those
make-spec options are not accepted when building from a spec configuration.

The macOS packages use an ad-hoc signature for internal testing. They are not
Apple-notarized, so the first launch may require Control-clicking the app and
choosing **Open**. External distribution requires a Developer ID Application
certificate and Apple notarization.

## HubSpot Integration

LeadHarbor can check selected local companies against KCC's HubSpot portal,
preview every proposed change, and create or enrich approved Companies and
Contacts. Checking is read-only. Synchronization fills missing HubSpot values
by default and never replaces a non-empty HubSpot value unless the user
explicitly selects **Use LeadHarbor (overwrite)** for that field.

### Employee workflow

1. Open **Settings → HubSpot Integration**.
2. Enter the private-app access token and click **Save**.
3. Click **Test Connection** and confirm that the status is **Connected**.
4. Open **Companies**, select the desired rows, and click **Check HubSpot**.
5. Review the **New**, **Already in HubSpot**, **Can Enrich**, and **Needs
   Review** results.
6. Keep HubSpot values for conflicts unless an overwrite is intentional and
   approved.
7. Click **Sync approved items**. A failure on one record does not stop the
   remaining records.

The token can instead be supplied before startup through the environment. It
takes priority over the value saved in the local SQLite settings database:

```powershell
$env:HUBSPOT_ACCESS_TOKEN="pat-your-private-app-token"
python web_app.py
```

The Settings page displays only a masked value such as
`••••••••••••abcd`. Never put the real token in source control, screenshots,
support messages, or log files.

### Required HubSpot private-app scopes

The implemented Company, Contact, Search, batch, and association operations
require these object scopes:

- `crm.objects.companies.read`
- `crm.objects.companies.write`
- `crm.objects.contacts.read`
- `crm.objects.contacts.write`

These optional scopes allow LeadHarbor to inspect portal property metadata and
automatically use the supported `leadharbor_*` custom properties when they
already exist:

- `crm.schemas.companies.read`
- `crm.schemas.contacts.read`

Without the optional schema scopes, standard Company and Contact syncing still
works. LeadHarbor never silently creates custom HubSpot properties. If present,
it recognizes `leadharbor_company_id`, `leadharbor_contact_id`,
`leadharbor_source`, `leadharbor_score`, and
`leadharbor_last_enriched_at`.

HubSpot's official references for this configuration are the
[private-app scope guide](https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/scopes),
[Contacts API guide](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/contacts/guide),
and [Companies API guide](https://developers.hubspot.com/docs/api-reference/latest/crm/objects/companies/guide).

### Developer architecture and safety policy

The integration lives under `leadharbor/hubspot/`:

- `client.py` centralizes Bearer authentication, timeouts, safe errors,
  `Retry-After`, bounded 429/5xx retry, and a conservative four-searches-per-
  second limiter.
- `companies.py` and `contacts.py` own object search/read/create/update calls.
- `normalization.py`, `dedup.py`, `mapper.py`, and `diff.py` are deterministic,
  independently tested policy layers.
- `batch.py` splits CRM reads, creates, and updates into groups of at most 100.
- `sync.py` separates the read-only check from explicit writes and reports
  partial results.
- `associations.py` uses HubSpot's default Contact-to-Company association API.

HubSpot endpoints are centralized and currently use the supported `2026-03`
CRM object/search/property endpoints, plus the v4 default association endpoint:

- `GET/POST/PATCH /crm/objects/2026-03/{companies|contacts}...`
- `POST /crm/objects/2026-03/{companies|contacts}/search`
- `POST /crm/objects/2026-03/{companies|contacts}/batch/{read|create|update}`
- `GET /crm/properties/2026-03/{companies|contacts}` (optional metadata)
- `POST /crm/v4/associations/contact/company/batch/associate/default`

See HubSpot's official [CRM Search documentation](https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm)
and [association guide](https://developers.hubspot.com/docs/api-reference/latest/crm/associations/associate-records/guide)
for the upstream behavior summarized here.

Local application endpoints are:

- `POST /api/integrations/hubspot/test`
- `POST /api/integrations/hubspot/check` — read-only in HubSpot
- `POST /api/integrations/hubspot/sync` — requires a saved check batch and
  explicit per-record actions

Company matching priority is local HubSpot ID, optional LeadHarbor integration
ID, exact normalized domain, then company name with state/name-only as a review
candidate. Contact matching priority is local HubSpot ID, optional integration
ID, and exact normalized email. Phone or person name matches remain review
candidates and are never automatic exact matches. A strong possible duplicate
blocks automatic creation.

Check snapshots and local records retain the HubSpot Company/Contact IDs,
status, last check/sync timestamps, and last safe error. Before updating an
existing record, LeadHarbor reads it again; if HubSpot's `updatedAt` value has
changed since preview, the result is `RECHECK_REQUIRED`. This protects against
stale previews and double clicks.

### Manual HubSpot acceptance test

Use a test portal or clearly named test records; do not delete production data.

1. Test the connection with a valid token, an invalid token, and a token missing
   one required scope.
2. Check one company absent from HubSpot and verify no record is created.
3. Check one exact-domain duplicate and one name-only possible duplicate.
4. Check an existing company with an empty phone and a different non-empty
   field; verify the preview proposes only the empty phone by default.
5. Create one approved test Company, then repeat the same sync and verify no
   duplicate is created.
6. Create/enrich one real person Contact and verify its Company association.
7. Modify a HubSpot record after preview and verify synchronization requests a
   fresh check.
8. Check and synchronize a mixed batch containing one intentionally invalid
   record; verify other records succeed and each result remains visible.
9. Restart the desktop app and verify saved HubSpot relationships are reused.

Automated tests mock all HubSpot HTTP responses and never require or modify a
real portal. The integration uses the existing `requests` dependency, so no
additional runtime or SDK is required by Windows, Linux, or macOS packages.

Public data can have attribution and redistribution requirements. Follow the
source license, each website's terms, `robots.txt`, privacy rules, and applicable
anti-spam laws when using exported leads.
