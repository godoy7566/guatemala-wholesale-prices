"""Agricultural wholesale prices, organized by normalized product.

Run from the project folder: python -m streamlit run dashboard.py
Expected layout: dashboard.py and data/{prices,products}.parquet
"""
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
TABLES_DIR = BASE_DIR / "data"

st.set_page_config(page_title="Agricultural Prices", page_icon="🌱", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 1.8rem; max-width: 1450px;}
[data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.18); padding: 14px 18px; border-radius: 12px;}
[data-testid="stMetricLabel"] {font-weight: 600;}
</style>
""", unsafe_allow_html=True)


def opts(series):
    return sorted(series.dropna().astype(str).unique().tolist(), key=str.casefold)


@st.cache_data(show_spinner="Loading prices…")
def load_data():
    prices = pd.read_parquet(TABLES_DIR / "prices.parquet")
    products = pd.read_parquet(TABLES_DIR / "products.parquet")
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["price"] = pd.to_numeric(prices["price"], errors="coerce")

    if products["product"].duplicated().any():
        raise ValueError("products.parquet must contain one row per original product.")

    # A normalized product is a category; its single main product supplies
    # the representative price series for all dashboard-level analysis.
    products["is_main_product"] = products["is_main_product"].astype("boolean")
    mains = products.loc[
        products["is_main_product"].eq(True).fillna(False)
        & products["normalized_product"].notna()
    ].copy()
    repeated = mains.loc[mains["normalized_product"].duplicated(keep=False), "normalized_product"]
    if not repeated.empty:
        raise ValueError(
            "Multiple products are marked as main for these normalized products: "
            + ", ".join(opts(repeated))
        )

    joined = prices.merge(products, on="product", how="left", validate="many_to_one", indicator=True)
    joined["matched_product"] = joined.pop("_merge").eq("both")
    # Main-only data prevents mixing prices from distinct varieties/grades/sizes
    # within the same normalized product.
    main_data = joined.loc[
        joined["is_main_product"].eq(True).fillna(False)
        & joined["normalized_product"].notna()
    ].copy()
    return products, joined, main_data


def latest_and_previous(frame, group_col="normalized_product"):
    """Compare last two observations by selected product level and unit."""
    keys = [group_col, "measurement"]
    valid = frame.dropna(subset=["date", "price", *keys]).copy()
    if valid.empty:
        return pd.DataFrame()
    daily = (valid.groupby(keys + ["date"], as_index=False)
             .agg(price=("price", "mean")).sort_values("date"))
    last = (daily.groupby(keys, as_index=False).tail(1)
            .rename(columns={"date": "latest_date", "price": "latest_price"}))
    previous = (daily.groupby(keys, as_index=False).nth(-2).reset_index(drop=True)
                .rename(columns={"date": "previous_date", "price": "previous_price"}))
    result = last.merge(previous, on=keys, how="left")
    result["change_pct"] = (result["latest_price"] / result["previous_price"] - 1) * 100
    result.loc[result["previous_price"].eq(0), "change_pct"] = float("nan")
    return result


def seven_day_changes(frame, end_date, group_col="normalized_product"):
    """Compare the as-of prices on the chosen date and seven days beforehand.

    Each observation can be up to seven days old at its own boundary. The
    two boundaries must use distinct observations.
    """
    keys = [group_col, "measurement"]
    valid = frame.dropna(subset=["date", "price", "product", *keys]).copy()
    if valid.empty:
        return pd.DataFrame()
    # The representative original product is shown in the bar tooltip.
    representatives = (valid[keys + ["product"]].drop_duplicates(subset=keys)
                       if group_col != "product" else None)
    daily = (valid.groupby(keys + ["date"], as_index=False)
             .agg(price=("price", "mean")).sort_values("date"))
    end = pd.Timestamp(end_date)
    start = end - pd.Timedelta(days=7)

    def snapshot(boundary, date_name, price_name):
        eligible = daily.loc[daily["date"].le(boundary)]
        return (eligible.groupby(keys, as_index=False).tail(1)
                .rename(columns={"date": date_name, "price": price_name}))

    current = snapshot(end, "latest_date", "latest_price")
    previous = snapshot(start, "previous_date", "previous_price")
    result = current.merge(previous, on=keys, how="inner")
    result = result.loc[
        result["latest_date"].gt(result["previous_date"])
        & result["previous_price"].ne(0)
        & result["latest_date"].ge(end - pd.Timedelta(days=7))
        & result["previous_date"].ge(start - pd.Timedelta(days=7))
    ].copy()
    result["change_pct"] = (result["latest_price"] / result["previous_price"] - 1) * 100
    result["days_between"] = (result["latest_date"] - result["previous_date"]).dt.days
    if representatives is not None:
        return result.merge(representatives, on=keys, how="left", validate="many_to_one")
    return result


try:
    catalog, all_data, main_data = load_data()
except Exception as exc:
    st.error(f"Unable to load the Parquet files: {exc}")
    st.info("Place prices.parquet and products.parquet in a data/ folder beside dashboard.py.")
    st.stop()

st.title("🌱 Agricultural Prices")
st.caption("Explore prices by normalized categories or by original products; normalized categories use their designated main product")

missing_main = set(catalog["normalized_product"].dropna()) - set(
    catalog.loc[catalog["is_main_product"].eq(True).fillna(False), "normalized_product"].dropna()
)
if missing_main:
    st.warning(
        f"{len(missing_main)} normalized products have no designated main product and are excluded "
        "from the charts. Review their flags in the Data explorer."
    )

with st.sidebar:
    st.header("Filters")
    level = st.radio("Analysis level", ["Normalized product", "Original product"],
                     horizontal=False, help="Normalized: one main product per category. "
                     "Original: every individual product, including variants.")
    group_col = "normalized_product" if level == "Normalized product" else "product"
    source_data = main_data if level == "Normalized product" else all_data
    categories = st.multiselect("Product type", opts(source_data["type"]))
    normalized = st.multiselect("Normalized product", opts(source_data["normalized_product"]))
    grades = st.multiselect("Grade", opts(source_data["grade"]))
    sizes = st.multiselect("Size", opts(source_data["size"]))
    units = st.multiselect("Measurement unit", opts(source_data["measurement"]))

    dates = source_data["date"].dropna()
    if not dates.empty:
        minimum, maximum = dates.min().date(), dates.max().date()
        date_range = st.date_input("Date range", (minimum, maximum),
                                   min_value=minimum, max_value=maximum)
    else:
        date_range = ()

# Normalized mode uses only designated main products. Original mode includes variants.
filtered_without_date = source_data.copy()
for column, selection in [
    ("type", categories), ("normalized_product", normalized),
    ("grade", grades), ("size", sizes), ("measurement", units)
]:
    if selection:
        filtered_without_date = filtered_without_date[
            filtered_without_date[column].isin(selection)
        ]

filtered = filtered_without_date.copy()
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    start, end = date_range
    filtered = filtered.loc[filtered["date"].dt.date.between(start, end)]

if filtered.empty:
    st.warning("No price observations match your filters.")
    st.stop()

summary, comparison, explorer = st.tabs(["Overview", "Price comparison", "Data explorer"])

with summary:
    a, b, c, d = st.columns(4)
    a.metric("Normalized products" if group_col == "normalized_product" else "Products",
             f"{filtered[group_col].nunique():,}")
    b.metric("Price observations", f"{filtered['price'].notna().sum():,}")
    c.metric("Measurement units", f"{filtered['measurement'].nunique():,}")
    d.metric("Latest date", filtered["date"].max().strftime("%d %b %Y")
             if filtered["date"].notna().any() else "—")

    st.subheader("Price history")
    chosen = st.selectbox("Normalized product" if group_col == "normalized_product" else "Product",
                          opts(filtered[group_col]), key="overview_selected_product")
    product_data = filtered.loc[filtered[group_col].eq(chosen)].dropna(
        subset=["date", "price"])
    if not product_data.empty:
        representative = opts(product_data["product"])
        if representative and group_col == "normalized_product":
            st.caption(f"Representative product: **{representative[0]}**")
        trend = (product_data.groupby(["date", "measurement"], as_index=False)
                 .agg(price=("price", "mean")))
        fig = px.line(trend.sort_values("date"), x="date", y="price", color="measurement",
                      markers=True,
                      labels={"date": "Date", "price": "Price", "measurement": "Unit"})
        fig.update_layout(margin=dict(l=0, r=0, t=12, b=0))
        st.plotly_chart(fig, use_container_width=True)
        if trend["measurement"].nunique() > 1:
            st.caption("Different measurement units are shown separately and are not directly comparable.")

    st.subheader("Largest 7-day price changes")
    available = filtered_without_date["date"].dropna()
    if available.empty:
        st.info("No dated observations available for a seven-day comparison.")
    else:
        earliest, latest = available.min().date(), available.max().date()
        selected_max = filtered["date"].dropna().max()
        default_end = selected_max.date() if pd.notna(selected_max) else latest
        picker, interval = st.columns([1, 2], vertical_alignment="bottom")
        with picker:
            comparison_end = st.date_input("Week ending", value=default_end,
                                           min_value=earliest, max_value=latest,
                                           key="overview_week_end")
        comparison_start = comparison_end - pd.Timedelta(days=7)
        with interval:
            st.caption(f"Comparing **{comparison_start:%d %b %Y}** to **{comparison_end:%d %b %Y}**")

        weekly = seven_day_changes(filtered_without_date, comparison_end, group_col)
        if weekly.empty:
            st.info("No products have valid observations for both boundaries of this week.")
        else:
            changes = (weekly.assign(abs_change=lambda x: x["change_pct"].abs())
                       .nlargest(12, "abs_change").sort_values("change_pct"))
            # The y-axis shows only the selected product level; original product
            # names and units remain available in the hover details.
            chart = px.bar(
                changes, x="change_pct", y=group_col, orientation="h",
                color="change_pct", color_continuous_scale="RdYlGn",
                color_continuous_midpoint=0,
                labels={"change_pct": "7-day change (%)", group_col: ""},
                hover_data={"product": True, "measurement": True,
                            "previous_date": "|%d %b %Y", "latest_date": "|%d %b %Y",
                            "previous_price": ":.2f", "latest_price": ":.2f",
                            "days_between": True})
            chart.update_layout(coloraxis_showscale=False,
                                margin=dict(l=0, r=0, t=10, b=0), height=420)
            st.plotly_chart(chart, use_container_width=True)
            st.caption(
                ("Uses each normalized product's main product. " if group_col == "normalized_product"
                 else "Uses individual original products. ") + "Compares the latest available "
                "observation at each seven-day boundary (maximum seven days old per boundary). "
                "Actual dates are shown on hover."
            )

with comparison:
    st.subheader("Compare normalized products" if group_col == "normalized_product"
                 else "Compare original products")
    reference = st.selectbox("Reference normalized product" if group_col == "normalized_product"
                             else "Reference product", opts(filtered[group_col]),
                             key="comparison_reference")
    reference_units = opts(filtered.loc[filtered[group_col].eq(reference), "measurement"])
    if len(reference_units) > 1:
        unit = st.selectbox("Measurement unit", reference_units, key="comparison_reference_unit")
    else:
        unit = reference_units[0] if reference_units else None

    if unit:
        st.caption(f"Compatible {'normalized products' if group_col == 'normalized_product' else 'products'} measured in **{unit}**")
        scoped = filtered.loc[filtered["measurement"].eq(unit)].copy()
        candidates = [p for p in opts(scoped[group_col]) if p != reference]
        others = st.multiselect("Other normalized products (up to 7)" if group_col == "normalized_product"
                                else "Other products (up to 7)", candidates,
                                default=candidates[:2], max_selections=7,
                                key=f"comparison_{reference}_{unit}")
        selected = [reference, *others]
        trend = (scoped.loc[scoped[group_col].isin(selected)]
                 .dropna(subset=["date", "price"])
                 .groupby(["date", group_col], as_index=False)
                 .agg(price=("price", "mean")))
        if not trend.empty:
            chart = px.line(trend.sort_values("date"), x="date", y="price",
                            color=group_col, markers=True,
                            labels={"date": "Date", "price": f"Price ({unit})",
                                    group_col: level})
            chart.update_layout(hovermode="x unified", margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(chart, use_container_width=True)

            latest_selection = latest_and_previous(
                scoped.loc[scoped[group_col].isin(selected)], group_col)
            if not latest_selection.empty:
                st.dataframe(
                    latest_selection[[group_col, "measurement", "latest_date",
                                      "latest_price", "previous_date", "previous_price",
                                      "change_pct"]],
                    use_container_width=True, hide_index=True,
                    column_config={"change_pct": st.column_config.NumberColumn(
                        "Change (%)", format="%.1f%%")})
            if group_col == "normalized_product":
                main_names = (scoped.loc[scoped[group_col].isin(selected),
                                         ["normalized_product", "product"]]
                              .drop_duplicates().sort_values("normalized_product"))
                with st.expander("Representative products used"):
                    st.dataframe(main_names, use_container_width=True, hide_index=True)
        else:
            st.info("No valid prices for the selected products.")
    else:
        st.info("The reference product has no available measurement unit.")

with explorer:
    st.subheader("Price observations")
    show_variants = st.checkbox("Include non-main product variants", value=False,
                                help="In normalized mode, charts use only main products; "
                                     "in original-product mode, all products are analyzed.",
                                disabled=group_col == "product")
    if show_variants and group_col == "normalized_product":
        # Apply selected normalized/type categories without discarding their
        # original product variants based on main-product grade/size.
        included_normalized = filtered_without_date["normalized_product"].dropna().unique()
        explorer_data = all_data.loc[
            all_data["normalized_product"].isin(included_normalized)].copy()
        if units:
            explorer_data = explorer_data.loc[explorer_data["measurement"].isin(units)]
        if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
            explorer_data = explorer_data.loc[
                explorer_data["date"].dt.date.between(*date_range)]
    else:
        explorer_data = filtered.copy()
    st.dataframe(explorer_data.sort_values("date", ascending=False),
                 use_container_width=True, hide_index=True)
    st.download_button("Download displayed data (CSV)",
                       data=explorer_data.to_csv(index=False).encode("utf-8-sig"),
                       file_name="filtered_agricultural_prices.csv", mime="text/csv")
    with st.expander("Product master (all original products)"):
        st.dataframe(catalog, use_container_width=True, hide_index=True)
    unmatched = int((~all_data["matched_product"]).sum())
    if unmatched:
        st.warning(f"{unmatched} price rows do not match the product master.")
    st.caption("No price conversion is performed between measurement units.")
