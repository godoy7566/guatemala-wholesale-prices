from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import logging
import re

import pandas as pd
import pdfplumber
import requests
from tqdm import tqdm


LOGGER = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
FILES_DIR = BASE_DIR / "files"
DATA_DIR = BASE_DIR / "data"
REPORTS_URL = "https://precios.maga.gob.gt/informes/semanales/"
PROCESSED_REPORTS_FILE = DATA_DIR / "processed_reports.json"
SOURCE_COLUMNS = ["source_report", "source_url", "source_page", "extracted_at"]
PRICE_COLUMNS = ["product", "measurement", "date", "price", "product_type", *SOURCE_COLUMNS]
MIN_REPORT_YEAR = 2025


def match_date_text(text):
    """
    Search for a date range in a string.
    Args:
        text: Text containing a date range.
    Returns:
        Regex match object containing the date range and its format.
    """
    date_patterns = {
        'intramonth': r'\d+ (?:al|-) \d+ de \w+ (?:de )?\d+',
        'intermonth': r'\d+ de \w+ al \d+ de \w+ (?:de )?\d+',
        'interanual': r'\d+ de \w+ \d+ al \d+ de \w+ \d+'
    }

    # Combine patterns using named groups to identify the matched format
    combined_pattern = '|'.join(
        f'(?P<{date_type}>{regex})'
        for date_type, regex in date_patterns.items()
    )

    date_match = re.search(combined_pattern, text)

    return date_match


def format_matched_date(date_match):
    """
    Parse a matched date range and generate all dates within it.
    Args:
        date_match: Regex match object containing the date range and its format.
    Returns:
        List of daily timestamps covering the matched date range.
    """
    date_text = date_match.group()
    date_type = date_match.lastgroup
    date_parts = date_text.split()

    # Remove connecting words and separators
    separators = ['al', '-', 'de']
    date_parts = [part for part in date_parts if part not in separators]

    # Map spanish month names to their numerical equivalents
    month_mapper = {
        'enero': 1,
        'febrero': 2,
        'marzo': 3,
        'abril': 4,
        'mayo': 5,
        'junio': 6,
        'julio': 7,
        'agosto': 8,
        'septiembre': 9,
        'octubre': 10,
        'noviembre': 11,
        'diciembre': 12
    }

    # Parse date ranges within the same month
    if date_type == 'intramonth':
        start_day = int(date_parts[0])
        end_day = int(date_parts[1])
        month = int(month_mapper[date_parts[2].lower()])
        year = int(date_parts[3])

        start_date = date(year, month, start_day)
        end_date = date(year, month, end_day)

    # Parse date ranges spanning different months within the same year
    if date_type == 'intermonth':
        start_day = int(date_parts[0])
        start_month = int(month_mapper[date_parts[1].lower()])
        end_day = int(date_parts[2])
        end_month = int(month_mapper[date_parts[3].lower()])
        year = int(date_parts[4])

        start_date = date(year, start_month, start_day)
        end_date = date(year, end_month, end_day)

    # Parse date ranges spanning different years
    if date_type == 'interanual':
            start_day = int(date_parts[0])
            start_month = int(month_mapper[date_parts[1].lower()])
            start_year = int(date_parts[2])
            end_day = int(date_parts[3])
            end_month = int(month_mapper[date_parts[4].lower()])
            end_year = int(date_parts[5])
    
            start_date = date(start_year, start_month, start_day)
            end_date = date(end_year, end_month, end_day)

    # Generate a daily sequence between the start and end dates
    date_range = list(pd.date_range(start=start_date, end=end_date, freq='D'))

    return date_range


def find_max_date(text):
    """
    Identify the latest date in a date range.

    Args:
        text: Text containing a date range.

    Returns:
        Latest timestamp in the matched date range.
    """
    date_match = match_date_text(text)
    date_range = format_matched_date(date_match)

    return sorted(date_range)[-1]



category_mapper = {
    'granos básicos': 'basic_grains', 
    'vegetales': 'vegetables', 
    'frutas': 'fruits'
}


product_category_mapper = {
    'arroz': 'basic_grains',
    'sorgo': 'basic_grains',
    'apio': 'vegetables',
    'cebolla': 'vegetables',
    'pepino': 'vegetables',
    'zanahora': 'vegetables',
    'aguacate': 'fruits',
    'mango': 'fruits',
    'uva': 'fruits',
    'zapote': 'fruits'
}



