#!/usr/bin/env python3
"""Download Google Analytics 4 data to Parquet files using the Google Analytics Data API.

Fetches several standard GA4 reports and saves each as a zstd-compressed Parquet
file in the data/ directory.

Usage:
    python download_ga_data.py [--property-id ID] [--days N]
                               [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
                               [--filter DIMENSION=VALUE] [--filter DIMENSION!=VALUE]

Authentication:
    Set GOOGLE_APPLICATION_CREDENTIALS to the path of a service account JSON key,
    or configure Application Default Credentials via `gcloud auth application-default login`.

Property ID:
    Set GA4_PROPERTY_ID environment variable, or pass --property-id.
    The property ID is the numeric ID from Google Analytics Admin → Property details
    (digits only, e.g. 123456789 — not prefixed with "properties/").
"""

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Filter,
    FilterExpression,
    FilterExpressionList,
    Metric,
    RunReportRequest,
)

DATA_DIR = Path("data/google-analytics")
DEFAULT_DAYS = 365
PAGE_SIZE = 100_000  # GA4 API maximum rows per request

# Reports to download. Each becomes one Parquet file.
REPORTS = [
    {
        "name": "traffic_sources",
        "description": "Sessions by date, source, medium, and campaign",
        "dimensions": ["date", "landingPage", "sessionSource", "sessionMedium", "sessionCampaignName"],
        "metrics": [
            "sessions",
            "totalUsers",
            "newUsers",
            "bounceRate",
            "averageSessionDuration",
        ],
    },
    {
        "name": "page_views",
        "description": "Page-level traffic and engagement by date, path, and title",
        "dimensions": ["date", "pagePath", "pageTitle"],
        "metrics": [
            "screenPageViews",
            "sessions",
            "averageSessionDuration",
            "bounceRate",
            "totalUsers",
        ],
    },
    {
        "name": "events",
        "description": "Event counts and per-user rates by date and event name",
        "dimensions": ["date", "eventName"],
        "metrics": [
            "eventCount",
            "totalUsers",
            "eventCountPerUser",
        ],
    },
    {
        "name": "user_segments",
        "description": "Sessions by date, country, device, browser, and OS",
        "dimensions": ["date", "country", "deviceCategory", "browser", "operatingSystem"],
        "metrics": [
            "sessions",
            "totalUsers",
            "newUsers",
        ],
    },
]


def parse_filters(filter_strings):
    """Parse a list of filter strings into (dimension, value, exclude) tuples.

    Accepted formats:
        dimension=value   — include only rows where dimension exactly matches value
        dimension!=value  — exclude rows where dimension exactly matches value

    Examples:
        "country=United States"
        "sessionSource!=spam.com"
        "deviceCategory=mobile"
    """
    filters = []
    for f in filter_strings:
        if "!=" in f:
            dimension, value = f.split("!=", 1)
            filters.append((dimension.strip(), value.strip(), True))
        elif "=" in f:
            dimension, value = f.split("=", 1)
            filters.append((dimension.strip(), value.strip(), False))
        else:
            raise SystemExit(
                f"Invalid filter: {f!r}\n"
                "Use 'dimension=value' to include or 'dimension!=value' to exclude."
            )
    return filters


def build_filter_expression(filters):
    """Build a GA4 FilterExpression from parsed (dimension, value, exclude) tuples.

    Multiple filters are AND-ed together.
    Returns None if filters is empty.
    """
    if not filters:
        return None

    exprs = []
    for dimension, value, exclude in filters:
        expr = FilterExpression(
            filter=Filter(
                field_name=dimension,
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.EXACT,
                    value=value,
                    case_sensitive=False,
                ),
            )
        )
        if exclude:
            expr = FilterExpression(not_expression=expr)
        exprs.append(expr)

    if len(exprs) == 1:
        return exprs[0]

    return FilterExpression(and_group=FilterExpressionList(expressions=exprs))


