#!/usr/bin/env python3
"""
migrate_category.py — Convert a product-category CSV into a WooCommerce-ready
import CSV, using images-and-categories.csv for product images and category names.

Supported input formats (auto-detected by columns present):
    - eating.csv     : SKU, NOMBRE, DESCRIPCION, DESCRIPCION LARGA, ATRIBUTO, PRECIO
    - oficina.csv    : SKU, NOMBRE, DESCRIPCION, DESCRIPCION LARGA, COLOR, PRECIO, ...
    - personal-care  : sku, nombre, descripcion, descripcion2, color, talla, precio

Usage:
    python migrate_category.py <category.csv> <images-and-categories.csv> <colors.csv> [output.csv]

Category names are read directly from the Categorias column (index 27) of
images-and-categories.csv, so no --category flag is needed. WooCommerce escapes
commas inside category names as backslash-comma in exports; this is unescaped
on load.

Color to image-URL-code mapping is loaded from a colors CSV produced by
parse_products.py (columns: color_name, count, candidate_ids). Colors absent
from the CSV leave the variation image blank rather than guessing.

Output follows the same column structure as tecnologias-output.csv.

Side output:
    <stem>_errors.csv - rows with missing images, prices, or parsing issues.
"""

import csv
import re
import sys
from pathlib import Path

_URL_CODE_RE = re.compile(r'/(\d+)_(\w+)_\d+_\d+\.jpg')


def _normalise_color(c):
    return c.strip().upper()


def _parse_colors(raw):
    if not raw or not raw.strip():
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def _parse_sizes(raw):
    if not raw or not raw.strip():
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _format_price(raw):
    """Normalise price to Spanish decimal format (comma separator)."""
    if not raw or not raw.strip():
        return ""
    raw = raw.strip()
    if "." in raw and "," not in raw:
        try:
            return "{:.2f}".format(float(raw)).replace(".", ",")
        except ValueError:
            return raw
    return raw


def _url_code(url):
    m = _URL_CODE_RE.search(url)
    return m.group(2) if m else None


