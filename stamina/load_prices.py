#!/usr/bin/env python3
"""
load_prices.py — Parse TodoPrecios.csv and provide price lookups for
(sku, size_name, color_name) triples as used in make_variations.py.

Price CSV structure (TodoPrecios.csv):
    Ref., Nombre, Género, Talla, Color, PRECIO

- Ref. is only filled on the first row of each product; subsequent rows
  carry forward the same ref.
- Talla uses range notation (e.g. "S-2XL", "3XL", "XS-2XL").
- Color uses tier categories, not actual color names:
    "Blanco ..."        -> tier "blanco"   (white products)
    "Color ..."         -> tier "color"    (all other colors, catch-all)
    "Blanco/Color ..."  -> tier "all"      (same price for white and color)
    "Negro ..."         -> tier "negro"
    "Vigore ..."        -> tier "vigore"
    "Marino ..."        -> tier "marino"
    "Color Fluor ..."   -> tier "fluor"

Size ranges use a standard ordered sequence for letter sizes. Numeric
ranges (shoe sizes, garment sizes) are expanded as integer sequences.
"""

import csv
import re
from pathlib import Path

# Ordered letter-size sequence used to resolve ranges like "S-2XL"
_LETTER_SIZES = [
    "3XS", "2XS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "5XL",
]
_LETTER_SIZE_INDEX = {s: i for i, s in enumerate(_LETTER_SIZES)}

# Aliases: normalize size spellings to canonical form before lookup
_SIZE_ALIASES = {
    "2XL": "XXL",  # treat 2XL and XXL as the same
    "4 XL": "4XL",
    "S-2L": "S-XL",  # apparent typo in price data
    # range aliases (in price CSV) -> kept as-is after normalization
}

# Sizes that mean "one size fits all" in the price CSV
_UNICA_SIZES = {"UNICA", "One Size", "One size", "Talla unica", "Senior"}

