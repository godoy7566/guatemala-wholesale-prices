# Agricultural wholesale price tracker (Guatemala)

A Python data pipeline and Streamlit dashboard for weekly agricultural wholesale prices published in Guatemala. The pipeline downloads weekly PDF reports, extracts dated price tables, standardizes product names, and creates two Parquet datasets used by the dashboard.

**Source:** [MAGA weekly price reports](https://precios.maga.gob.gt/informes/semanales/). This is an independent project, not an official MAGA product. Prices and measurement units are preserved as published, without currency or unit conversion.

## Workflow

1. Find PDF report links on the MAGA weekly reports site with Selenium and Chrome.
2. Download new/unprocessed PDFs into `files/` (excluded from Git).
3. Extract in-scope price tables with `pdfplumber`, retain available dates within the reporting period, and normalize product names in Spanish.
4. Combine historical and newly extracted prices, deduplicate by `(product, date, measurement)`, and rebuild `products.parquet` from the full combined history.
5. Select exactly one original `product` per `normalized_product` as `is_main_product`. Explicit manual choices take precedence; ties otherwise resolve deterministically.
6. Explore prices in the Streamlit dashboard, using normalized or original products and unit-compatible comparisons.

## Structure

- `wholesale_prices.py` — data extraction, normalization and incremental updates
- `dashboard.py` — Streamlit charts and filters
- `data/prices.parquet` — curated price observations (included to try the dashboard)
- `data/products.parquet` — curated product classification and representative flags
- `examples/` — optional example input PDFs
- `tests/` — automated pipeline tests
- `files/` — locally downloaded source reports, not tracked in Git

## Setup

Python 3.10+ and a compatible Chrome browser are recommended. Use a virtual environment or Conda environment.

```bash
python -m pip install -r requirements.txt
python -m streamlit run dashboard.py
```

The committed example datasets let the dashboard run without downloading reports. To update them:

```bash
python wholesale_prices.py
```

The pipeline processes reports not recorded in
`data/processed_reports.json`. Keep this local manifest alongside
your local datasets.

Deleting the manifest triggers reprocessing of all available reports
from 2025 onward. Existing historical observations are preserved,
and observations without verified source information retain null
provenance.

**Publishing updates:** The data files are intentionally tracked, but `files/` and the local processed-report manifest are ignored.

## Data model

`prices.parquet`: `product`, `measurement`, `date`, `price`, plus the following provenance fields:

| Field | Description |
| --- | --- |
| `source_report` | Original report name |
| `source_url` | PDF URL discovered on the MAGA website |
| `source_page` | Actual one-based PDF page number |
| `extracted_at` | UTC extraction timestamp |

Product names remain normalized in Spanish. Legacy observations without a verified source keep null provenance rather than guessed metadata. The dashboard's observation explorer and CSV export include these fields when available.

## Data quality and report acceptance

The pipeline extracts available, in-scope observations rather than requiring every PDF page or every expected reporting date to contain prices. Completeness means successfully extracting the eligible data that is actually present in the source.

### Page selection and date handling

The scope is basic grains, vegetables and fruits. The reporting period defines which dates are eligible for extraction, not which dates must appear in every table.

| Scenario | Action |
| --- | --- |
| Page does not belong to a recognized in-scope product category | Skip the page intentionally. |
| Some expected dates are absent (e.g. holidays) | Accept the available in-range dates; do not impute missing dates. |
| A date column falls outside the reporting period | Log a warning and drop that column before reshaping the table. |
| No date columns map to the reporting period | Log a warning and skip the page; continue processing the rest of the PDF. |
| A retained table cannot be extracted or has malformed headers, missing product/unit columns, or duplicate retained column names | Reject the report. |
| All pages are skipped or no valid observations are extracted | Do not mark the report as processed. |

For example, if the reporting period covers the 10th through the 14th and the PDF contains columns `10`, `11`, `12`, `15`, `14`, the pipeline retains `10`, `11`, `12`, `14` and discards `15`. The absence of the 13th is not an error. If a page contains only dates outside the reporting period, it is skipped without rejecting the other pages.

### Observation validation and persistence

- Empty cells and the source missing-value codes `SO`, `SD` and `ND` are treated as missing observations.
- Nonpositive prices are logged with their available observation details and excluded before deduplication, so they cannot overwrite a valid historical price. Other invalid numeric values cause validation or report extraction to fail.
- Conflicting prices for the same observation key within a report cause the report to be rejected.
- A genuine extraction or validation failure on an eligible table rejects the entire PDF: no partial rows are accepted, and the report remains eligible for retry. Intentional page and date exclusions described above are not failures.
- Successful reports must produce at least one valid price observation and are recorded in the manifest only after persistence succeeds.
- Accepted observations retain their source report, URL, page number and UTC extraction timestamp when available.

`products.parquet`: one row per original `product`, including `normalized_product`, `type`, `grade`, `size`, `variant`, `is_main_product`.

## Dashboard guide

The dashboard is designed for historical wholesale price monitoring, comparison and data exploration. It is organized into three tabs: **Overview**, **Price comparison** and **Data explorer**.

### Analysis level and shared filters

- **Normalized product:** use one designated representative original product per normalized category. This avoids mixing varieties, grades and sizes into a single price series. The representative is not a weighted average or a market-wide price for the category. Categories without a designated main product are excluded from normalized analysis.
- **Original product:** analyze individual products as named in the source, including their varieties, grades and sizes. Use this mode when those distinctions matter.

The sidebar provides filters for product type, normalized product, grade, size, measurement unit and date range. In normalized mode, grade and size filters apply to the representative products, not to all variants. Prices retain their published units; no unit or currency conversion is performed.

### 1. Overview

**Intended use:** monitor historical price behavior and identify products with notable recent price movements.

- **Summary indicators:** number of distinct products at the selected analysis level, non-null price observations, measurement units and latest observation date within the filters.
- **Price history:** select a product to inspect its observed price series over time. Different measurement units are plotted separately, and the representative original product is identified in normalized mode.
- **Largest 7-day price changes:** select a week-ending date to rank up to 12 product/unit combinations by absolute percentage change, including both increases and decreases. Hover over a bar to inspect the original product, unit, actual observation dates, prices and elapsed days.

The seven-day calculation compares the last available price on or before the selected date with the last available price on or before the date seven days earlier. Each observation must be no more than seven days old relative to its own boundary, and the two observations must be distinct. The percentage change is calculated as `(latest_price / previous_price - 1) × 100`.

**Limitations:** the actual observations may be fewer or more than seven days apart; this is an as-of comparison, not necessarily an exact seven-day return. Products without two qualifying observations are excluded. The week-ending selector uses the available history under the non-date sidebar filters and may look outside the sidebar date range. Price histories show recorded observations, not imputed daily prices. The latest date indicator is the latest observation in the filtered dataset, not a guarantee that every product has a price on that date.

### 2. Price comparison

**Intended use:** compare historical price levels and movements across products with the same published measurement unit, or compare individual variants in original-product mode.

Select a reference product and a measurement unit (if more than one is available), then select up to seven additional products with that same unit. The chart displays their price histories within the selected date range. A summary table shows the latest and previous available observation, their dates and prices, and the percentage change for each selected product. In normalized mode, the representative-products panel identifies the original series behind each category.

**Limitations:** matching measurement units is required, but does not imply equivalent quality, variety or commercial substitutability. The summary table compares each product's last two available observations independently; the dates and elapsed intervals may differ between products. These changes are not necessarily weekly changes, nor are the latest prices necessarily from the same date. A product with only one observation has no previous-price comparison. No unit conversion, interpolation or price forecasting is performed.

### 3. Data explorer

**Intended use:** inspect individual observations, investigate product classifications and extract data for external analysis or quality checks.

- Browse observation-level records, sorted by date, with product classifications and source provenance when available.
- Download the displayed dataset as CSV for further analysis in Python, Excel or other tools.
- Expand the product master to review all original products, their normalized names, types, grades, sizes, variants and representative flags.
- In normalized mode, enable **Include non-main product variants** to inspect additional original products belonging to the selected normalized categories. Original-product mode already includes variants.

**Limitations:** enabling non-main variants expands the selected normalized categories to all their variants, retaining the date and measurement-unit filters but not reapplying grade or size filters to those variants. The eligible categories are determined by the main-product filters. The product master is the full catalog, not the filtered observation dataset. The CSV contains the explorer's selected observations, not necessarily the same rows used by the charts. Historical observations may lack provenance until their source reports are successfully reprocessed. The explorer contains curated observations, not the original PDF tables or rejected rows.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## Limitations

- The scraper depends on the source site's HTML structure and PDF formatting and may require maintenance.
- Missing price observations are not interpolated, and different measurement units are not converted.
- Corrections to previously published reports are not automatically detected once a filename is marked processed; remove that filename from the manifest to reprocess it.
- Page and category detection is heuristic. Unrecognized categories are intentionally skipped; unfamiliar layouts may require manual review if relevant tables are not detected or cannot be parsed. A genuine extraction failure or a report with no valid observations prevents it from being marked processed.
- Two Parquet files are written using temporary files but replacement is not an atomic transaction across both files. Back up the datasets before large ETL runs.

## License and attribution

The original project code is licensed under the [MIT License](LICENSE).

The underlying price data originates from publicly available MAGA weekly
reports. Source materials and derived datasets remain subject to their
applicable rights and usage conditions.

This is an independent project and is not affiliated with, endorsed by,
or maintained by MAGA.
