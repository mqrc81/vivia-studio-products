# STAMINA Migration Scripts

## Step 1 — Build the color map

Prerequisites: `products.csv` (encoded-ID format)

```bash
python parse_products.py products.csv [extra_source.csv ...]
```

Produces `products_color_map.csv`.

---

## Step 2 — Build the WooCommerce variations CSV

Prerequisites: `products.csv`, `TodoPrecios.csv`, `images-and-categories.csv`

```bash
python make_variations.py products.csv output.csv TodoPrecios.csv images-and-categories.csv
```

---

## Step 3 — Migrate a product category

Prerequisites: `input.csv`, `images-and-categories.csv`, `products_color_map.csv`

```bash
python migrate_category.py input.csv images-and-categories.csv products_color_map.csv
```

Produces `input_woo_output.csv` and `input_woo_output_errors.csv`.