def get_grade(product_name: str):
    """
    Classify a product according to its quality grade.
    Args:
        product_name: Product description.
    Returns:
        Product grade ('primera' or 'segunda'), if identified.
    """
    if 'de primera' in product_name or 'importad' in product_name:
        return 'primera'
    if 'de segunda' in product_name or 'nacional' in product_name:
        return 'segunda'


def get_size(product_name: str):
    """
    Classify a product according to its size.
    Args:
        product_name: Product description.
    Returns:
        Product size ('big', 'medium', or 'small'), if identified.
    """
    if 'grande' in product_name:
        return 'big'
    if 'median' in product_name:
        return 'medium'
    if 'pequeñ' in product_name:
        return 'small'



product_mapper = {
    'arroz': 'rice',
    'frijol': 'beans',
    'maiz': 'corn',
    'sorgo': 'sorghum',
    'apio': 'celery',
    'ajo': 'garlic',
    'arveja': 'peas',
    'brocoli': 'broccoli',
    'camote': 'sweet potato',
    'cebolla': 'onion',
    'chile': 'pepper',
    'cilantro': 'cilantro',
    'coliflor': 'cauliflower',
    'ejote': 'green bean',
    'guicoy': 'squash',
    'guisquil': 'chayote',
    'hierba buena': 'spearmint',
    'lechuga': 'lettuce',
    'loroco': 'loroco',
    'malanga': 'taro',
    'miltomate': 'tomatillo',
    'papa': 'potato',
    'pepino': 'cucumber',
    'perulero': 'white chayote',
    'rabano': 'radish',
    'remolacha': 'beetroot',
    'repollo': 'cabbage',
    'tomate': 'tomato',
    'yuca': 'cassava',
    'zanahoria': 'carrot',
    'aguacate': 'avocado',
    'banano': 'banana',
    'coco': 'coconut',
    'fresa': 'strawberry',
    'limon': 'lime',
    'mandarina': 'mandarin',
    'melon': 'melon',
    'mora': 'blackberry',
    'naranja': 'orange',
    'papaya': 'papaya',
    'piña': 'pineapple',
    'platano': 'plantain',
    'sandia': 'watermelon',
    'uva': 'grape',
    'zapote': 'sapote',
    'rambutan': 'rambutan',
    'jocote': 'spanish plum',
    'durazno': 'peach',
    'pitahaya': 'dragon fruit',
    'mango': 'mango',
    'manzana': 'apple'
}



def normalize_product(product_name):
    """
    Match a product description to its standardized product name.
    Args:
        product_name: Product description.
    Returns:
        Matched product name from the product mapper, if identified.
    """
    # Search for product names while preventing partial word matches
    for base_product in product_mapper:
        if re.search(rf'\b{re.escape(base_product)}\b', product_name.lower()):
            return base_product



def get_variant(product_name, normalized_product):
    """
    Extract the product variant from its description.

    Args:
        product_name: Original product description.
        normalized_product: Normalized product name.

    Returns:
        Remaining product variant, or None if no variant is identified.
    """
    product_name = product_name.lower()

    # Define product names, grades, and sizes to exclude
    attributes_to_remove = [normalized_product, 'de primera', 'de segunda', 'nacional', 'importado', 'importada', 'grande', 'mediana', 'mediano', 'pequeño', 'pequeña']

    # Remove standardized attributes from the product description
    for attribute in attributes_to_remove:
        if isinstance(attribute, str):
            product_name = product_name.replace(attribute, '')

    # Remove commas, normalize whitespace, and trim the result
    product_name = re.sub(r'\s+', ' ', product_name.replace(',', '')).strip()

    return product_name if product_name else None



def select_main_product(candidates):
    """
    Select the representative product within a normalized product group.
    Args:
        candidates: DataFrame containing product descriptions and observation counts.
    Returns:
        Selected product name, or None if the selection remains ambiguous.
    """
    # Prioritize products with the highest number of observations
    candidates = candidates[candidates['n_observations'].eq(candidates['n_observations'].max())]

    if candidates['product'].nunique() == 1:
        return candidates['product'].iloc[0]

    # Prefer first-grade products when multiple candidates remain
    first_grade_candidates = candidates[candidates['grade'].eq('primera')]

    if not first_grade_candidates.empty:
        candidates = first_grade_candidates

    if candidates['product'].nunique() == 1:
        return candidates['product'].iloc[0]

    # Prefer larger products if the grade criterion does not resolve the tie
    large_candidates = candidates[candidates['size'].eq('big')]

    if not large_candidates.empty:
        candidates = large_candidates

    if candidates['product'].nunique() == 1:
        return candidates['product'].iloc[0]

    # Stable final tie-break: every normalized category gets one representative.
    return sorted(candidates["product"].dropna().unique(), key=str.casefold)[0]