# Sizes that mean children's numeric sizes 2-16 in the price CSV
_KIDS_SIZES = {"Kids", "kids", "Niño - Kid - Enfant"}
_KIDS_NUMERIC = {str(n) for n in range(2, 17)}.union({f"{n * 3} MESES" for n in range(1, 7)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_size(s: str) -> str:
    s = s.strip()
    return _SIZE_ALIASES.get(s, s)


def _expand_size_range(size_range: str) -> set[str]:
    """
    Expand a price-CSV size range to the set of individual sizes it covers.

    Examples:
        "S-2XL"    -> {"S","M","L","XL","XXL","2XL"}  (note: 2XL alias for XXL)
        "XS-3XL"   -> {"XS","S","M","L","XL","XXL","2XL","3XL"}
        "3XL-4XL"  -> {"3XL","4XL"}
        "3XL"      -> {"3XL"}
        "36-48"    -> {"36","37","38",...,"48"}
        "36/39"    -> {"36","39"}          (slash = explicit list, not range)
        "M/L - XL/2XL" -> {"M","L","XL","XXL"}
        "Kids"     -> {"UNICA"}
    """
    s = _normalize_size(size_range)

    # UNICA-equivalent
    if s in _UNICA_SIZES:
        return {"UNICA"}

    # Kids sizes → numeric children's sizes 2-16
    if s in _KIDS_SIZES:
        return _KIDS_NUMERIC

    # M/L - XL/2XL style: two slash-groups separated by " - "
    if " - " in s and "/" in s:
        result = set()
        for part in re.split(r"\s*-\s*", s):
            for sub in part.split("/"):
                sub = _normalize_size(sub.strip())
                result.add(sub)
        return result

    # Slash-separated explicit list: "36/39", "40/42", "43/46"
    if "/" in s and "-" not in s:
        return {_normalize_size(p.strip()) for p in s.split("/")}

    # Hyphen range
    if "-" in s:
        parts = s.split("-", 1)
        lo, hi = parts[0].strip(), parts[1].strip()
        lo, hi = _normalize_size(lo), _normalize_size(hi)

        # Letter-size range
        if lo in _LETTER_SIZE_INDEX and hi in _LETTER_SIZE_INDEX:
            i0, i1 = _LETTER_SIZE_INDEX[lo], _LETTER_SIZE_INDEX[hi]
            return set(_LETTER_SIZES[i0: i1 + 1])

        # Numeric range (shoe/garment sizes)
        try:
            n0, n1 = int(lo), int(hi)
            return {str(n) for n in range(n0, n1 + 1)}
        except ValueError:
            pass

    # Single value
    return {_normalize_size(s)}


def _classify_price_color(price_color: str) -> list[str]:
    """
    Map a price-CSV color description to one or more tier keys.
    Returns a list so "Blanco/Color" can map to both tiers.
    """
    c = price_color.strip().upper()
    tiers = []
    if "VIGORE" in c:
        tiers.append("vigore")
    if "FLUOR" in c:
        tiers.append("fluor")
    if "MARINO" in c:
        tiers.append("marino")
    if "NEGRO" in c and "BLANCO" not in c and "COLOR" not in c:
        tiers.append("negro")
    if "BLANCO" in c and "COLOR" not in c:
        tiers.append("blanco")
    if "COLOR" in c and "BLANCO" not in c:
        tiers.append("color")
    if "BLANCO" in c and "COLOR" in c:
        tiers.append("blanco")
        tiers.append("color")
    return tiers if tiers else ["color"]


def _classify_variation_color(color_name: str) -> list[str]:
    """
    Map an actual variation color name (e.g. "NEGRO VIGORE") to the ordered
    list of price tiers to try, most-specific first.
    """
    c = color_name.strip().upper()
    tiers = []
    if "VIGORE" in c:
        tiers.append("vigore")
    if "FLUOR" in c:
        tiers.append("fluor")
    if "MARINO" in c:
        tiers.append("marino")
    # Only pure NEGRO (not "NEGRO VIGORE" etc.) gets the negro tier first
    if c == "NEGRO" or c.startswith("NEGRO ") and "VIGORE" not in c and "FLUOR" not in c:
        tiers.append("negro")
    if "BLANCO" in c:
        tiers.append("blanco")
    # catch-all always last
    tiers.append("color")
    return tiers


def _expand_variation_size(size_name: str) -> list[str]:
    """
    Expand a variation size name into the list of candidate sizes to try
    for price lookup, in preference order.

    Cases:
      "S-M"   -> ["S", "M"]        (adjacent letter-size range in products CSV)
      "3/4"   -> ["3", "4"]        (kids slash-pair meaning either size)
      "UNICA" -> ["UNICA"]
      "XS"    -> ["XS"]            (plain size, no expansion needed)
      "2"     -> ["2"]             (numeric, could match Kids range in table)
    """
    s = _normalize_size(size_name.strip())

    # Adjacent letter-size range: "S-M", "M-L", "L-XL", "XL-XXL", "XS-S" etc.
    if "-" in s:
        parts = s.split("-", 1)
        lo, hi = _normalize_size(parts[0].strip()), _normalize_size(parts[1].strip())
        if lo in _LETTER_SIZE_INDEX and hi in _LETTER_SIZE_INDEX:
            i0, i1 = _LETTER_SIZE_INDEX[lo], _LETTER_SIZE_INDEX[hi]
            return list(_LETTER_SIZES[i0: i1 + 1])

    # Slash-pair kids size: "3/4", "5/6", "7/8", "9/10", "11/12"
    if "/" in s:
        parts = [_normalize_size(p.strip()) for p in s.split("/")]
        return parts

    return [s]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PriceLookup:
    """
    Loaded price table supporting lookup by (sku, size_name, color_name).

    Internal structure:
        self._table[sku][(size_individual, tier)] = price_str
    """

    def __init__(self, prices_csv: str | Path):
        self._table: dict[str, dict[tuple[str, str], str]] = {}
        self._load(prices_csv)

    def _load(self, path: str | Path) -> None:
        current_ref = None
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            next(reader)  # skip header
            for row in reader:
                if not any(c.strip() for c in row):
                    continue
                if row[0].strip():
                    current_ref = row[0].strip()
                if not current_ref:
                    continue
                if len(row) < 6:
                    continue
                size_raw = row[3].strip()
                color_raw = row[4].strip()
                price = row[5].strip()
                if not size_raw or not color_raw or not price:
                    continue

                sizes = _expand_size_range(size_raw)
                tiers = _classify_price_color(color_raw)

                entry = self._table.setdefault(current_ref, {})
                for size in sizes:
                    for tier in tiers:
                        # Don't overwrite: first entry wins (preserves row order)
                        entry.setdefault((size, tier), price)

    def lookup(self, sku: str, size_name: str, color_name: str) -> str:
        """
        Return the price string for a variation, or "" if not found.

        Size expansion:
          - "S-M" or "3XS-2XS" (adjacent range in products CSV) -> try each size
          - "3/4" (kids slash-pair) -> try "3" then "4"
          - numeric sizes like "2"-"16" also match a "Kids" price entry

        Color matching tries tiers most-specific-first (vigore > fluor > marino
        > negro > blanco > color).
        """
        entry = self._table.get(sku)
        if not entry:
            return ""

        candidate_sizes = _expand_variation_size(size_name)
        tiers = _classify_variation_color(color_name)

        for size in candidate_sizes:
            # 1. Try each color tier for this size
            for tier in tiers:
                price = entry.get((size, tier))
                if price:
                    return price

            # 2. No-color fallback: use "blanco" then "color"
            if not color_name:
                for tier in ("blanco", "color"):
                    price = entry.get((size, tier))
                    if price:
                        return price

            # 3. Any tier for this size
            for (s, t), price in entry.items():
                if s == size:
                    return price

        # 4. UNICA fallback: any price for the SKU
        if _normalize_size(size_name) == "UNICA":
            if entry:
                return next(iter(entry.values()))

        return ""

    def has_sku(self, sku: str) -> bool:
        return sku in self._table


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python load_prices.py <TodoPrecios.csv>")
        sys.exit(1)

    pl = PriceLookup(sys.argv[1])
    print(f"Loaded {len(pl._table)} SKUs\n")

    tests = [
        ("0304", "S", "BLANCO"),
        ("0304", "S", "NEGRO"),
        ("0304", "3XL", "BLANCO"),
        ("6554", "XS", "BLANCO"),
        ("6554", "3XL", "ROJO"),
        ("6554", "XS", "NEGRO VIGORE"),
        ("8388", "48", "NEGRO/PLOMO"),
    ]
    for sku, size, color in tests:
        price = pl.lookup(sku, size, color)
        print(f"  sku={sku} size={size!r:6} color={color!r:20} -> {price!r}")
