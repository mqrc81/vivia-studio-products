# ROLY Migration Scripts

## Step 1 — Parse product IDs

Prerequisites: `products.csv` (encoded-ID format)

```bash
python parse_products.py products.csv
```

Produces `products_color_map.csv`, `products_sizes.csv`, `products_errors.csv`.

---

## Step 2 — Generate WooCommerce import CSV

Prerequisites: `products.csv`, `TodoPrecios.csv`, `images-and-categories.csv`

```bash
python make_variations.py products.csv output.csv TodoPrecios.csv images-and-categories.csv
```

Produces `output.csv` and `products_output_errors.csv`.
