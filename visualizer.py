# visualizer.py

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
import pandas as pd

from config import config
from logger import get_logger

logger = get_logger("visualizer")

sns.set_theme(style="whitegrid", palette="Blues_d", font_scale=1.1)

SUPPORTED_CHARTS = {
    "bar", "horizontal_bar", "line", "pie", "scatter",
    "histogram", "box", "violin", "multi_series",
}


def generate_chart(
    data: list[dict],
    chart_type: str,
    category_column: str,
    value_column: str,
    title: str,
    value_columns: list[str] | None = None,
) -> Path:
    """
    Render a chart from aggregated query result data and save to CHARTS_DIR.

    Returns the absolute Path of the saved PNG.
    Raises ValueError if data is empty or required columns are missing.
    """
    if not data:
        raise ValueError("Cannot generate chart: query returned no data.")

    df = pd.DataFrame(data)

    chart_type = chart_type.lower()
    if chart_type not in SUPPORTED_CHARTS:
        logger.warning(f"Unknown chart type '{chart_type}', falling back to bar")
        chart_type = "bar"

    # ── Normalise column names (Oracle returns UPPER; be tolerant) ────────────
    col_map = {c.upper(): c for c in df.columns}

    def _resolve(col: str | None) -> str | None:
        if col is None:
            return None
        return col_map.get(col.upper(), col)

    category_column = _resolve(category_column)
    value_column    = _resolve(value_column)
    if value_columns:
        value_columns = [_resolve(c) for c in value_columns]

    # ── Guard: category_column must not be None ───────────────────────────────
    if category_column is None:
        # Infer: pick the first non-numeric column, else the first column
        text_cols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
        category_column = text_cols[0] if text_cols else df.columns[0]
        logger.warning(f"category_column was None — inferred '{category_column}'")

    # ── Guard: category_column must differ from value_column for non-histogram ─
    if chart_type not in ("histogram",) and category_column == value_column:
        # Try to pick a different column
        other = [c for c in df.columns if c != value_column]
        if other:
            text_like = [c for c in other if not pd.api.types.is_numeric_dtype(df[c])]
            category_column = text_like[0] if text_like else other[0]
            logger.warning(
                f"category_column == value_column — reassigned category to '{category_column}'"
            )
        else:
            # Only one column: render a histogram instead
            logger.warning("Single-column data — switching to histogram")
            chart_type = "histogram"

    # ── Column validation ─────────────────────────────────────────────────────
    if chart_type == "multi_series":
        required_cols = [category_column] + (value_columns or [value_column])
    elif chart_type == "histogram":
        required_cols = [value_column]
    else:
        required_cols = [category_column, value_column]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        # One final attempt: case-insensitive lookup
        df.columns = [c.upper() for c in df.columns]
        required_upper = [c.upper() if c else c for c in required_cols]
        missing = [c for c in required_upper if c and c not in df.columns]
        if missing:
            raise ValueError(
                f"Column(s) not found in query result: {missing}. "
                f"Available columns: {list(df.columns)}"
            )
        category_column = category_column.upper() if category_column else category_column
        value_column    = value_column.upper()
        if value_columns:
            value_columns = [c.upper() for c in value_columns]

    # ── Drop rows where key columns are None/NaN ──────────────────────────────
    drop_subset = (
        required_cols if chart_type != "multi_series"
        else [category_column] + (value_columns or [value_column])
    )
    drop_subset = [c for c in drop_subset if c]  # remove any None entries
    df = df.dropna(subset=drop_subset)
    if df.empty:
        raise ValueError("No plottable data after dropping NULL rows.")

    # ── Coerce numeric columns ────────────────────────────────────────────────
    numeric_targets = (
        (value_columns or [value_column]) if chart_type == "multi_series" else [value_column]
    )
    for col in numeric_targets:
        if col:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=[c for c in numeric_targets if c])
    if df.empty:
        raise ValueError("No numeric data remaining after type coercion.")

    # ── Auto-correct swapped category / value columns ─────────────────────────
    if chart_type not in ("histogram", "multi_series", "scatter"):
        if category_column and value_column:
            cat_is_numeric = pd.api.types.is_numeric_dtype(df[category_column])
            val_is_numeric = pd.api.types.is_numeric_dtype(df[value_column])
            if cat_is_numeric and not val_is_numeric:
                logger.warning(
                    f"category_column='{category_column}' is numeric and "
                    f"value_column='{value_column}' is text — swapping."
                )
                category_column, value_column = value_column, category_column

    # ── Auto-upgrade bar → horizontal_bar when there are many categories ──────
    if chart_type == "bar" and category_column:
        n_cats = df[category_column].nunique()
        if n_cats > 10:
            logger.info(f"Switching bar → horizontal_bar ({n_cats} categories)")
            chart_type = "horizontal_bar"

    # ── Scatter: both axes must be numeric; fall back to horizontal_bar ───────
    if chart_type == "scatter":
        cat_numeric = pd.api.types.is_numeric_dtype(df[category_column])
        val_numeric = pd.api.types.is_numeric_dtype(df[value_column])
        if not (cat_numeric and val_numeric):
            logger.warning(
                "Scatter requires two numeric columns; at least one is non-numeric. "
                "Falling back to horizontal_bar."
            )
            # Swap so the text column becomes the category for horizontal_bar
            if not cat_numeric and val_numeric:
                pass  # already correct orientation
            elif cat_numeric and not val_numeric:
                category_column, value_column = value_column, category_column
            chart_type = "horizontal_bar"

    # ── Render ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 7))

    try:
        if chart_type == "bar":
            _bar_chart(df, category_column, value_column, ax)
        elif chart_type == "horizontal_bar":
            _horizontal_bar_chart(df, category_column, value_column, ax)
        elif chart_type == "line":
            _line_chart(df, category_column, value_column, ax)
        elif chart_type == "pie":
            _pie_chart(df, category_column, value_column, ax)
        elif chart_type == "scatter":
            _scatter_chart(df, category_column, value_column, ax)
        elif chart_type == "histogram":
            _histogram_chart(df, value_column, ax)
        elif chart_type == "box":
            _box_chart(df, category_column, value_column, ax)
        elif chart_type == "violin":
            _violin_chart(df, category_column, value_column, ax)
        elif chart_type == "multi_series":
            _multi_series_chart(df, category_column, value_columns or [value_column], ax)

        ax.set_title(title, fontsize=14, fontweight="bold", pad=16)
        plt.tight_layout()

        output_path = _save_figure(fig, chart_type)
        logger.info(f"Chart saved: {output_path}")
        return output_path

    finally:
        plt.close(fig)


