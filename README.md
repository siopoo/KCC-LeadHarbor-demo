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

The macOS packages use an ad-hoc signature for internal testing. They are not
Apple-notarized, so the first launch may require Control-clicking the app and
choosing **Open**. External distribution requires a Developer ID Application
certificate and Apple notarization.

Public data can have attribution and redistribution requirements. Follow the
source license, each website's terms, `robots.txt`, privacy rules, and applicable
anti-spam laws when using exported leads.