def fetch_report(client, property_id, report, start_date, end_date, filter_expression=None):
    """Fetch all rows for a report, paginating through the full result set."""
    dim_names = report["dimensions"]
    met_names = report["metrics"]
    all_rows = []
    offset = 0

    while True:
        request = RunReportRequest(
            property=f"properties/{property_id}",
            dimensions=[Dimension(name=d) for d in dim_names],
            metrics=[Metric(name=m) for m in met_names],
            date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
            dimension_filter=filter_expression,
            limit=PAGE_SIZE,
            offset=offset,
        )
        response = client.run_report(request)

        for row in response.rows:
            record = {}
            for i, dim in enumerate(dim_names):
                record[dim] = row.dimension_values[i].value
            for i, met in enumerate(met_names):
                record[met] = row.metric_values[i].value
            all_rows.append(record)

        offset += len(response.rows)
        if offset >= response.row_count:
            break

    return all_rows


def rows_to_dataframe(rows, report):
    """Convert raw API rows to a typed DataFrame."""
    dim_names = report["dimensions"]
    met_names = report["metrics"]

    if not rows:
        return pd.DataFrame(columns=dim_names + met_names)

    df = pd.DataFrame(rows)

    # Numeric metrics
    for col in met_names:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # GA4 returns dates as YYYYMMDD strings
    if "date" in dim_names:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.date

    return df


def save_partitioned(df, name, data_dir):
    """Write a DataFrame to date-partitioned Parquet files.

    Writes one file per date under:
        <data_dir>/<name>/date=<YYYY-MM-DD>/<name>.parquet

    Existing partitions for a given date are overwritten, so re-running for an
    overlapping date range safely refreshes only the affected dates.

    Returns the number of date partitions written.
    """
    partitions_written = 0
    for date_val, partition_df in df.groupby("date"):
        partition_dir = data_dir / name / f"date={date_val}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(partition_df, preserve_index=False)
        pq.write_table(table, partition_dir / f"{name}.parquet", compression="zstd")
        partitions_written += 1
    return partitions_written


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download GA4 data to Parquet files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--property-id",
        default=os.environ.get("GA4_PROPERTY_ID"),
        help=(
            "GA4 property ID (digits only, e.g. 123456789). "
            "Defaults to GA4_PROPERTY_ID environment variable."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Number of days of history to download (default: {DEFAULT_DAYS}). Ignored if --start-date is set.",
    )
    parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format. Overrides --days.",
    )
    parser.add_argument(
        "--end-date",
        default="today",
        help="End date in YYYY-MM-DD format, or 'today' (default: today).",
    )
    parser.add_argument(
        "--filter",
        dest="filters",
        action="append",
        default=[],
        metavar="DIMENSION=VALUE",
        help=(
            "Filter rows by dimension value. Repeatable; all filters are AND-ed. "
            "Use = to include, != to exclude. "
            "Examples: --filter 'country=United States'  --filter 'sessionSource!=spam.com'"
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.property_id:
        raise SystemExit(
            "Error: GA4 property ID is required.\n"
            "Set the GA4_PROPERTY_ID environment variable or pass --property-id."
        )

    if args.start_date:
        start_date = args.start_date
    else:
        start_date = (datetime.today() - timedelta(days=args.days)).strftime("%Y-%m-%d")

    end_date = args.end_date

    filters = parse_filters(args.filters)
    filter_expression = build_filter_expression(filters)

    print(f"Property:   {args.property_id}")
    print(f"Date range: {start_date} → {end_date}")
    if filters:
        for dimension, value, exclude in filters:
            print(f"Filter:     {dimension} {'!=' if exclude else '='} {value}")
    print()

    DATA_DIR.mkdir(exist_ok=True)
    client = BetaAnalyticsDataClient()

    for report in REPORTS:
        name = report["name"]
        print(f"[{name}] {report['description']}")

        rows = fetch_report(client, args.property_id, report, start_date, end_date, filter_expression)
        df = rows_to_dataframe(rows, report)

        partitions = save_partitioned(df, name, DATA_DIR)

        print(f"  {len(df):,} rows → {DATA_DIR}/{name}/date=*/ ({partitions} partitions)")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