def get_dates_from_pages(pages):
    """
    Search PDF pages for a date range and extract its individual dates.
    Args:
        pages: Collection of PDF pages to search.
    Returns:
        List of daily timestamps covering the reporting period.
    """
    date_match = None
    page_index = 0

    # Search consecutive pages until a valid date range is found
    while date_match is None and page_index < len(pages):
        page_text = (pages[page_index].extract_text() or '').replace('\n', ' ').lower()
        date_match = match_date_text(page_text)
        page_index += 1

    if date_match is None:
        raise ValueError('No reporting date range found in PDF')

    # Convert the matched date range into individual dates
    date_range = format_matched_date(date_match)

    return date_range



def process_tables(pages, date_range, report_name="<unknown report>", source_url=None):
    """
    Process PDF tables into a standardized dataset of daily product prices.
    Args:
        pages: Collection of PDF pages containing price tables.
        date_range: List of dates corresponding to the reporting period.
    Returns:
        DataFrame containing product names, measurements, dates, prices, and categories.
    """
    processed_tables = []

    for page in pages:
        page_number = page.page_number
        # Extract table data and page text
        table_data = page.extract_table()
        page_text = page.extract_text() or ''

        # Identify the product category using primary category names
        product_category = None
        for category_name, category in category_mapper.items():
            if category_name in page_text.lower():
                product_category = category
                break

        # Use individual product names if the primary category is not found
        if product_category == None:
            for product_name, category in product_category_mapper.items():
                    if product_name in page_text.lower():
                        product_category = category
                        break

        # Raise an error if the product category cannot be identified
        if product_category is None:
            raise ValueError(
                f"Unknown product category in {report_name}, table page {page_number}"
            )

        if not table_data:
            raise ValueError(f'{report_name}, page {page_number}: no extractable table')

        table_df = pd.DataFrame(table_data)

        # Identify the header row using the presence of numeric values
        header_mask = table_df.astype("string").apply(lambda column: column.str.fullmatch(r'\d+')).any(axis=1)
        header_index = header_mask[header_mask].index[0] if header_mask.any() else None

        if header_index is None:
            raise ValueError(f'{report_name}, page {page_number}: no numeric date header')

        # Recover column names and identify valid product, measurement, and date columns
        column_names = table_df.iloc[header_index].replace('', None).fillna(table_df.iloc[0]).str.replace('\n', ' ')
        identifier_columns = ['Producto', 'Medida']

        valid_columns = [column for column in column_names.astype('string').dropna() if column in identifier_columns or column.isdigit()]

        # Remove irrelevant columns and rows preceding the table data
        columns_to_drop = column_names.index[~column_names.isin(valid_columns)]

        column_names = column_names.drop(index=columns_to_drop)
        table_df = table_df.drop(columns=columns_to_drop).iloc[header_index+1:].reset_index(drop=True)

        # Assign standardized column names
        table_df.columns = column_names
        table_df = table_df.rename(columns={'Producto': 'product', 'Medida': 'measurement'})
        column_names = table_df.columns

        # Separate identifier columns from date columns
        key_columns = ['product', 'measurement']
        date_columns = [column for column in column_names if column not in key_columns]

        # Match day numbers in table headers to reporting dates
        date_column_mapper = {}
        for date_column in date_columns:
            for report_date in date_range:
                if report_date.day == int(date_column):
                    date_column_mapper[date_column] = report_date
                    break

        # Drop date columns outside the reporting period
        invalid_date_columns = set(date_columns) - set(date_column_mapper)

        if invalid_date_columns:
            LOGGER.warning(
                '%s, page %s: dropping out-of-range date columns %s',
                report_name, page_number, sorted(invalid_date_columns)
            )
            table_df = table_df.drop(columns=list(invalid_date_columns))

        # Skip pages without dates within the reporting period
        if not date_column_mapper:
            LOGGER.warning(
                '%s, page %s: skipping page without valid dates',
                report_name, page_number
            )
            continue

        # Validate remaining table headers
        if (not set(key_columns).issubset(table_df.columns)
                or table_df.columns.duplicated().any()):
            raise ValueError(f'{report_name}, page {page_number}: invalid table headers')

        table_df = table_df.rename(columns=date_column_mapper)

        # Reshape price observations from wide to long format
        table_df = table_df.melt(id_vars=key_columns, var_name='date', value_name='price')

        # Clean product names, measurements, and price values
        table_df['product'] = table_df['product'].str.replace('\n', ' ')
        table_df['measurement'] = table_df['measurement'].str.replace('\n', ' ')
        raw_price = table_df['price'].astype('string').str.strip()
        missing = raw_price.isna() | raw_price.str.upper().isin(['SO', 'SD', 'ND', ''])
        table_df = table_df.loc[~missing].copy()
        table_df['price'] = pd.to_numeric(raw_price.loc[~missing].str.replace(',', '', regex=False), errors='raise')
        table_df['product_type'] = product_category
        table_df['source_report'] = report_name
        table_df['source_url'] = source_url
        table_df['source_page'] = page_number
        table_df['extracted_at'] = pd.Timestamp.now(tz='UTC')
        if table_df[key_columns].isna().any().any() or table_df[key_columns].apply(lambda c: c.str.strip().eq('')).any().any():
            raise ValueError(f'{report_name}, page {page_number}: price without product/unit')
        table_df = filter_invalid_prices(table_df)
        if not table_df['price'].map(lambda value: pd.notna(value) and float('-inf') < value < float('inf')).all():
            raise ValueError(f'{report_name}, page {page_number}: nonfinite price')
        if table_df.empty and not missing.all():
            raise ValueError(f'{report_name}, page {page_number}: no valid prices')
        LOGGER.info('%s page %s: %s prices, %s missing cells', report_name, page_number, len(table_df), int(missing.sum()))

        processed_tables.append(table_df)

    # Combine all processed tables into a single DataFrame
    return pd.concat(processed_tables, ignore_index=True) if processed_tables else pd.DataFrame(columns=PRICE_COLUMNS)



