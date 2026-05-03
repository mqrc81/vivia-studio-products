#!/usr/bin/env python3
"""
make_variations.py — Convert a products CSV into a WooCommerce-style
variable/variation/simple CSV.

Usage:
    python make_variations.py products.csv
    python make_variations.py products.csv output.csv
    python make_variations.py products.csv output.csv prices.csv images.csv

Rules:
    - Missing size          -> replaced with "UNICA"
    - Missing color         -> left empty; Color attribute omitted if always empty
    - 1 size + 1 color      -> "simple", both as regular (non-variation) attributes
    - Multiple sizes only   -> "variable", Talla drives variations,
                               Color is regular attr on variable row only
    - Multiple colors only  -> "variable", Color drives variations,
                               Talla is regular attr on variable row only
    - Multiple of both      -> "variable", both drive variations

Side outputs (alongside the main output file):
    <stem>_output_errors.csv  : rows that could not be parsed
    <stem>_size_counts.csv    : each size name with how many times it appears
    <stem>_color_counts.csv   : each color name with how many times it appears
"""

import csv
import re
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_products import parse_variation
from load_prices import PriceLookup

DEFAULT_SIZE = "UNICA"


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x == y:
            n += 1
        else:
            break
    return n


def _update_size_candidates(candidates: list, new_chars: str, id_str: str) -> None:
    """Refine prefix candidates for a size ID (keep at least 2 leading chars)."""
    if len(new_chars) < 2:
        candidates.append(f'INVALID-${id_str}')
        return
    matched = False
    for i, P in enumerate(candidates):
        clen = _common_prefix_len(P, new_chars)
        if clen >= 2:
            candidates[i] = new_chars[:clen]
            matched = True
    if not matched:
        candidates.append(new_chars)


def _update_color_candidates(candidates: list, new_chars: str, id_str: str) -> None:
    """Refine suffix candidates for a color ID (keep at least 2 trailing chars)."""
    if len(new_chars) < 2:
        candidates.append(f'INVALID-${id_str}')
        return
    matched = False
    for i, P in enumerate(candidates):
        clen = _common_prefix_len(P[::-1], new_chars[::-1])  # common suffix
        if clen >= 2:
            candidates[i] = new_chars[-clen:]
            matched = True
    if not matched:
        candidates.append(new_chars)


def slugify(text: str) -> str:
    """Strip all non-alphanumeric characters (keep letters and digits only)."""
    return re.sub(r"[^A-Za-z0-9]", "", text)


