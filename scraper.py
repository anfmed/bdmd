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
LISTING_CONCURRENCY = 6
DETAIL_CONCURRENCY = 4


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def slugify_key(label: str) -> str:
    s = label.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "section"


def parse_price(price_text: str) -> dict:
    raw = clean_text(price_text)
    result = {
        "unit_price": None,
        "pack_price": None,
        "raw_price": raw,
    }
    if not raw:
        return result

    unit_match = re.search(
        r"Unit Price:\s*(?:৳|BDT|Tk\.?|เงณ)?\s*([\d,]+(?:\.\d+)?)", raw, re.I
    )
    if unit_match:
        result["unit_price"] = float(unit_match.group(1).replace(",", ""))

    pack_match = re.search(
        r"(?:Strip|Pack)\s*Price:\s*(?:৳|BDT|Tk\.?|เงณ)?\s*([\d,]+(?:\.\d+)?)", raw, re.I
    )
    if pack_match:
        result["pack_price"] = float(pack_match.group(1).replace(",", ""))
    elif result["unit_price"] is None:
        single_match = re.search(r"(?:৳|BDT|Tk\.?|เงณ)\s*([\d,]+(?:\.\d+)?)", raw, re.I)
        if single_match:
            result["unit_price"] = float(single_match.group(1).replace(",", ""))

    return result


def extract_brands_from_html(html: str) -> list:
    """Flexible parser for MedEx brand listing cards across layout variations."""
    soup = BeautifulSoup(html, "lxml")
    brands = []

    # Candidate card containers: primary a.hoverable-block, fallback to links pointing to /brands/<id>/...
    cards = soup.select("a.hoverable-block")
    if not cards:
        cards = [
            a
            for a in soup.select("a[href]")
            if re.search(r"/brands/\d+", a.get("href") or "")
            and any(k in (a.get_text(" ", strip=True).lower()) for k in ["৳", "bdt", "unit price", "strip price", "pack price", "mg", "ml", "%"])
        ]

    for card in cards:
        href = (card.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("/"):
            href = f"https://medex.com.bd{href}"

        id_match = re.search(r"/brands/(\d+)", href)
        brand_id = int(id_match.group(1)) if id_match else href

        # 1. Brand Name & Dosage Form
        dosage = ""
        name = ""
        top_row = card.select_one(".data-row-top, .brand-name, .row-title, h1, h2, h3, h4, strong, b")
        if top_row:
            dosage_el = top_row.select_one(".grey-ligten, .dosage-form, .form, small, span.text-muted")
            if dosage_el:
                dosage = clean_text(dosage_el.get_text(" ", strip=True))
                dosage_el.decompose()
            name = clean_text(top_row.get_text(" ", strip=True))

        # 2. Strength
        strength = ""
        strength_el = card.select_one(".data-row-strength, .strength, .drug-strength")
        if strength_el:
            strength = clean_text(strength_el.get_text(" ", strip=True))
        else:
            for el in card.select("span, small, div"):
                txt = clean_text(el.get_text(" ", strip=True))
                if txt and txt != name and re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|iu|%)\b", txt, re.I) and len(txt) < 60:
                    strength = txt
                    break

        # 3. Manufacturer / Company
        company = ""
        company_el = card.select_one(".data-row-company, .company-name, .manufacturer, .pharmaceutical")
        if company_el:
            company = clean_text(company_el.get_text(" ", strip=True))

        # 4. Generic Name & extra non-pricing labels
        generic = ""
        extra_labels = []
        gen_el = card.select_one("[title*='Generic' i], .generic-name, .data-row-generic")
        if gen_el:
            generic = clean_text(gen_el.get_text(" ", strip=True))

        for div in card.select("div, span, p, small"):
            if div.find(["div", "p"]):
                continue
            txt = clean_text(div.get_text(" ", strip=True))
            if not txt:
                continue
            if any(tok in txt.lower() for tok in ["৳", "bdt", "unit price", "strip price", "pack price"]):
                continue
            if txt in (name, dosage, strength, company, generic):
                continue
            if not generic and len(txt) < 120:
                generic = txt
            elif txt not in extra_labels and len(txt) < 140:
                extra_labels.append(txt)

        # 5. Pricing & pack details
        price_el = card.select_one(".package-pricing, .price, .pricing")
        if price_el:
            raw_price_text = price_el.get_text(" ", strip=True)
        else:
            card_full = clean_text(card.get_text(" ", strip=True))
            price_chunks = re.findall(
                r"(?:Unit Price|Strip Price|Pack Price)?\s*:?\s*(?:৳|BDT|Tk\.?)\s*[\d,]+(?:\.\d+)?",
                card_full,
                re.I,
            )
            raw_price_text = " | ".join(price_chunks)

        price_info = parse_price(raw_price_text)

        pack_details = ""
        if price_el:
            pack_spans = [
                clean_text(el.get_text(" ", strip=True))
                for el in price_el.select("span, small, .pack-size-info")
                if el.get_text(strip=True)
            ]
            non_price = [
                s for s in pack_spans if not any(k in s.lower() for k in ["৳", "bdt", "price"])
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

    gen_link = soup.select_one("a[href*='/generics/']")
    if gen_link and gen_link.get("href"):
        g_href = gen_link.get("href").strip()
        detail["generic_url"] = f"https://medex.com.bd{g_href}" if g_href.startswith("/") else g_href

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

    headers = soup.select(".ac-header")
    for hdr in headers:
        title = clean_text(hdr.get_text(" ", strip=True))
        body = hdr.find_next_sibling(class_="ac-body")
        if not body and hdr.parent:
            body = hdr.parent.select_one(".ac-body")
        if not body:
            continue

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


async def get_total_pages(client: httpx.AsyncClient) -> tuple[int, str]:
    resp = await client.get(BASE_URL, headers=HEADERS, timeout=30.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    page_numbers = []
    for link in soup.select("ul.pagination li a, .pagination a, nav a"):
        text = link.get_text(strip=True)
        if text.isdigit():
            page_numbers.append(int(text))

    return (max(page_numbers) if page_numbers else 1), resp.text


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
    )
    parser.add_argument("--names", default=os.getenv("FILTER_NAMES", ""))
    parser.add_argument("--generics", default=os.getenv("FILTER_GENERICS", ""))
    parser.add_argument("--ids", default=os.getenv("FILTER_IDS", ""))
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("DETAILS_LIMIT", "0") or 0),
    )
    parser.add_argument(
        "--skip-existing-details",
        action="store_true",
        default=os.getenv("SKIP_EXISTING_DETAILS", "true").lower() == "true",
    )
    args = parser.parse_args()

    os.makedirs("data/details", exist_ok=True)
    list_sem = asyncio.Semaphore(LISTING_CONCURRENCY)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        total_pages, first_html = await get_total_pages(client)
        print(f"Total listing pages discovered: {total_pages}")

        all_medicines = []
        seen_ids = set()

        # Parse page 1 first so we have immediate results and diagnostic visibility
        page1_items = extract_brands_from_html(first_html)
        for item in page1_items:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                all_medicines.append(item)
        print(f"Page 1 parsed: {len(all_medicines)} medicines")

        batch_size = 50
        for start in range(2, total_pages + 1, batch_size):
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
            snippet = clean_text(first_html)[:400]
            raise RuntimeError(
                f"Scraper fetched 0 medicines. Page 1 HTML snippet: {snippet}"
            )

        all_medicines.sort(
            key=lambda x: (0, x["id"]) if isinstance(x["id"], int) else (1, str(x["id"]))
        )

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
