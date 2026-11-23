# Bangladesh Medicine Price & Monograph Dataset (Auto-Updating)

An automated, zero-cost pipeline using **GitHub Actions** to scrape Bangladeshi medicine prices and clinical detail pages (Indications, Dosage & Administration, Side Effects, Pregnancy & Lactation, Precautions & Warnings, Interactions, Contraindications, Storage) from MedEx.

## Endpoints (GitHub Raw CDN)

Replace `<USERNAME>` and `<REPO>` with your GitHub username and repository name:

- **Version & Stats:**
  `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/version.json`
- **All Medicines (Prices + Summary):**
  `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/medicines.json`
- **Combined Detailed Monographs:**
  `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/medicine_details.json`
- **Single Medicine Detail (by ID):**
  `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/details/<BRAND_ID>.json`

## Detail Scraping Modes (Manual or CLI)

When clicking **Run workflow** in GitHub Actions, you can choose:
1. **`none`** *(default for weekly cron)*: Fast price & brand update (~2–3 mins).
2. **`filter`**: Scrape detail pages only for matching brand names (`Napa,Seclo`), generics (`Paracetamol,Omeprazole`), or IDs.
3. **`all`**: Scrape detail pages for all medicines. Use `details_limit` (e.g. `3000`) with `skip_existing_details: true` to safely build the full detail database across multiple runs without hitting rate limits.