def _load_color_map(colors_csv):
    """
    Load a parse_products.py color CSV into {color_name: [code, ...]}.

    Accepts two formats (auto-detected by header):
      - <stem>_color_map.csv  (new): columns color_name, color_id
      - <stem>_color_counts.csv (old): columns color_name, count, candidate_ids
        where candidate_ids may be pipe-separated

    Colors are stored uppercase for case-insensitive lookup.
    """
    result = {}
    with open(colors_csv, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [h.strip().lower() for h in next(reader)]
        if "color_id" in header:
            name_col = header.index("color_name")
            id_col   = header.index("color_id")
            for row in reader:
                if len(row) <= max(name_col, id_col) or not row[name_col].strip():
                    continue
                color = row[name_col].strip().upper()
                code  = row[id_col].strip()
                if code:
                    result[color] = [code]
        else:
            for row in reader:
                if len(row) < 3 or not row[0].strip():
                    continue
                color = row[0].strip().upper()
                codes = [c.strip() for c in row[2].split("|") if c.strip()]
                if codes:
                    result[color] = codes
    return result


def _images_for_color(all_urls, color_norm, all_colors_norm, color_map):
    """
    Return image URLs for a specific color variation, or [] if the mapping is
    unknown or no matching image exists in this product's URL list.

    When two colors share a candidate code, the code goes to whichever color
    lists it first; the other must use its next candidate. If no uncontested
    code exists, return [] rather than guessing.
    """
    codes = color_map.get(color_norm)
    if not codes:
        return []

    claimed_by_others = set()
    for other in all_colors_norm:
        if other == color_norm:
            continue
        for c in color_map.get(other, []):
            claimed_by_others.add(c)

    chosen = next((c for c in codes if c not in claimed_by_others), None)
    if chosen is None:
        return []

    return [url for url in all_urls if _url_code(url) == chosen]


def _load_images_and_categories(images_csv):
    """
    Load images-and-categories.csv.

    Returns:
        images     : {sku: [url, ...]}
        categories : {sku: category_string}

    - Nombre column (index 4): 'STAMINA 1063' -> sku '1063'
    - Imagenes column (index 30): comma-separated URLs
    - Categorias column (index 27): WooCommerce category; backslash-comma
      escapes (e.g. 'Ocio\\, Deportes y Cuidado Personal') are unescaped.

    Duplicate SKU rows are deduplicated (keep first occurrence).
    """
    images = {}
    categories = {}

    with open(images_csv, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        next(reader)
        for row in reader:
            if not row:
                continue
            nombre = row[4].strip() if len(row) > 4 else ""
            sku = nombre.replace("STAMINA ", "").strip()
            if not sku or sku in images:
                continue
            imgs_raw = row[30].strip() if len(row) > 30 else ""
            images[sku] = [u.strip() for u in imgs_raw.split(",") if u.strip()]
            cat_raw = row[27].strip() if len(row) > 27 else ""
            categories[sku] = cat_raw.replace("\\,", ",")

    return images, categories


def _detect_columns(header):
    h = [c.strip().lower() for c in header]

    def find(*candidates):
        for name in candidates:
            try:
                return h.index(name.lower())
            except ValueError:
                pass
        return None

    cols = {
        "sku":         find("sku"),
        "nombre":      find("nombre"),
        "descripcion": find("descripcion"),
        "desc_larga":  find("descripcion larga", "descripcion2"),
        "color":       find("color", "atributo"),
        "talla":       find("talla"),
        "precio":      find("precio"),
    }
    missing = [k for k, v in cols.items() if v is None and k in ("sku", "nombre", "precio")]
    if missing:
        raise ValueError("Required columns not found in header: {}".format(missing))
    return cols


HEADER = [
    "ID", "Tipo", "SKU", "GTIN, UPC, EAN o ISBN", "Nombre",
    "Publicado", "\u00bfEst\u00e1 destacado?", "Visibilidad en el cat\u00e1logo",
    "Descripci\u00f3n corta", "Descripci\u00f3n",
    "D\u00eda en que empieza el precio rebajado", "D\u00eda en que termina el precio rebajado",
    "Estado del impuesto", "Clase de impuesto",
    "\u00bfExistencias?", "Inventario", "Cantidad de bajo inventario",
    "\u00bfPermitir reservas de productos agotados?", "\u00bfVendido individualmente?",
    "Peso (kg)", "Longitud (cm)", "Anchura (cm)", "Altura (cm)",
    "\u00bfPermitir valoraciones de clientes?",
    "Nota de compra", "Precio rebajado", "Precio normal",
    "Categor\u00edas", "Etiquetas", "Clase de env\u00edo",
    "Im\u00e1genes",
    "L\u00edmite de descargas", "D\u00edas de caducidad de la descarga",
    "Superior", "Productos agrupados", "Ventas dirigidas", "Ventas cruzadas",
    "URL externa", "Texto del bot\u00f3n", "Posici\u00f3n", "Marcas",
    "Nombre del atributo 1", "Valor(es) del atributo 1",
    "Atributo visible 1", "Atributo global 1",
    "Nombre del atributo 2", "Valor(es) del atributo 2",
    "Atributo visible 2", "Atributo global 2",
]

_I = {h: i for i, h in enumerate(HEADER)}


def _base_row():
    return [""] * len(HEADER)


def _fill_common(row, sku, nombre, desc_corta, desc_larga,
                 precio, categoria, images, tipo, superior=""):
    row[_I["ID"]]            = ""
    row[_I["Tipo"]]          = tipo
    row[_I["SKU"]]           = sku
    row[_I["Nombre"]]        = nombre
    row[_I["Publicado"]]     = "1"
    row[_I["\u00bfEst\u00e1 destacado?"]]           = "0"
    row[_I["Visibilidad en el cat\u00e1logo"]]       = "visible"
    row[_I["Descripci\u00f3n corta"]]               = desc_corta
    row[_I["Descripci\u00f3n"]]                     = desc_larga
    row[_I["Estado del impuesto"]]                  = "none"
    row[_I["\u00bfExistencias?"]]                   = "1"
    row[_I["\u00bfPermitir reservas de productos agotados?"]] = "0"
    row[_I["\u00bfVendido individualmente?"]]        = "0"
    row[_I["\u00bfPermitir valoraciones de clientes?"]] = "0"
    row[_I["Precio normal"]]                        = precio
    row[_I["Categor\u00edas"]]                      = categoria
    row[_I["Im\u00e1genes"]]                        = images
    row[_I["Posici\u00f3n"]]                        = "0"
    row[_I["Superior"]]                             = superior


def _set_attr(row, slot, name, value, visible="1", global_="1"):
    assert slot in (1, 2)
    row[_I["Nombre del atributo {}".format(slot)]]     = name
    row[_I["Valor(es) del atributo {}".format(slot)]]  = value
    row[_I["Atributo visible {}".format(slot)]]        = visible
    row[_I["Atributo global {}".format(slot)]]         = global_


def main():
    args = sys.argv[1:]
    if len(args) < 3 or args[0] in ("-h", "--help"):
        print("Usage: python migrate_category.py <category.csv> "
              "<images-and-categories.csv> <colors.csv> [output.csv]")
        sys.exit(0 if not args else 1)

    products_csv = Path(args[0])
    images_csv   = Path(args[1])
    colors_csv   = Path(args[2])
    out_path     = Path(args[3]) if len(args) >= 4 else \
                   products_csv.parent / (products_csv.stem + "_woo_output.csv")
    errors_path  = out_path.parent / (out_path.stem + "_errors.csv")

    color_map = _load_color_map(colors_csv)
    print("Loaded color map: {} colors from: {}".format(len(color_map), colors_csv.name))

    images_map, categories_map = _load_images_and_categories(images_csv)
    print("Loaded images/categories for {} SKUs from: {}".format(
        len(images_map), images_csv.name))

    with open(products_csv, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        raw_header = next(reader)
        cols = _detect_columns(raw_header)
        raw_rows = list(reader)

    errors = []
    skus_not_in_map = set()   # color unknown
    skus_code_missing = set() # color known, image absent
    output_rows = []

    for lineno, row in enumerate(raw_rows, start=2):
        if not row or all(c.strip() == "" for c in row):
            continue

        def get(field):
            idx = cols.get(field)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        sku        = get("sku")
        nombre     = get("nombre")
        desc_corta = get("descripcion")
        desc_larga = get("desc_larga")
        color_raw  = get("color")
        talla_raw  = get("talla")
        precio_raw = get("precio")

        if not sku or not nombre:
            errors.append((sku or "<line {}>".format(lineno), nombre, "Missing SKU or Nombre"))
            continue

        precio      = _format_price(precio_raw)
        colors      = _parse_colors(color_raw)
        sizes       = _parse_sizes(talla_raw)
        colors_norm = [_normalise_color(c) for c in colors]

        all_urls  = images_map.get(sku, [])
        all_imgs  = ", ".join(all_urls)
        categoria = categories_map.get(sku, "")

        has_color   = bool(colors)
        has_size    = bool(sizes)
        multi_color = len(colors) > 1
        multi_size  = len(sizes) > 1

        if not all_urls:
            errors.append((sku, nombre, "No images found"))
        if not precio:
            errors.append((sku, nombre, "Missing price"))
        if not categoria:
            errors.append((sku, nombre, "No category found in images-and-categories.csv"))

        # ── simple ────────────────────────────────────────────────────────────
        if not multi_color and not multi_size:
            r = _base_row()
            _fill_common(r, sku, nombre, desc_corta, desc_larga,
                         precio, categoria, all_imgs, "simple")
            if has_color:
                _set_attr(r, 1, "Color", colors_norm[0])
            if has_size:
                _set_attr(r, 2 if has_color else 1, "Talla", sizes[0].upper())
            output_rows.append(r)
            continue

        # ── variable row ──────────────────────────────────────────────────────
        var_row = _base_row()
        _fill_common(var_row, sku, nombre, desc_corta, desc_larga,
                     "", categoria, all_imgs, "variable")
        attr_slot = 1
        if has_color:
            _set_attr(var_row, attr_slot, "Color", ", ".join(colors_norm))
            attr_slot += 1
        if has_size:
            _set_attr(var_row, attr_slot, "Talla", ", ".join(s.upper() for s in sizes))
        output_rows.append(var_row)

        # ── variation rows ────────────────────────────────────────────────────
        iter_sizes  = sizes if sizes else [""]
        iter_colors = list(zip(colors, colors_norm)) if colors else [("", "")]

        var_position = 1
        for size in iter_sizes:
            for color, color_norm in iter_colors:
                vr = _base_row()
                _fill_common(vr, "", nombre, "", "",
                             precio, "", "", "variation", superior=sku)
                vr[_I["Posición"]] = str(var_position)
                vr[_I["Clase de impuesto"]] = "parent"
                var_position += 1

                if color_norm:
                    var_urls = _images_for_color(all_urls, color_norm, colors_norm, color_map)
                    if var_urls:
                        vr[_I["Im\u00e1genes"]] = var_urls[0]
                    else:
                        if color_norm in color_map:
                            skus_code_missing.add(sku)
                        else:
                            skus_not_in_map.add(sku)

                attr_slot = 1
                if has_color and color:
                    _set_attr(vr, attr_slot, "Color", color_norm, visible="", global_="1")
                    attr_slot += 1
                if has_size and size:
                    _set_attr(vr, attr_slot, "Talla", size.upper(), visible="", global_="1")

                output_rows.append(vr)

    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(output_rows)

    # skus_code_missing excludes any SKU already in skus_not_in_map
    skus_code_missing -= skus_not_in_map

    with open(errors_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        # Section 1: colors not in color map (variation image blank)
        writer.writerow(["[1] SKUs with colors not in color map (variation image blank)"])
        writer.writerow(["sku"])
        for sku in sorted(skus_not_in_map):
            writer.writerow([sku])
        writer.writerow([])
        # Section 2: color known but image absent for that code
        writer.writerow(["[2] SKUs where color code known but not present in product images"])
        writer.writerow(["sku"])
        for sku in sorted(skus_code_missing):
            writer.writerow([sku])
        writer.writerow([])
        # Remaining errors
        writer.writerow(["[3] Other errors"])
        writer.writerow(["sku", "nombre", "reason"])
        writer.writerows(errors)

    n_products = sum(1 for r in output_rows
                     if r[_I["Tipo"]] in ("simple", "variable"))
    print("Written {} rows ({} products) to: {}".format(
        len(output_rows), n_products, out_path))
    print("Errors: {} | No color map: {} SKUs | Code missing: {} SKUs -> {}".format(
        len(errors), len(skus_not_in_map), len(skus_code_missing), errors_path.name))


if __name__ == "__main__":
    main()