def main():
    if len(sys.argv) < 2:
        print("Usage: python make_variations.py <products.csv> [output.csv]")
        sys.exit(1)

    products_csv = Path(sys.argv[1])
    out_csv = Path(sys.argv[2]) if len(sys.argv) >= 3 else \
        products_csv.parent / (products_csv.stem + "_output.csv")
    prices_csv  = Path(sys.argv[3]) if len(sys.argv) >= 4 else None
    images_csv  = Path(sys.argv[4]) if len(sys.argv) >= 5 else None
    stem = products_csv.stem
    parent = products_csv.parent
    errors_csv = parent / (stem + "_output_errors.csv")
    size_counts_csv = parent / (stem + "_size_counts.csv")
    color_counts_csv = parent / (stem + "_color_counts.csv")

    price_lookup = PriceLookup(prices_csv) if prices_csv else None
    if price_lookup:
        print(f"Loaded prices for {len(price_lookup._table)} SKUs from: {prices_csv.name}")

    # sku -> {categories, images}
    meta: dict[str, dict] = {}
    if images_csv:
        with open(images_csv, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            img_header = [h.strip() for h in next(reader)]
            # ID is always at index 0, Categorías at 27, Imágenes at 30
            for row in reader:
                if not row or not row[0].strip():
                    continue
                sku_id = row[0].strip().zfill(4)
                meta[sku_id] = {
                    "categories": row[27].strip() if len(row) > 27 else "",
                    "images":     row[30].strip() if len(row) > 30 else "",
                }
        print(f"Loaded meta for {len(meta)} SKUs from: {images_csv.name}")

    # ── 1. Read and group by SKU ──────────────────────────────────────────────
    skus: dict[str, dict] = {}
    errors: dict[str, str] = {}
    size_stats: dict[str, dict] = {}
    color_stats: dict[str, dict] = {}

    with open(products_csv, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for lineno, row in enumerate(reader, start=1):
            if lineno == 1 and (not row or not re.match(r"^[A-Za-z]{2}\d", row[0].strip())):
                continue
            if not row or all(c.strip() == "" for c in row):
                continue
            if len(row) < 2:
                errors[f"<line {lineno}>"] = "Row has fewer than 2 columns"
                continue

            id_str = row[0].strip()
            variation = row[1].strip()

            m = re.match(r"^[A-Za-z]{2}(\d{4})", id_str)
            if not m:
                errors[id_str] = "ID format not recognised (expected 2 letters + at least 6 digits)"
                continue

            sku = m.group(1)

            result = parse_variation(variation)
            if result is None:
                errors[id_str] = "Cannot parse variation — no recognisable 'T/<size>' found"
                continue

            product_name, size_name, color_name = result
            if not size_name:
                size_name = DEFAULT_SIZE
            color_name = color_name.strip()
            size_name = size_name.strip()

            # Only extract size/color candidates from IDs with a valid structure.
            # Minimum valid ID: 2 letters + 4 SKU + 2 size + 2 color = 10 chars.
            # Short IDs like DE912302 (8 chars) have no size segment, so fixed
            # offsets would extract garbage (e.g. the color digits at position 6-8).
            if len(id_str) >= 10:
                size_chars = id_str[6:9]
                if size_name not in size_stats:
                    size_stats[size_name] = {"count": 1, "candidates": [size_chars]}
                else:
                    size_stats[size_name]["count"] += 1
                    _update_size_candidates(size_stats[size_name]["candidates"], size_chars, id_str)

                if color_name:
                    color_chars = id_str[8:]
                    if color_name not in color_stats:
                        color_stats[color_name] = {"count": 1, "candidates": [color_chars]}
                    else:
                        color_stats[color_name]["count"] += 1
                        _update_color_candidates(color_stats[color_name]["candidates"], color_chars, id_str)

            if sku not in skus:
                skus[sku] = {"name": product_name, "variations": [], "seen": set()}

            combo = (size_name, color_name)
            if combo not in skus[sku]["seen"]:
                skus[sku]["seen"].add(combo)
                skus[sku]["variations"].append(combo)

    # ── 2. Write output CSV ───────────────────────────────────────────────────
    HEADER = [
        "Tipo", "SKU", "Nombre",
        "Publicado", "¿Está destacado?", "Visibilidad en el catálogo",
        "Estado del impuesto", "Clase de impuesto",
        "¿Existencias?", "¿Permitir reservas de productos agotados?",
        "¿Vendido individualmente?", "¿Permitir valoraciones de clientes?",
        "Superior", "Posición",
        "Precio normal",
        "Categorías", "Imágenes",
        "Nombre del atributo 1", "Valor(es) del atributo 1",
        "Atributo visible 1", "Atributo global 1",
        "Nombre del atributo 2", "Valor(es) del atributo 2",
        "Atributo visible 2", "Atributo global 2",
    ]

    COMMON = ["1", "0", "visible", "none", "", "1", "0", "0", "0"]

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)

        for sku in sorted(skus):
            name = skus[sku]["name"]
            variations = skus[sku]["variations"]

            all_sizes = list(dict.fromkeys(s for s, c in variations))
            all_colors = list(dict.fromkeys(c for s, c in variations))

            multi_size = len(all_sizes) > 1
            multi_color = len(all_colors) > 1
            has_color = any(all_colors)

            size_is_variation = multi_size
            color_is_variation = multi_color and has_color

            def attr1(value):
                return ["Talla", value, "1", "1"]

            def attr2(value):
                return (["Color", value, "1", "1"] if has_color else ["", "", "", ""])

            # ── price error helpers ───────────────────────────────────────────
            def price_for(row_sku, size, color):
                """Look up price and log to errors if missing or SKU unknown."""
                if not price_lookup:
                    return ""
                if not price_lookup.has_sku(sku):
                    errors[row_sku] = f"No price data for SKU '{sku}' ({name})"
                    return ""
                p = price_lookup.lookup(sku, size, color)
                if not p:
                    errors[row_sku] = (
                        f"No matching price for SKU '{sku}' ({name}) "
                        f"size={size!r} color={color!r}"
                    )
                return p

            sku_meta = meta.get(sku, {})
            categories = sku_meta.get("categories", "")
            images     = sku_meta.get("images", "")

            # ── simple ────────────────────────────────────────────────────────
            if not multi_size and not multi_color:
                price = price_for(sku, all_sizes[0], all_colors[0])
                writer.writerow(
                    ["simple", sku, name]
                    + COMMON + ["", "0"]
                    + [price, categories, images]
                    + attr1(all_sizes[0])
                    + attr2(all_colors[0])
                )
                continue

            # ── variable row ──────────────────────────────────────────────────
            writer.writerow(
                ["variable", sku, name]
                + COMMON + ["", "0"]
                + ["", categories, images]
                + attr1(", ".join(all_sizes))
                + attr2(", ".join(c for c in all_colors if c))
            )

            # ── variation rows ────────────────────────────────────────────────
            for size_name, color_name in variations:
                parts = [sku]
                if size_is_variation and size_name:   parts.append(slugify(size_name))
                if color_is_variation and color_name: parts.append(slugify(color_name))
                var_sku = "-".join(parts)

                v_attr1 = (["Talla", size_name, "1", "1"]
                           if size_is_variation else ["", "", "", ""])
                v_attr2 = (["Color", color_name, "1", "1"]
                           if color_is_variation and has_color else ["", "", "", ""])

                price = price_for(var_sku, size_name, color_name)
                writer.writerow(
                    ["variation", var_sku, name]
                    + ["1", "0", "visible", "none", "parent", "1", "0", "0", "0"]
                    + [sku, "0"]
                    + [price, "", ""]
                    + v_attr1 + v_attr2
                )

    # ── 3. Write errors CSV ───────────────────────────────────────────────────
    with open(errors_csv, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "error_reason"])
        for k, v in sorted(errors.items()):
            writer.writerow([k, v])

    # ── 4. Write size & color count CSVs ─────────────────────────────────────
    for path, stats, col in [
        (size_counts_csv, size_stats, "size_name"),
        (color_counts_csv, color_stats, "color_name"),
    ]:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow([col, "count", "candidate_ids"])
            for name, data in sorted(stats.items()):
                writer.writerow([name, data["count"], "|".join(data["candidates"])])

    total_rows = sum(
        (1 + len(e["variations"]))
        if (len(set(s for s, c in e["variations"])) > 1
            or len(set(c for s, c in e["variations"])) > 1)
        else 1
        for e in skus.values()
    )
    print(f"Written {total_rows} rows ({len(skus)} products) to: {out_csv}")
    print(f"Errors:       {len(errors)}  -> {errors_csv.name}")
    print(f"Size counts:  {len(size_stats)} unique sizes  -> {size_counts_csv.name}")
    print(f"Color counts: {len(color_stats)} unique colors -> {color_counts_csv.name}")


if __name__ == "__main__":
    main()