"""Curated links to official EMA medicine pages and documents.

EMA links are deliberately curated rather than guessed from product names. A
single EMA medicine may cover many UK presentations and parallel imports, and
not every apparent brand-name match has the same regulatory scope.
"""
import re


LINKS = {
    "HUMALOG": {
        "medicine": "Humalog",
        "epar_url": "https://www.ema.europa.eu/en/medicines/human/EPAR/humalog",
        "product_info_url": (
            "https://www.ema.europa.eu/en/documents/product-information/"
            "humalog-epar-product-information_en.pdf"
        ),
        "presentations_url": (
            "https://www.ema.europa.eu/en/documents/all-authorised-presentations/"
            "humalog-epar-all-authorised-presentations_en.pdf"
        ),
    },
}


def for_product(product_name):
    """Return a curated EMA record for an exact leading brand token."""
    name = re.sub(r"\s+", " ", (product_name or "").strip().upper())
    for brand, links in LINKS.items():
        if name == brand or name.startswith(brand + " "):
            return dict(links)
    return None
