#!/usr/bin/env python3
"""
parse_products.py — Build product-lookup dictionaries from a variation CSV.

Usage:
    python parse_products.py products.csv

Output (printed to stdout and saved alongside the input file):
    <stem>_sku.csv    : sku_id    -> product_name
    <stem>_sizes.csv  : size_id   -> size_name
    <stem>_colors.csv : color_id  -> color_name
    <stem>_errors.csv : id        -> error_reason
    <stem>_parsed.json: all four dicts combined

ID structure (10-12 characters):
    [AA][SSSS][ZZ?][CC?]
     ^^  ^^^^  ^^^  ^^^
     |   |     |    +-- 2 or 3 digit color ID
     |   |     +------- 2 or 3 digit size ID
     |   +------------- 4 digit SKU
     +----------------- 2 uppercase letters

Ambiguity resolution:
    All rows are placed in an iterative pool.  On every pass, candidate
    splits are filtered using BOTH forward (id->name) and reverse (name->id)
    dictionaries.  A split is eliminated if:
      - its size_id  is already mapped to a different size_name  (forward)
      - its color_id is already mapped to a different color_name (forward)
      - its size_name  is already assigned to a different size_id  (reverse)
      - its color_name is already assigned to a different color_id (reverse)
    Rows with exactly one surviving candidate are registered and removed
    from the pool.  Iteration continues until no further progress is made.
"""

