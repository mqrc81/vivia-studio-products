# WooCommerce Migration Scripts

## Files required in the same directory

```
load_prices.py
make_variations.py
migrate_category.py
parse_products.py
products.csv                  # encoded-ID source (BA9092... format)
images-and-categories.csv     # WooCommerce export with images and categories
TodoPrecios.csv               # price table
eating.csv / oficina.csv / personal-care.csv   # category files to migrate
```

---

## Step 1 — Build the color map

Run once. Add extra encoded-ID source files as additional arguments if available.

```bash
python parse_products.py products.csv
```

Produces `products_color_map.csv` (used in Step 3).

---

## Step 2 — Build the WooCommerce variations CSV (existing pipeline)

```bash
python make_variations.py products.csv output.csv TodoPrecios.csv images-and-categories.csv
```

---

## Step 3 — Migrate a product category

Run once per category file.

```bash
python migrate_category.py eating.csv images-and-categories.csv products_color_map.csv eating_output.csv
python migrate_category.py oficina.csv images-and-categories.csv products_color_map.csv oficina_output.csv
python migrate_category.py personal-care.csv images-and-categories.csv products_color_map.csv personal-care_output.csv
```

Each run produces:
- `<output>.csv` — WooCommerce import file
- `<output>_errors.csv` — missing images, prices, and color map gaps
