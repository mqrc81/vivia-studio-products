# WooCommerce Migration Scripts

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

Prerequisites: `products-input.csv`, `images-and-categories.csv`, `products_color_map.csv`

```bash
python migrate_category.py products-input.csv images-and-categories.csv products_color_map.csv
```

Produces `products-input_woo_output.csv` and `products-input_woo_output_errors.csv`.