import csv
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Size patterns -- tried in order; first match wins.
# ---------------------------------------------------------------------------
_SIZE_PATTERNS = [
    # T/KID (31/34)  or  T/KID(35/40)
    (
        re.compile(r"^(KID\s*\(\d+/\d+\))\s*(.*)$", re.DOTALL),
        lambda m: (m.group(1).strip(), m.group(2).strip()),
    ),
    # T/1/2  or  T/11/12  (numeric fraction -- must come before plain digit)
    (
        re.compile(r"^(\d+/\d+)(?:\s+(.+))?$", re.DOTALL),
        lambda m: (m.group(1), (m.group(2) or "").strip()),
    ),
    # T/3 MESES  or  T/9 MESES
    (
        re.compile(r"^(\d+)\s+(MESES)(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL),
        lambda m: (f"{m.group(1)} MESES", (m.group(3) or "").strip()),
    ),
    # T/5XL  T/4XL  T/3XL  T/XXL  T/XL  T/XS  T/UNICA  T/NINO  T/S  T/M  T/L
    # T/3XS-2XS  T/2XS-XS  T/XS-S  T/S-M  T/M-L  T/L-XL  T/XL-XXL  (ranges)
    (
        re.compile(r"^(5XL|4XL|3XL|XXL|XL|3XS-2XS|2XS-XS|XS-S|XS|S-M|S|M-L|M|L-XL|L|XL-XXL|UNICA|NI\xd1O)(?:\s+(.+))?$", re.DOTALL),
        lambda m: (m.group(1), (m.group(2) or "").strip()),
    ),
    # T/4  T/52  (plain integer)
    (
        re.compile(r"^(\d+)(?:\s+(.+))?$", re.DOTALL),
        lambda m: (m.group(1), (m.group(2) or "").strip()),
    ),
]


def parse_variation(variation):
    """
    Parse a variation name string.
    Returns (product_name, size_name, color_name) or None.
    """
    matches = list(re.finditer(r"(?:(?<=\s)|^)T/", variation))
    if not matches:
        return None

    best = None
    for m in matches:
        after = variation[m.end():]
        for pattern, extractor in _SIZE_PATTERNS:
            pm = pattern.match(after)
            if pm:
                best = (m, pm, extractor)
                break

    if best is None:
        return None

    t_match, pm, extractor = best
    product_name = variation[: t_match.start()].strip()
    size_name, color_name = extractor(pm)
    return product_name, size_name, color_name


def candidate_splits(id_str):
    """
    Return all structurally valid (letters, sku, size_id, color_id) splits.

    Two remainder formats are supported:

    Regular  ([digits only]):
        size ID  : 2 or 3 digits
        color ID : remaining digits, must be >= 2  (no upper bound)

    Z-prefix ([Z][digits]):
        size ID  : "Z" + 2 or 3 digits  (the digits directly encode the size name)
        color ID : remaining digits, must be >= 2  (no upper bound)
        Z-prefix splits are self-validating -- Z28 must pair with size name "28".
    """
    m = re.match(r"^([A-Z]{2})(\d{4})(Z?\d+)$", id_str)
    if not m:
        return None

    letters, sku, rem = m.group(1), m.group(2), m.group(3)

    candidates = []
    if rem.startswith("Z"):
        # Z-prefix: always Z + exactly 2 digits for size_id
        size_id  = rem[:3]   # "Z" + 2 digits
        color_id = rem[3:]
        if len(color_id) >= 2 and re.match(r"^\d+$", color_id):
            candidates.append((letters, sku, size_id, color_id))
    else:
        # Regular all-digit remainder
        for size_len in (2, 3):
            color_len = len(rem) - size_len
            if color_len >= 2:
                candidates.append((letters, sku, rem[:size_len], rem[size_len:]))

    return candidates if candidates else None


def filter_candidates(
    candidates,
    size_name, color_name,
    size_id_to_name, color_id_to_name,
    size_name_to_id, color_name_to_id,
):
    """
    Eliminate candidates that contradict any established forward or reverse
    mapping.
    """
    valid = []
    for letters, sku, size_id, color_id in candidates:
        # Z-prefix size IDs directly encode the size name (Z28 -> "28").
        # Validate immediately without needing the dictionary.
        if size_id.startswith("Z") and size_id[1:] != size_name:
            continue
        # Forward checks
        if size_id_to_name.get(size_id, size_name)    != size_name:  continue
        if color_id_to_name.get(color_id, color_name) != color_name: continue
        # Reverse checks
        # Skip reverse size check when either side is a Z-prefix ID: Z-prefix
        # and regular numeric IDs are separate namespaces and must not conflict.
        known_size_id = size_name_to_id.get(size_name, size_id)
        if not (size_id.startswith("Z") or known_size_id.startswith("Z")):
            if known_size_id != size_id: continue
        if color_name_to_id.get(color_name, color_id) != color_id:   continue
        valid.append((letters, sku, size_id, color_id))
    return valid


def register(
    id_str, candidate,
    product_name, size_name, color_name,
    sku_to_name,
    size_id_to_name, color_id_to_name,
    size_name_to_id, color_name_to_id,
    errors,
):
    """
    Register a resolved candidate into all forward and reverse dicts.
    Adds to errors on conflict and returns False.
    """
    _, sku, size_id, color_id = candidate
    ok = True

    checks = [
        (sku_to_name,       sku,        product_name, "Product/SKU"),
        (size_id_to_name,   size_id,    size_name,    "Size"),
        (color_id_to_name,  color_id,   color_name,   "Color"),
        (size_name_to_id,   size_name,  size_id,      "Size (reverse)"),
        (color_name_to_id,  color_name, color_id,     "Color (reverse)"),
    ]
    for d, key, val, label in checks:
        if key in d and d[key] != val:
            # Z-prefix and regular numeric size IDs are separate namespaces:
            # size_name "36" may validly map to both "36" and "Z36".
            # Skip the conflict when either the stored or incoming value is Z-prefix.
            if label == "Size (reverse)" and (val.startswith("Z") or d[key].startswith("Z")):
                d[key] = val  # update to whichever was seen last (both are valid)
                continue
            errors[id_str] = (
                f"{label} conflict: '{key}' already mapped to '{d[key]}', "
                f"but this row requires '{val}'"
            )
            ok = False
        else:
            d[key] = val

    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(csv_file):
    sku_to_name      = {}
    size_id_to_name  = {}
    color_id_to_name = {}
    size_name_to_id  = {}   # reverse -- internal only
    color_name_to_id = {}   # reverse -- internal only
    errors           = {}

    # -- 1. Read & pre-parse every row ----------------------------------------
    # All rows enter the iterative pool regardless of candidate count.
    # Single-candidate rows resolve on the very first pass.
    pool = []  # (id_str, product_name, size_name, color_name, [candidates])

    with open(csv_file, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for lineno, row in enumerate(reader, start=1):
            if not row or all(c.strip() == "" for c in row):
                continue
            if len(row) < 2:
                errors[f"<line {lineno}>"] = f"Row has fewer than 2 columns: {row!r}"
                continue

            id_str    = row[0].strip()
            variation = row[1].strip()

            var_result = parse_variation(variation)
            if var_result is None:
                errors[id_str] = (
                    f"Cannot parse variation -- no recognisable 'T/<size>' found: "
                    f"'{variation}'"
                )
                continue

            product_name, size_name, color_name = var_result

            candidates = candidate_splits(id_str)
            if not candidates:
                errors[id_str] = (
                    f"ID format not recognised (expected 2 letters + 8-10 digits): "
                    f"'{id_str}'"
                )
                continue

            # SKU is always unambiguous -- register immediately
            sku = id_str[2:6]
            if sku in sku_to_name and sku_to_name[sku] != product_name:
                errors[id_str] = (
                    f"SKU '{sku}' conflict: already '{sku_to_name[sku]}', "
                    f"this row says '{product_name}'"
                )
                continue
            sku_to_name[sku] = product_name

            pool.append((id_str, product_name, size_name, color_name, candidates))

    # -- 2. Iteratively resolve the pool --------------------------------------
    changed = True
    while changed and pool:
        changed = False
        still_pending = []

        for id_str, product_name, size_name, color_name, candidates in pool:
            valid = filter_candidates(
                candidates, size_name, color_name,
                size_id_to_name, color_id_to_name,
                size_name_to_id, color_name_to_id,
            )

            if len(valid) == 0:
                errors[id_str] = (
                    f"No valid split for '{id_str}' "
                    f"(size='{size_name}', color='{color_name}') -- "
                    f"all candidates contradict established mappings"
                )
                changed = True

            elif len(valid) == 1:
                register(
                    id_str, valid[0],
                    product_name, size_name, color_name,
                    sku_to_name,
                    size_id_to_name, color_id_to_name,
                    size_name_to_id, color_name_to_id,
                    errors,
                )
                changed = True

            else:
                if len(valid) < len(candidates):
                    changed = True  # at least one candidate was pruned
                still_pending.append(
                    (id_str, product_name, size_name, color_name, valid)
                )

        pool = still_pending

    # -- 3. Phase 2: resolve by partial confirmation -------------------------
    # A candidate is kept if at least one of its IDs is already confirmed.
    # The reverse dicts still apply to prune contradictions.
    changed = True
    while changed and pool:
        changed = False
        still_pending = []

        for id_str, product_name, size_name, color_name, candidates in pool:
            # Apply reverse-dict pruning first (same as phase 1)
            valid = filter_candidates(
                candidates, size_name, color_name,
                size_id_to_name, color_id_to_name,
                size_name_to_id, color_name_to_id,
            )

            # Keep only candidates where at least one side is already known
            partial = [
                (letters, sku, size_id, color_id)
                for letters, sku, size_id, color_id in valid
                if size_id_to_name.get(size_id)  == size_name
                or color_id_to_name.get(color_id) == color_name
            ]

            if len(partial) == 0:
                # No confirmed anchor on either side yet — keep for next pass
                still_pending.append(
                    (id_str, product_name, size_name, color_name, valid)
                )

            elif len(partial) == 1:
                register(
                    id_str, partial[0],
                    product_name, size_name, color_name,
                    sku_to_name,
                    size_id_to_name, color_id_to_name,
                    size_name_to_id, color_name_to_id,
                    errors,
                )
                changed = True

            else:
                # Multiple candidates still have a confirmed anchor — keep pruned list
                if len(partial) < len(candidates):
                    changed = True
                still_pending.append(
                    (id_str, product_name, size_name, color_name, partial)
                )

        pool = still_pending

    # -- 4. Truly unresolvable rows -> errors ---------------------------------
    for id_str, product_name, size_name, color_name, candidates in pool:
        options = [(s, c) for _, _, s, c in candidates]
        errors[id_str] = (
            f"Ambiguous split could not be resolved for '{id_str}' -- "
            f"candidate (size_id, color_id) pairs: {options}"
        )

    return sku_to_name, size_id_to_name, color_id_to_name, errors


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _print_dict(title, d):
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  {title}  ({len(d)} entries)")
    print(bar)
    for k, v in sorted(d.items()):
        print(f"  {k!r:20s}  ->  {v}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_products.py <csv_file>")
        sys.exit(1)

    csv_path = sys.argv[1]
    sku_to_name, size_id_to_name, color_id_to_name, errors = main(csv_path)

    _print_dict("SKU -> Product Name",    sku_to_name)
    _print_dict("Size ID -> Size Name",   size_id_to_name)
    _print_dict("Color ID -> Color Name", color_id_to_name)
    _print_dict("ERRORS",                 errors)

    stem   = Path(csv_path).stem
    parent = Path(csv_path).parent

    # JSON
    out_json = parent / (stem + "_parsed.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "sku_to_name":      sku_to_name,
                "size_id_to_name":  size_id_to_name,
                "color_id_to_name": color_id_to_name,
                "errors":           errors,
            },
            fh, ensure_ascii=False, indent=2,
        )
    print(f"\n+ {stem}_parsed.json")

    # CSVs
    csv_outputs = [
        (sku_to_name,      "sku_id",   "product_name", stem + "_sku.csv"),
        (size_id_to_name,  "size_id",  "size_name",    stem + "_sizes.csv"),
        (color_id_to_name, "color_id", "color_name",   stem + "_colors.csv"),
        (errors,           "id",       "error_reason", stem + "_errors.csv"),
    ]
    for d, col_key, col_val, filename in csv_outputs:
        out_csv = parent / filename
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([col_key, col_val])
            for k, v in sorted(d.items()):
                writer.writerow([k, v])
        print(f"+ {filename:40s} ({len(d)} rows)")