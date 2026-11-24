import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wholesale_prices as wp


def test_date_parsing_cross_month():
    matched = wp.match_date_text('Informe semanal del 31 de agosto al 04 de septiembre de 2026')
    assert matched is not None
    dates = wp.format_matched_date(matched)
    assert dates[0] == pd.Timestamp('2026-08-31')
    assert dates[-1] == pd.Timestamp('2026-09-04')


def test_date_parsing_cross_year():
    matched = wp.match_date_text('30 de diciembre 2024 al 03 de enero 2025')
    assert matched is not None
    assert wp.format_matched_date(matched)[-1] == pd.Timestamp('2025-01-03')


def test_date_parsing_same_month():
    matched = wp.match_date_text('del 05 al 09 de enero 2026')
    assert wp.format_matched_date(matched)[0] == pd.Timestamp('2026-01-05')


def test_manual_main_overrides_automatic():
    rows = []
    for i in range(4):
        rows.append(dict(product='Frijol rojo, de primera', product_type='basic_grains',
                         measurement='Quintal', date=pd.Timestamp('2026-01-01') + pd.Timedelta(days=i), price=100+i))
    rows.append(dict(product='Frijol negro, de primera', product_type='basic_grains',
                     measurement='Quintal', date=pd.Timestamp('2026-01-01'), price=90))
    products = wp.build_products(pd.DataFrame(rows))
    selected = products.loc[products['is_main_product'], 'product'].tolist()
    assert selected == ['Frijol negro, de primera']
    assert products.groupby('normalized_product')['is_main_product'].sum().eq(1).all()


def test_automatic_tie_is_deterministic():
    rows = pd.DataFrame([
        dict(product='Maiz blanco, de primera', product_type='basic_grains', price=1),
        dict(product='Maiz rojo, de primera', product_type='basic_grains', price=1),
    ])
    output = wp.build_products(rows)
    assert output['is_main_product'].sum() == 1


def test_prices_reject_duplicate_keys():
    data = pd.DataFrame([
        dict(product='Maiz blanco', measurement='Quintal', date=pd.Timestamp('2026-01-01'), price=10),
        dict(product='Maiz blanco', measurement='Quintal', date=pd.Timestamp('2026-01-01'), price=11),
    ])
    with pytest.raises(ValueError, match='Duplicate'):
        wp.validate_prices(data)


def test_manifest_roundtrip(tmp_path):
    path = tmp_path / 'processed_reports.json'
    assert wp.read_processed_reports(path) == set()
    path.write_text(json.dumps(['report1.pdf', 'report2.pdf']), encoding='utf-8')
    assert wp.read_processed_reports(path) == {'report1.pdf', 'report2.pdf'}


def test_persist_rebuilds_flags_from_full_history(tmp_path, monkeypatch):
    # Simulate the parquet interface without requiring pyarrow for unit tests.
    monkeypatch.setattr(pd, 'read_parquet', lambda path: pd.read_pickle(path))
    monkeypatch.setattr(pd.DataFrame, 'to_parquet',
                        lambda self, path, index=False: self.to_pickle(path))
    historical = pd.DataFrame([
        dict(product='Frijol rojo, de primera', measurement='Quintal',
             date=pd.Timestamp('2026-01-01') + pd.Timedelta(days=i), price=100+i)
        for i in range(4)
    ])
    historical.to_pickle(tmp_path / 'prices.parquet')
    wp.build_products(historical.assign(product_type='basic_grains')).to_pickle(tmp_path / 'products.parquet')
    new = pd.DataFrame([dict(product='Frijol negro, de primera', measurement='Quintal',
                             date=pd.Timestamp('2026-02-01'), price=90, product_type='basic_grains')])
    wp.persist(new, tmp_path)
    saved_prices = pd.read_pickle(tmp_path / 'prices.parquet')
    saved_products = pd.read_pickle(tmp_path / 'products.parquet')
    assert len(saved_prices) == 5
    assert saved_products.groupby('normalized_product')['is_main_product'].sum().eq(1).all()
    assert saved_products.loc[saved_products['is_main_product'], 'product'].tolist() == ['Frijol negro, de primera']


def test_invalid_new_price_cannot_replace_history(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pd, 'read_parquet', lambda path: pd.read_pickle(path))
    monkeypatch.setattr(pd.DataFrame, 'to_parquet', lambda self, path, index=False: self.to_pickle(path))
    base = dict(product='Frijol negro, de primera', measurement='Quintal', product_type='basic_grains')
    old = pd.DataFrame([dict(base, date=pd.Timestamp('2026-01-01'), price=100)])
    wp.persist(old, tmp_path)
    new = pd.DataFrame([
        dict(base, date=pd.Timestamp('2026-01-01'), price=0),
        dict(base, date=pd.Timestamp('2026-01-02'), price=110, source_report='new.pdf',
             source_url='https://example.org/new.pdf', source_page=5, extracted_at=pd.Timestamp('2026-01-03', tz='UTC')),
    ])
    wp.persist(new, tmp_path)
    saved = pd.read_pickle(tmp_path / 'prices.parquet').sort_values('date')
    assert saved['price'].tolist() == [100, 110]
    assert pd.isna(saved.iloc[0]['source_report'])
    assert saved.iloc[1]['source_page'] == 5
    assert saved.iloc[1]['source_report'] == 'new.pdf'
    assert 'Rejected nonpositive price' in caplog.text


class FakePage:
    def __init__(self, number, table):
        self.page_number = number
        self.table = table

    def extract_text(self):
        return 'del 05 al 06 de enero 2026\nGranos básicos\nTabla 1'

    def extract_table(self):
        return self.table


def test_partial_report_is_rejected(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    valid = FakePage(5, [['Producto', 'Medida', '05', '06'], ['Frijol negro', 'Quintal', '100', '110']])
    invalid = FakePage(6, None)
    monkeypatch.setattr(wp.pdfplumber, 'open', lambda path: nullcontext(SimpleNamespace(pages=[valid, invalid])))
    prices, successful = wp.extract_prices([tmp_path / 'partial.pdf'])
    assert prices.empty
    assert successful == set()


def test_unmatched_or_missing_date_column_rejects_page():
    page = FakePage(5, [['Producto', 'Medida', '05'], ['Frijol negro', 'Quintal', '100']])
    with pytest.raises(ValueError, match='invalid table headers'):
        wp.process_tables([page], list(pd.date_range('2026-01-05', '2026-01-06')))


def test_example_pdf_provenance():
    path = next((Path(wp.BASE_DIR) / 'examples').glob('*.pdf'), None)
    if path is None:
        pytest.skip('Optional source PDF is not available')
    url = 'https://example.org/report.pdf'
    prices, successful = wp.extract_prices([path], {path.name: url})
    assert successful == {path.name}
    assert len(prices) == 219
    assert set(prices['source_page']) == {5, 9, 10, 14}
    assert prices['source_report'].eq(path.name).all()
    assert prices['source_url'].eq(url).all()
    assert prices['extracted_at'].notna().all()
