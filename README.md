# Bangladesh Medicine Price & Monograph Dataset (Auto-Updating)

An automated, zero-cost pipeline using **GitHub Actions** to scrape Bangladeshi medicine prices and clinical detail pages from MedEx.

## Endpoints (GitHub Raw CDN)

Replace `<USERNAME>` and `<REPO>` with your GitHub username and repository name:

- **Version & Stats:** `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/version.json`
- **All Medicines:** `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/medicines.json`
- **Combined Details:** `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/medicine_details.json`
- **Single Detail:** `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/details/<BRAND_ID>.json`
