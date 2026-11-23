import asyncio
import json
import os
import re
from datetime import datetime, timezone
import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://medex.com.bd/brands"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CONCURRENCY = 8  # Safe concurrency limit to avoid rate-limiting or timeouts


def parse_price(price_text: str) -> dict:
    """Extracts clean float unit_price and pack_price from MedEx pricing text."""
    result = {
        "unit_price": None,
        "pack_price": None,
        "raw_price": re.sub(r"\s+", " ", price_text).strip() if price_text else "",
    }
    if not result["raw_price"]:
        return result

    unit_match = re.search(r"Unit Price:\s*৳?\s*([\d,]+(?:\.\d+)?)", result["raw_price"], re.I)
    if unit_match:
        result["unit_price"] = float(unit_match.group(1).replace(",", ""))

    pack_match = re.search(
        r"(?:Strip|Pack)\s*Price:\s*৳?\s*([\d,]+(?:\.\d+)?)", result["raw_price"], re.I
    )
    if pack_match:
        result["pack_price"] = float(pack_match.group(1).replace(",", ""))
    elif result["unit_price"] is None:
        # Handle single-price items (e.g., syrups, drops, ointments, inhalers)
        single_match = re.search(r"৳\s*([\d,]+(?:\.\d+)?)", result["raw_price"])
        if single_match:
            result["unit_price"] = float(single_match.group(1).replace(",", ""))

    return result


def extract_brands_from_html(html: str) -> list:
    """Parses all brand cards from a single MedEx brands listing page."""
    soup = BeautifulSoup(html, "lxml")
    brands = []

    cards = soup.select("a.hoverable-block")
    for card in cards:
        href = (card.get("href") or "").strip()
        if not href:
            continue

        # Extract numeric brand ID from URL like https://medex.com.bd/brands/1234/napa-500-mg-tablet
        id_match = re.search(r"/brands/(\d+)", href)
        brand_id = int(id_match.group(1)) if id_match else href

        # Brand Name & Dosage Form
        top_row = card.select_one(".data-row-top")
        dosage = ""
        name = ""
        if top_row:
            dosage_el = top_row.select_one(".grey-ligten")
            if dosage_el:
                dosage = dosage_el.get_text(" ", strip=True)
                dosage_el.decompose()
            name = top_row.get_text(" ", strip=True)

        # Strength
        strength_el = card.select_one(".data-row-strength")
        strength = strength_el.get_text(" ", strip=True) if strength_el else ""

        # Manufacturer / Company
        company_el = card.select_one(".data-row-company")
        company = company_el.get_text(" ", strip=True) if company_el else ""

        # Generic Name (typically a plain div without row classes or pricing)
        generic = ""
        for div in card.select("div"):
            classes = div.get("class") or []
            if any(
                c in classes
                for c in [
                    "data-row-top",
                    "data-row-strength",
                    "data-row-company",
                    "package-pricing",
                    "col-xs-12",
                    "row",
                ]
            ):
                continue
            text = div.get_text(" ", strip=True)
            if text and "৳" not in text and text not in (name, dosage, strength, company):
                generic = text
                break

        # Pricing
        price_el = card.select_one(".package-pricing")
        price_info = parse_price(price_el.get_text(" ", strip=True) if price_el else "")

        if name:
            brands.append(
                {
                    "id": brand_id,
                    "name": name,
                    "dosage_form": dosage,
                    "strength": strength,
                    "generic": generic,
                    "manufacturer": company,
                    "unit_price": price_info["unit_price"],
                    "pack_price": price_info["pack_price"],
                    "raw_price": price_info["raw_price"],
                    "url": href,
                }
            )

    return brands


async def get_total_pages(client: httpx.AsyncClient) -> int:
    """Fetches page 1 and determines total pagination count."""
    resp = await client.get(BASE_URL, headers=HEADERS, timeout=30.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    page_numbers = []
    for link in soup.select("ul.pagination li a"):
        text = link.get_text(strip=True)
        if text.isdigit():
            page_numbers.append(int(text))

    return max(page_numbers) if page_numbers else 1


async def fetch_page(
    client: httpx.AsyncClient, page: int, semaphore: asyncio.Semaphore
) -> list:
    """Fetches and parses a single page with exponential backoff retries."""
    url = f"{BASE_URL}?page={page}"
    async with semaphore:
        for attempt in range(4):
            try:
                await asyncio.sleep(0.35)
                resp = await client.get(url, headers=HEADERS, timeout=30.0)
                if resp.status_code == 200:
                    return extract_brands_from_html(resp.text)
                if resp.status_code in (429, 503):
                    await asyncio.sleep(3 * (attempt + 1))
            except Exception as exc:
                if attempt == 3:
                    print(f"[ERROR] Page {page} failed after retries: {exc}")
                await asyncio.sleep(2 * (attempt + 1))
    return []


async def main():
    os.makedirs("data", exist_ok=True)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        total_pages = await get_total_pages(client)
        print(f"Total pages discovered: {total_pages}")

        all_medicines = []
        seen_ids = set()

        # Process in batches of 50 pages to log progress cleanly
        batch_size = 50
        for start in range(1, total_pages + 1, batch_size):
            end = min(start + batch_size - 1, total_pages)
            tasks = [fetch_page(client, p, semaphore) for p in range(start, end + 1)]
            results = await asyncio.gather(*tasks)

            for page_items in results:
                for item in page_items:
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        all_medicines.append(item)

            print(f"Processed pages {start}-{end} | Total unique medicines: {len(all_medicines)}")

    if not all_medicines:
        raise RuntimeError("Scraper fetched 0 medicines. Aborting to prevent overwriting data with empty file.")

    # Sort deterministically by ID (numeric first, then string) to keep git diffs clean
    all_medicines.sort(key=lambda x: (0, x["id"]) if isinstance(x["id"], int) else (1, str(x["id"])))

    output_path = "data/medicines.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_medicines, f, ensure_ascii=False, indent=2)

    version_info = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_medicines": len(all_medicines),
        "source": "medex.com.bd",
    }
    with open("data/version.json", "w", encoding="utf-8") as f:
        json.dump(version_info, f, indent=2)

    print(f"Done! Saved {len(all_medicines)} medicines to {output_path}.")


if __name__ == "__main__":
    asyncio.run(main())