# ── Chart Renderers ────────────────────────────────────────────────────────────

def _fmt_y(ax: plt.Axes) -> None:
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))


def _fmt_x(ax: plt.Axes) -> None:
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))


def _label_axis(ax: plt.Axes, col: str | None, axis: str = "x") -> None:
    if not col:
        return
    label = col.replace("_", " ").title()
    if axis == "x":
        ax.set_xlabel(label, fontsize=11)
    else:
        ax.set_ylabel(label, fontsize=11)


def _bar_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    data    = df.groupby(category)[value].sum().reset_index().sort_values(value, ascending=False)
    palette = sns.color_palette("Blues_d", len(data))
    sns.barplot(data=data, x=category, y=value, ax=ax, palette=palette,
                hue=category, legend=False)
    _label_axis(ax, category, "x")
    _label_axis(ax, value, "y")
    _fmt_y(ax)
    plt.xticks(rotation=30, ha="right")


def _horizontal_bar_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    data    = df.groupby(category)[value].sum().reset_index().sort_values(value, ascending=True)
    palette = sns.color_palette("Blues_d", len(data))
    sns.barplot(data=data, y=category, x=value, ax=ax, palette=palette,
                hue=category, orient="h", legend=False)
    _label_axis(ax, category, "y")
    _label_axis(ax, value, "x")
    _fmt_x(ax)


def _line_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    data = df.groupby(category)[value].sum().reset_index()
    try:
        data[category] = pd.to_numeric(data[category])
    except (ValueError, TypeError):
        pass
    data = data.sort_values(category)
    sns.lineplot(data=data, x=category, y=value, ax=ax,
                 marker="o", linewidth=2.5, color="#2196F3")
    _label_axis(ax, category, "x")
    _label_axis(ax, value, "y")
    _fmt_y(ax)
    plt.xticks(rotation=30, ha="right")


