"""Read the public MHRA Products Azure Search catalogue."""
import json
import time
import urllib.parse
import urllib.request

SERVICE = "https://mhraproducts4853.search.windows.net"
INDEX = "products-index"
# This is the public read-only query key used by the MHRA Products website.
QUERY_KEY = "17CCFC430C1A78A169B392A35A99C49D"
API_VERSION = "2017-11-11"
FIELDS = (
    "metadata_storage_name", "metadata_storage_path", "doc_type",
    "product_name", "pl_number", "substance_name", "created", "rev_label",
    "title", "file_name", "metadata_storage_size", "territory", "release_state",
)


def _get(url, timeout=60, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "THIL-MHRA-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last


def _page(skip, top=1000):
    query = urllib.parse.urlencode({
        "api-version": API_VERSION,
        "api-key": QUERY_KEY,
        "search": "*",
        "$count": "true",
        "$top": top,
        "$skip": skip,
        "$orderby": "created desc",
        "$select": ",".join(FIELDS),
    })
    return _get(f"{SERVICE}/indexes/{INDEX}/docs?{query}")


def harvest(progress=print):
    """Yield all catalogue records, newest first, deduplicated by content hash."""
    seen = set()
    total = None
    skip = 0
    while skip < 100000:
        page = _page(skip)
        if total is None:
            total = page.get("@odata.count")
            if progress:
                progress(f"MHRA catalogue total: {total}")
        values = page.get("value", [])
        if not values:
            break
        for value in values:
            name = value.get("metadata_storage_name")
            if name and name not in seen:
                seen.add(name)
                yield {key: value.get(key) for key in FIELDS}
        skip += len(values)
        if progress and skip % 10000 < 1000:
            progress(f"  checked {len(seen)}/{total}")
        if total is not None and skip >= total:
            break
