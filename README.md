# Bangladesh Medicine Price Dataset (Auto-Updating)

An automated, zero-cost pipeline using **GitHub Actions** to keep a structured JSON dataset of Bangladeshi medicine brands, generics, manufacturers, and prices up to date.

## Endpoints (GitHub Raw CDN)

Replace `<USERNAME>` and `<REPO>` with your GitHub username and repository name:

- **Metadata / Version Check:**
  `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/version.json`
- **Full Dataset:**
  `https://raw.githubusercontent.com/<USERNAME>/<REPO>/main/data/medicines.json`

## Setup Instructions

1. Push this repository to GitHub (`main` branch).
2. Go to **Settings > Actions > General** in your repository.
3. Under **Workflow permissions**, select **Read and write permissions** and click **Save**.
4. Go to the **Actions** tab, select **Update BD Medicine Prices**, and click **Run workflow** to trigger the initial scrape.
5. After that, the workflow automatically runs every Friday at 3:00 AM (BST) and commits updates only when data changes.
