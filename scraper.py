import argparse
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
LISTING_CONCURRENCY = 8
DETAIL_CONCURRENCY = 5  # Conservative concurrency for detail pages to avoid rate limits


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def slugify_key(label: str) -> str:
    """Converts section heading like 'Dosage & Administration' -> 'dosage_and_administration'."""
    s = label.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "section"


def parse_price(price_text: str) -> dict:
    """Extracts clean float unit_price and pack_price from MedEx pricing text."""
    raw = clean_text(price_text)
    result = {
        "unit_price": None,
        "pack_price": None,
        "raw_price": raw,
    }
    if not raw:
        return result

    unit_match = re.search(
        r"Unit Price:\s*৳?\s*([\d,]+(?:\.\d+)?)", raw, re.I
    )
    if unit_match:
        result["unit_price"] = float(unit_match.group(1).replace(",", ""))

    pack_match = re.search(
        r"(?:Strip|Pack)\s*Price:\s*৳?\s*([\d,]+(?:\.\d+)?)", raw, re.I
    )
    if pack_match:
        result["pack_price"] = float(pack_match.group(1).replace(",", ""))
    elif result["unit_price"] is None:
        single_match = re.search(r"৳\s*([\d,]+(?:\.\d+)?)", raw)
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

        id_match = re.search(r"/brands/(\d+)", href)
        brand_id = int(id_match.group(1)) if id_match else href

        top_row = card.select_one(".data-row-top")
        dosage = ""
        name = ""
        if top_row:
            dosage_el = top_row.select_one(".grey-ligten")
            if dosage_el:
                dosage = clean_text(dosage_el.get_text(" ", strip=True))
                dosage_el.decompose()
            name = clean_text(top_row.get_text(" ", strip=True))

        strength_el = card.select_one(".data-row-strength")
        strength = clean_text(strength_el.get_text(" ", strip=True)) if strength_el else ""

        company_el = card.select_one(".data-row-company")
        company = clean_text(company_el.get_text(" ", strip=True)) if company_el else ""

        generic = ""
        extra_labels = []
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
            text = clean_text(div.get_text(" ", strip=True))
            if text and "৳" not in text and text not in (name, dosage, strength, company):
                if not generic:
                    generic = text
                elif text not in extra_labels and text != generic:
                    extra_labels.append(text)

        price_el = card.select_one(".package-pricing")
        raw_price_text = price_el.get_text(" ", strip=True) if price_el else ""
        price_info = parse_price(raw_price_text)

        pack_details = ""
        if price_el:
            pack_spans = [
                clean_text(el.get_text(" ", strip=True))
                for el in price_el.select("span, small, .pack-size-info")
                if el.get_text(strip=True)
            ]
            non_price = [
                s for s in pack_spans if "৳" not in s and "price" not in s.lower()
            ]
            if non_price:
                pack_details = " | ".join(dict.fromkeys(non_price))

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
                    "pack_details": pack_details,
                    "extra_info": extra_labels,
                    "url": href,
                }
            )

    return brands


def extract_detail_from_html(html: str, brand: dict) -> dict:
    """Extracts clinical monograph sections (Indication, Dosage, Side Effects, etc.) from a brand detail page."""
    soup = BeautifulSoup(html, "lxml")

    detail = {
        "id": brand["id"],
        "name": brand["name"],
        "generic": brand["generic"],
        "strength": brand["strength"],
        "dosage_form": brand["dosage_form"],
        "manufacturer": brand["manufacturer"],
        "unit_price": brand["unit_price"],
        "pack_price": brand["pack_price"],
        "url": brand["url"],
        "generic_url": "",
        "drug_class": "",
        "indications": "",
        "composition": "",
        "pharmacology": "",
        "dosage_and_administration": "",
        "interaction": "",
        "contraindications": "",
        "side_effects": "",
        "pregnancy_and_lactation": "",
        "precautions_and_warnings": "",
        "use_in_special_populations": "",
        "overdose_effects": "",
        "therapeutic_class": "",
        "storage_conditions": "",
        "all_sections": {},
    }

    # Generic link if available
    gen_link = soup.select_one("a[href*='/generics/']")
    if gen_link and gen_link.get("href"):
        detail["generic_url"] = gen_link.get("href").strip()

    # Map common MedEx section headers to normalized keys
    header_map = {
        "indication": "indications",
        "indications": "indications",
        "composition": "composition",
        "pharmacology": "pharmacology",
        "dosage & administration": "dosage_and_administration",
        "dosage and administration": "dosage_and_administration",
        "interaction": "interaction",
        "interactions": "interaction",
        "contraindication": "contraindications",
        "contraindications": "contraindications",
        "side effect": "side_effects",
        "side effects": "side_effects",
        "pregnancy & lactation": "pregnancy_and_lactation",
        "pregnancy and lactation": "pregnancy_and_lactation",
        "precautions & warnings": "precautions_and_warnings",
        "precautions and warnings": "precautions_and_warnings",
        "precautions": "precautions_and_warnings",
        "use in special populations": "use_in_special_populations",
        "overdose effects": "overdose_effects",
        "therapeutic class": "therapeutic_class",
        "storage conditions": "storage_conditions",
    }

    # MedEx detail pages use .ac-header followed by .ac-body
    headers = soup.select(".ac-header")
    for hdr in headers:
        title = clean_text(hdr.get_text(" ", strip=True))
        body = hdr.find_next_sibling(class_="ac-body")
        if not body:
            parent = hdr.parent
            if parent:
                body = parent.select_one(".ac-body")
        if not body:
            continue

        # Preserve line breaks for bullet/paragraph readability
        for br in body.find_all("br"):
            br.replace_with("\n")
        raw_body = body.get_text("\n", strip=True)
        lines = [clean_text(line) for line in raw_body.splitlines() if clean_text(line)]
        content = "\n".join(lines)

        if not title or not content:
            continue

        detail["all_sections"][title] = content
        norm_title = title.lower()
        if norm_title in header_map:
            detail[header_map[norm_title]] = content
        else:
            key = slugify_key(title)
            if key in detail and not detail[key]:
                detail[key] = content

    if detail["therapeutic_class"] and not detail["drug_class"]:
        detail["drug_class"] = detail["therapeutic_class"]

    return detail