# Define additional main products requiring manual selection
MANUALLY_SELECTED_PRODUCTS = {
    'Aguacate Hass, mediano, de primera',
    'Frijol negro, de primera',
    'Zanahoria mediana, de primera',
    'Maiz amarillo, de primera',
    'Limon Persa, mediano, de primera',
    'Banano de exportacion, mediano, de primera',
}
ACCENT_MAPPER = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ü': 'u'}


def read_processed_reports(path: Path = PROCESSED_REPORTS_FILE) -> set[str]:
    """Load the successful-report manifest; absent file means none are verified."""
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
        raise ValueError(f'Invalid processed-report manifest: {path}')
    return set(payload)


def discover_new_reports(processed: set[str]) -> dict[str, tuple[str, pd.Timestamp]]:
    """Discover all report years, excluding only successfully processed filenames."""
    from selenium import webdriver
    from selenium.webdriver.common.by import By

    driver = webdriver.Chrome()
    try:
        driver.get(REPORTS_URL)
        # Relative selectors avoid dependence on the exact page layout.
        links = driver.find_elements(By.CSS_SELECTOR, 'details a[href]')
        if not links:
            links = driver.find_elements(By.CSS_SELECTOR, 'a[href]')
        available = {}
        for link in links:
            name = (link.get_attribute('textContent') or '').strip()
            url = link.get_attribute('href')
            if not name or not url or name in processed:
                continue
            try:
                report_date = find_max_date(name)
                if report_date.year < MIN_REPORT_YEAR:
                    continue
            except (AttributeError, KeyError, ValueError, IndexError):
                LOGGER.debug('Skipping non-report link: %s', name)
                continue
            available[name] = (url, report_date)
        return dict(sorted(available.items(), key=lambda item: (item[1][1], item[0])))
    finally:
        driver.quit()


def download_reports(reports: dict, directory: Path) -> list[Path]:
    """Download reports; only completed PDFs are returned for processing."""
    directory.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for report_name, (url, _) in tqdm(reports.items(), desc='Downloading PDFs'):
        # Never allow a website-supplied filename to escape the reports folder.
        path = directory / Path(report_name).name
        if path.name != report_name:
            raise ValueError(f"Unsafe report filename: {report_name}")
        if path.exists() and path.stat().st_size:
            with path.open("rb") as local_pdf:
                if local_pdf.read(5) != b"%PDF-":
                    raise ValueError(f"Local report is not a valid PDF: {path.name}")
            downloaded.append(path)
            continue
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        temp = path.with_name(path.name + '.part')
        if not response.content.startswith(b"%PDF-"):
            raise ValueError(f"Downloaded content is not a PDF: {report_name}")
        temp.write_bytes(response.content)
        temp.replace(path)
        downloaded.append(path)
    return downloaded