def _pie_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    data = df.groupby(category)[value].sum()
    if len(data) > 9:
        top   = data.nlargest(9)
        other = pd.Series({"Other": data[~data.index.isin(top.index)].sum()})
        data  = pd.concat([top, other])
    palette = sns.color_palette("Blues_d", len(data))
    ax.pie(data.values, labels=data.index, autopct="%1.1f%%",
           startangle=140, colors=palette)


def _scatter_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    sns.scatterplot(data=df, x=category, y=value, ax=ax,
                    alpha=0.7, s=80, color="#1565C0")
    _label_axis(ax, category, "x")
    _label_axis(ax, value, "y")


def _histogram_chart(df: pd.DataFrame, value: str, ax: plt.Axes) -> None:
    sns.histplot(data=df, x=value, ax=ax, kde=True,
                 color="#1976D2", edgecolor="white", linewidth=0.6)
    _label_axis(ax, value, "x")
    ax.set_ylabel("Frequency", fontsize=11)
    _fmt_x(ax)


def _box_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    top_cats = df.groupby(category)[value].median().nlargest(20).index
    plot_df  = df[df[category].isin(top_cats)]
    palette  = sns.color_palette("Blues_d", len(top_cats))
    sns.boxplot(data=plot_df, x=category, y=value, ax=ax,
                palette=palette, hue=category, legend=False, order=top_cats)
    _label_axis(ax, category, "x")
    _label_axis(ax, value, "y")
    _fmt_y(ax)
    plt.xticks(rotation=30, ha="right")
    if len(df[category].unique()) > 20:
        ax.annotate("Showing top 20 categories by median",
                    xy=(0.01, 0.97), xycoords="axes fraction",
                    fontsize=9, color="grey", va="top")


def _violin_chart(df: pd.DataFrame, category: str, value: str, ax: plt.Axes) -> None:
    top_cats = df.groupby(category)[value].median().nlargest(15).index
    plot_df  = df[df[category].isin(top_cats)]
    palette  = sns.color_palette("Blues_d", len(top_cats))
    sns.violinplot(data=plot_df, x=category, y=value, ax=ax,
                   palette=palette, hue=category, legend=False,
                   order=top_cats, inner="box", cut=0)
    _label_axis(ax, category, "x")
    _label_axis(ax, value, "y")
    _fmt_y(ax)
    plt.xticks(rotation=30, ha="right")
    if len(df[category].unique()) > 15:
        ax.annotate("Showing top 15 categories by median",
                    xy=(0.01, 0.97), xycoords="axes fraction",
                    fontsize=9, color="grey", va="top")


def _multi_series_chart(
    df: pd.DataFrame,
    category: str,
    value_cols: list[str],
    ax: plt.Axes,
) -> None:
    TIME_HINTS = {"mnth", "month", "year", "quarter", "week", "date", "period", "qtr",
                  "yr", "mon", "wk", "dy", "day"}
    is_time = any(hint in category.lower() for hint in TIME_HINTS)

    agg = df.groupby(category)[value_cols].sum().reset_index()
    try:
        agg[category] = pd.to_numeric(agg[category])
    except (ValueError, TypeError):
        pass
    agg = agg.sort_values(category)

    agg_melt = agg.melt(id_vars=category, value_vars=value_cols,
                        var_name="Series", value_name="Value")
    palette = sns.color_palette("tab10", len(value_cols))

    if is_time:
        sns.lineplot(data=agg_melt, x=category, y="Value", hue="Series",
                     ax=ax, marker="o", linewidth=2.2, palette=palette)
    else:
        sns.barplot(data=agg_melt, x=category, y="Value", hue="Series",
                    ax=ax, palette=palette)
    plt.xticks(rotation=30, ha="right")
    _label_axis(ax, category, "x")
    ax.set_ylabel("Value", fontsize=11)
    _fmt_y(ax)
    ax.legend(title="Series", bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=9)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _save_figure(fig: plt.Figure, chart_type: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"chart_{chart_type}_{timestamp}.png"
    path      = config.CHARTS_DIR / filename
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return path.resolve()