async def get_total_pages(client: httpx.AsyncClient) -> int:
    resp = await client.get(BASE_URL, headers=HEADERS, timeout=30.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    page_numbers = []
    for link in soup.select("ul.pagination li a"):
        text = link.get_text(strip=True)
        if text.isdigit():
            page_numbers.append(int(text))

    return max(page_numbers) if page_numbers else 1


async def fetch_listing_page(
    client: httpx.AsyncClient, page: int, semaphore: asyncio.Semaphore
) -> list:
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
                    print(f"[ERROR] Listing page {page} failed: {exc}")
                await asyncio.sleep(2 * (attempt + 1))
    return []


async def fetch_detail_page(
    client: httpx.AsyncClient, brand: dict, semaphore: asyncio.Semaphore
) -> dict | None:
    url = brand["url"]
    if not url:
        return None
    async with semaphore:
        for attempt in range(4):
            try:
                await asyncio.sleep(0.5)
                resp = await client.get(url, headers=HEADERS, timeout=35.0)
                if resp.status_code == 200:
                    return extract_detail_from_html(resp.text, brand)
                if resp.status_code in (429, 503):
                    await asyncio.sleep(4 * (attempt + 1))
            except Exception as exc:
                if attempt == 3:
                    print(f"[ERROR] Detail page {brand['id']} ({brand['name']}) failed: {exc}")
                await asyncio.sleep(2 * (attempt + 1))
    return None


def filter_brands_for_details(
    brands: list,
    mode: str,
    names: str = "",
    generics: str = "",
    ids: str = "",
    limit: int = 0,
) -> list:
    """Selects which brands should have their detail pages scraped."""
    if mode == "none":
        return []

    selected = brands
    if mode == "filter":
        name_tokens = [t.strip().lower() for t in names.split(",") if t.strip()]
        gen_tokens = [t.strip().lower() for t in generics.split(",") if t.strip()]
        id_tokens = {t.strip() for t in ids.split(",") if t.strip()}

        filtered = []
        for b in brands:
            b_id = str(b["id"])
            b_name = b["name"].lower()
            b_gen = b["generic"].lower()

            match_id = b_id in id_tokens if id_tokens else False
            match_name = any(tok in b_name for tok in name_tokens) if name_tokens else False
            match_gen = any(tok in b_gen for tok in gen_tokens) if gen_tokens else False

            if match_id or match_name or match_gen:
                filtered.append(b)
        selected = filtered

    if limit and limit > 0:
        selected = selected[:limit]

    return selected


async def main():
    parser = argparse.ArgumentParser(description="MedEx Bangladesh Medicine & Detail Scraper")
    parser.add_argument(
        "--details-mode",
        choices=["none", "filter", "all"],
        default=os.getenv("DETAILS_MODE", "none"),
        help="none = listing only; filter = specific names/generics/ids; all = all medicines",
    )
    parser.add_argument(
        "--names",
        default=os.getenv("FILTER_NAMES", ""),
        help="Comma-separated brand names to match (e.g. 'Napa,Seclo,Sergel')",
    )
    parser.add_argument(
        "--generics",
        default=os.getenv("FILTER_GENERICS", ""),
        help="Comma-separated generic names to match (e.g. 'Paracetamol,Omeprazole')",
    )
    parser.add_argument(
        "--ids",
        default=os.getenv("FILTER_IDS", ""),
        help="Comma-separated brand IDs to match (e.g. '1234,5678')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("DETAILS_LIMIT", "0") or 0),
        help="Optional max number of detail pages to fetch in one run (0 = no limit)",
    )
    parser.add_argument(
        "--skip-existing-details",
        action="store_true",
        default=os.getenv("SKIP_EXISTING_DETAILS", "true").lower() == "true",
        help="Skip fetching detail pages already saved in data/details/<id>.json",
    )
    args = parser.parse_args()

    os.makedirs("data/details", exist_ok=True)
    list_sem = asyncio.Semaphore(LISTING_CONCURRENCY)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        total_pages = await get_total_pages(client)
        print(f"Total listing pages discovered: {total_pages}")

        all_medicines = []
        seen_ids = set()

        batch_size = 50
        for start in range(1, total_pages + 1, batch_size):
            end = min(start + batch_size - 1, total_pages)
            tasks = [fetch_listing_page(client, p, list_sem) for p in range(start, end + 1)]
            results = await asyncio.gather(*tasks)

            for page_items in results:
                for item in page_items:
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        all_medicines.append(item)

            print(
                f"Processed listing pages {start}-{end} | Unique medicines: {len(all_medicines)}"
            )

        if not all_medicines:
            raise RuntimeError(
                "Scraper fetched 0 medicines. Aborting to prevent overwriting data."
            )

        all_medicines.sort(
            key=lambda x: (0, x["id"]) if isinstance(x["id"], int) else (1, str(x["id"]))
        )

        # Scrape detail pages if requested
        targets = filter_brands_for_details(
            all_medicines,
            mode=args.details_mode,
            names=args.names,
            generics=args.generics,
            ids=args.ids,
            limit=args.limit,
        )

        if targets:
            print(f"Detail scrape mode='{args.details_mode}' | Target count: {len(targets)}")
            detail_sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

            to_fetch = []
            for b in targets:
                detail_file = f"data/details/{b['id']}.json"
                if args.skip_existing_details and os.path.exists(detail_file):
                    continue
                to_fetch.append(b)

            print(f"Fetching {len(to_fetch)} new detail pages (skipped {len(targets) - len(to_fetch)} cached)...")

            detail_batch = 100
            for start in range(0, len(to_fetch), detail_batch):
                chunk = to_fetch[start : start + detail_batch]
                tasks = [fetch_detail_page(client, b, detail_sem) for b in chunk]
                detail_results = await asyncio.gather(*tasks)

                for det in detail_results:
                    if det:
                        detail_path = f"data/details/{det['id']}.json"
                        with open(detail_path, "w", encoding="utf-8") as df:
                            json.dump(det, df, ensure_ascii=False, indent=2)

                print(f"Saved detail batch {start + 1}-{start + len(chunk)}")

    # Mark which medicines have local detail JSON available and build combined details index
    available_detail_ids = set()
    all_details_list = []
    if os.path.isdir("data/details"):
        for fname in sorted(os.listdir("data/details")):
            if fname.endswith(".json"):
                fpath = os.path.join("data/details", fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as df:
                        d_obj = json.load(df)
                        available_detail_ids.add(str(d_obj.get("id")))
                        all_details_list.append(d_obj)
                except Exception:
                    pass

    for med in all_medicines:
        has_det = str(med["id"]) in available_detail_ids
        med["has_details"] = has_det
        med["detail_path"] = f"data/details/{med['id']}.json" if has_det else None

    with open("data/medicines.json", "w", encoding="utf-8") as f:
        json.dump(all_medicines, f, ensure_ascii=False, indent=2)

    all_details_list.sort(
        key=lambda x: (0, x["id"]) if isinstance(x["id"], int) else (1, str(x["id"]))
    )
    with open("data/medicine_details.json", "w", encoding="utf-8") as f:
        json.dump(all_details_list, f, ensure_ascii=False, indent=2)

    version_info = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_medicines": len(all_medicines),
        "total_detailed_medicines": len(all_details_list),
        "source": "medex.com.bd",
    }
    with open("data/version.json", "w", encoding="utf-8") as f:
        json.dump(version_info, f, indent=2)

    print(
        f"Done! Saved {len(all_medicines)} medicines and {len(all_details_list)} detailed monographs."
    )


if __name__ == "__main__":
    asyncio.run(main())