def extract_prices(paths: list[Path], source_urls=None) -> tuple[pd.DataFrame, set[str]]:
    """Accept only reports whose in-scope table pages all parse successfully."""
    reports = []
    successful = set()
    source_urls = source_urls or {}
    for path in tqdm(paths, desc='Reading PDFs'):
        try:
            with pdfplumber.open(path) as pdf:
                pages = pdf.pages
                product_pages = []
                for page in pages:
                    text = (page.extract_text() or '').lower()
                    if 'tabla' not in text and not ('precios diarios' in text and 'producto' in text and 'medida' in text):
                        continue
                    if any(category in text for category in category_mapper):
                        product_pages.append(page)
                    elif any(category in text for category in ('pecuarios', 'flores de corte', 'especias', 'abarrotes', 'hidrobiológicos', 'fertilizantes')):
                        continue
                    elif any(category in text for category in product_category_mapper):
                        product_pages.append(page)
                if not product_pages:
                    raise ValueError('No in-scope price tables found')
                date_range = get_dates_from_pages(pages)
                report = process_tables(product_pages, date_range, report_name=path.name,
                                        source_url=source_urls.get(path.name))
                if report.empty:
                    raise ValueError('No valid product price rows extracted')
                report['product'] = report['product'].str.translate(str.maketrans(ACCENT_MAPPER)).str.replace('.', '', regex=False)
                keys = ['product', 'date', 'measurement']
                if report.groupby(keys)['price'].nunique().gt(1).any():
                    raise ValueError('Conflicting prices for the same key within report')
                reports.append(report)
                successful.add(path.name)
                LOGGER.info('%s: processed all %s selected table pages', path.name, len(product_pages))
        except Exception:
            LOGGER.exception('Report rejected; no rows accepted from %s', path.name)
    if not reports:
        return pd.DataFrame(columns=PRICE_COLUMNS), successful
    prices = pd.concat(reports, ignore_index=True)
    return prices, successful


def build_products(prices: pd.DataFrame) -> pd.DataFrame:
    """Generate product reference data and select representative products."""
    products = prices[['product']].drop_duplicates().copy()
    products['grade'] = products['product'].apply(get_grade).astype('string')
    products['size'] = products['product'].apply(get_size).astype('string')
    products['normalized_product'] = products['product'].apply(normalize_product)
    products['variant'] = products.apply(
        lambda row: get_variant(row['product'], row['normalized_product']), axis=1
    )

    counts = (
        prices.merge(products, on='product', how='left')
        .groupby(['product', 'normalized_product', 'grade', 'size'],
                 as_index=False, dropna=False)
        .agg(n_observations=('price', 'count'))
    )
    # Manual selections override the automatic selection WITHIN each category.
    # Recompute flags from the complete historical price dataset, never a batch.
    if products['normalized_product'].isna().any():
        missing = products.loc[products['normalized_product'].isna(), 'product'].tolist()
        raise ValueError(f'Cannot normalize products: {missing}')
    selected = {}
    for normalized_product, candidates in counts.groupby('normalized_product'):
        manual = candidates.loc[candidates['product'].isin(MANUALLY_SELECTED_PRODUCTS)]
        if len(manual) > 1:
            raise ValueError(f'Multiple manual main products for {normalized_product}: '
                             f"{manual['product'].tolist()}")
        if len(manual) == 1:
            selected[normalized_product] = manual['product'].iloc[0]
        else:
            selected[normalized_product] = select_main_product(candidates)
    products['is_main_product'] = products.apply(
        lambda row: row['product'] == selected[row['normalized_product']], axis=1
    )
    if (products.loc[products['is_main_product']]
            .groupby('normalized_product').size().ne(1).any()):
        raise ValueError('Main-product uniqueness validation failed')

    category_counts = prices.groupby('product')['product_type'].nunique(dropna=False)
    conflicting = category_counts[category_counts > 1]
    if not conflicting.empty:
        raise ValueError(f'Conflicting categories for products: {list(conflicting.index)}')
    categories = prices.groupby('product', as_index=False).agg(type=('product_type', 'first'))
    return products.merge(categories, on='product', how='left')


