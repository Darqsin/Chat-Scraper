# Maricopa County NTS Scraper

Automated Notice of Trustee Sale lead collection for the Maricopa County Recorder portal.

## What it does

- Searches the Maricopa Recorder document search page for `NS` / Notice of Trustee Sale
- Supports either manual `--start-date` and `--end-date` or a `--lookback-days 1` default run
- Opens each result, finds the PDF, downloads it, parses the text, and falls back to OCR when needed
- Produces:
  - `dashboard/records.json`
  - `data/records.json`
  - `data/ghl_export.csv`
  - `nts_data.csv`
  - `nts_data.xlsx`
  - downloaded PDFs in `grouped_output/pdfs/`
  - raw extracted text in `parsed_output/`

## Project structure

```text
Project/
├── .github/workflows/scrape.yml
├── dashboard/
│   ├── index.html
│   └── records.json
├── data/
│   ├── ghl_export.csv
│   └── records.json
├── grouped_output/pdfs/
├── parsed_output/
├── scraper/
│   ├── clerk_scraper.py
│   ├── enricher.py
│   ├── exporter.py
│   └── fetch.py
├── nts_data.csv
├── nts_data.xlsx
├── scraper.log
└── README.md
```

## Local setup

```bash
pip install requests beautifulsoup4 lxml dbfread playwright pdf2image pytesseract openpyxl pillow usaddress pdfplumber img2pdf
python -m playwright install --with-deps chromium
```

### Additional system dependencies

- **Tesseract OCR** must be installed and available in PATH
- **Poppler** is required for `pdf2image`

Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr
```

Windows:

- Install Tesseract OCR
- Install Poppler and add its `bin` folder to PATH

## Usage

From the `scraper/` folder:

### Default 1-day lookback

```bash
python fetch.py --lookback-days 1
```

### Manual date entry

```bash
python fetch.py --start-date 04/14/2026 --end-date 04/14/2026
```

### Visible browser for debugging

```bash
python fetch.py --start-date 04/14/2026 --end-date 04/14/2026 --headful --slow-mo 250 --verbose
```

## Notes

- The Recorder site is dynamic, so selectors are written with fallbacks.
- If the county changes the search form or result page markup, adjust selectors in `scraper/clerk_scraper.py`.
- Address/value enrichment can be added later in `scraper/enricher.py`.