def read_existing(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if path.exists() else None


def validate_prices(prices: pd.DataFrame) -> None:
    """Reject ambiguous price keys, missing values, or impossible prices."""
    required = ['product', 'date', 'measurement', 'price']
    if prices[required].isna().any().any():
        raise ValueError('Price records cannot have missing product/date/unit/price')
    if not pd.to_numeric(prices['price'], errors='coerce').map(
        lambda value: pd.notna(value) and float('-inf') < value < float('inf') and value > 0
    ).all():
        raise ValueError('Prices must be positive')
    if prices.duplicated(['product', 'date', 'measurement']).any():
        raise ValueError('Duplicate product/date/unit keys remain after merging')


def filter_invalid_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Log nonpositive prices before merging, so they cannot replace valid history."""
    numeric = pd.to_numeric(prices['price'], errors='raise')
    invalid = numeric.le(0)
    for row in prices.loc[invalid].to_dict('records'):
        LOGGER.warning('Rejected nonpositive price: %s', row)
    if invalid.any():
        LOGGER.warning('Rejected %s nonpositive observations', int(invalid.sum()))
    return prices.loc[~invalid].copy()


def persist(new_prices: pd.DataFrame, directory: Path) -> None:
    """Merge all history then rebuild main-product flags from the full dataset."""
    directory.mkdir(parents=True, exist_ok=True)
    price_file, product_file = directory / 'prices.parquet', directory / 'products.parquet'
    new_prices = filter_invalid_prices(new_prices)
    if new_prices.empty:
        raise ValueError('No valid new prices to persist')
    old_prices = read_existing(price_file)
    if old_prices is not None:
        new_prices = pd.concat([old_prices, new_prices], ignore_index=True)
    # Keep newer observations when a report is corrected.
    prices = new_prices.drop_duplicates(
        subset=['product', 'date', 'measurement'], keep='last'
    ).copy()
    prices['date'] = pd.to_datetime(prices['date'], errors='raise')
    prices['price'] = pd.to_numeric(prices['price'], errors='raise')
    # Existing prices lack product_type; recover their category from the
    # previous product master before rebuilding it.
    if 'product_type' not in prices.columns:
        prices['product_type'] = pd.NA
    old_products = read_existing(product_file)
    if old_products is not None and {'product', 'type'}.issubset(old_products.columns):
        mapping = old_products.set_index('product')['type']
        prices['product_type'] = prices['product_type'].fillna(prices['product'].map(mapping))
    if prices['product_type'].isna().any():
        unknown = prices.loc[prices['product_type'].isna(), 'product'].unique().tolist()
        raise ValueError(f'Unknown historical product categories: {unknown}')
    validate_prices(prices)
    products = build_products(prices)
    if len(products) != prices['product'].nunique():
        raise ValueError('Product catalog is not unique by original product')
    for column in SOURCE_COLUMNS:
        if column not in prices:
            prices[column] = pd.NA
    prices['source_page'] = prices['source_page'].astype('Int64')
    prices['extracted_at'] = pd.to_datetime(prices['extracted_at'], utc=True)
    for column in ['source_report', 'source_url']:
        prices[column] = prices[column].astype('string')
    saved_prices = prices.drop(columns='product_type')
    # Temp files keep incomplete writes from replacing the existing data.
    tmp_prices = price_file.with_suffix('.parquet.tmp')
    tmp_products = product_file.with_suffix('.parquet.tmp')
    try:
        saved_prices.to_parquet(tmp_prices, index=False)
        products.to_parquet(tmp_products, index=False)
        tmp_prices.replace(price_file)
        tmp_products.replace(product_file)
    finally:
        tmp_prices.unlink(missing_ok=True)
        tmp_products.unlink(missing_ok=True)
    LOGGER.info('Saved %s price rows and %s products', len(prices), len(products))


def main() -> None:
    processed = read_processed_reports()
    new_reports = discover_new_reports(processed)
    LOGGER.info('Found %s reports not in successful-report manifest', len(new_reports))
    if not new_reports:
        LOGGER.info('Nothing to process')
        return
    paths = download_reports(new_reports, FILES_DIR)
    new_prices, successful = extract_prices(paths, {name: url for name, (url, _) in new_reports.items()})
    if new_prices.empty:
        LOGGER.warning('No prices extracted; datasets unchanged')
        return
    persist(new_prices, DATA_DIR)
    # The manifest is only updated AFTER the datasets were persisted.
    manifest_temp = PROCESSED_REPORTS_FILE.with_suffix('.json.tmp')
    manifest_temp.write_text(json.dumps(sorted(processed | successful),
                                        ensure_ascii=False, indent=2), encoding='utf-8')
    manifest_temp.replace(PROCESSED_REPORTS_FILE)
    LOGGER.info('Recorded %s processed PDFs', len(successful))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    main()
