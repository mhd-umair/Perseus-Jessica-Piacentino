import json
import re
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "perseus_equipment_database.db"
STATIC_DIR = BASE_DIR / "static"
MIN_MONTH = "2017-04"
MAX_MONTH = "2026-03"
POSTED_STATUSES = ("finalized", "archived")
CAGR_MIN_START_MONTH = "2018-03"
MIN_TTM_CAGR_BASELINE_REVENUE = 10000
MIN_TTM_CAGR_INVOICE_COUNT = 3


def get_connection():
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def as_float(value):
    return float(value or 0)


def row_to_dict(row):
    result = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, bytes):
            value = value.hex()
        result[key] = value
    return result


def month_to_index(month):
    year, month_number = month.split("-")
    return int(year) * 12 + int(month_number) - 1


def index_to_month(index):
    year = index // 12
    month_number = index % 12 + 1
    return f"{year:04d}-{month_number:02d}"


def add_months(month, offset):
    return index_to_month(month_to_index(month) + offset)


def normalize_month(value, default):
    if not value:
        return default
    value = value[:7]
    if len(value) != 7 or value[4] != "-":
        return default
    try:
        month_index = month_to_index(value)
    except ValueError:
        return default
    return index_to_month(
        min(max(month_index, month_to_index(MIN_MONTH)), month_to_index(MAX_MONTH))
    )


def resolve_month_range(start_month=None, end_month=None):
    start = normalize_month(start_month, MIN_MONTH)
    end = normalize_month(end_month, MAX_MONTH)
    if month_to_index(start) > month_to_index(end):
        start, end = end, start
    return start, end


def month_labels(start_month, end_month):
    start_index = month_to_index(start_month)
    end_index = month_to_index(end_month)
    return [index_to_month(index) for index in range(start_index, end_index + 1)]


def month_span(start_month, end_month):
    return max(month_to_index(end_month) - month_to_index(start_month), 1)


def posted_month_filter(alias=""):
    prefix = f"{alias}." if alias else ""
    return (
        f"{prefix}Status in (?, ?) "
        f"and substr({prefix}ActivityDate, 1, 7) between ? and ?"
    )


def customer_summary_cte():
    return f"""
        with monthly as (
            select
                CustomerId,
                coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
                coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo,
                substr(ActivityDate, 1, 7) as Period,
                sum(coalesce(TotalInvoice, 0)) as Revenue,
                count(distinct InvoiceDocId) as InvoiceCount
            from InvoiceHeader
            where {posted_month_filter()}
            group by CustomerId, CustomerName, CustomerNo, Period
        ),
        summary as (
            select
                CustomerId,
                CustomerName,
                CustomerNo,
                sum(case when Period between ? and ? then Revenue else 0 end) as TotalRevenue,
                sum(case when Period between ? and ? then InvoiceCount else 0 end) as InvoiceCount,
                sum(case when Period = ? then Revenue else 0 end) as StartPeriodRevenue,
                sum(case when Period = ? then Revenue else 0 end) as EndPeriodRevenue,
                sum(case when Period = ? then InvoiceCount else 0 end) as StartPeriodInvoiceCount,
                sum(case when Period = ? then InvoiceCount else 0 end) as EndPeriodInvoiceCount,
                sum(case when Period between ? and ? then Revenue else 0 end) as StartTtmRevenue,
                sum(case when Period between ? and ? then Revenue else 0 end) as EndTtmRevenue,
                sum(case when Period between ? and ? then InvoiceCount else 0 end) as StartTtmInvoiceCount,
                sum(case when Period between ? and ? then InvoiceCount else 0 end) as EndTtmInvoiceCount,
                max(case when Period between ? and ? and Revenue > 0 then Period end) as LastRevenuePeriod,
                min(case when Period between ? and ? and Revenue > 0 then Period end) as FirstRevenuePeriod
            from monthly
            group by CustomerId, CustomerName, CustomerNo
        )
    """


def customer_summary_params(start_month, end_month):
    start_ttm_month = add_months(start_month, -11)
    end_ttm_month = add_months(end_month, -11)
    return [
        *POSTED_STATUSES,
        start_ttm_month,
        end_month,
        start_month,
        end_month,
        start_month,
        end_month,
        start_month,
        end_month,
        start_month,
        end_month,
        start_ttm_month,
        start_month,
        end_ttm_month,
        end_month,
        start_ttm_month,
        start_month,
        end_ttm_month,
        end_month,
        start_month,
        end_month,
        start_month,
        end_month,
    ]


def fetch_leaderboard(rank_by, limit, start_month, end_month):
    order_map = {
        "total_revenue": "TotalRevenue desc",
        "invoice_count": "InvoiceCount desc",
        "average_invoice_value": "AverageInvoiceValue desc",
        "latest_year_revenue": "EndRevenue desc",
        "cagr": "Cagr desc",
        "revenue_growth_dollars": "RevenueGrowthDollars desc",
    }
    order_by = order_map.get(rank_by, order_map["total_revenue"])
    sql = (
        customer_summary_cte()
        + f"""
        select
            CustomerId,
            CustomerName,
            CustomerNo,
            round(TotalRevenue, 2) as TotalRevenue,
            InvoiceCount,
            round(TotalRevenue / nullif(InvoiceCount, 0), 2) as AverageInvoiceValue,
            round(StartPeriodRevenue, 2) as StartRevenue,
            round(EndPeriodRevenue, 2) as EndRevenue,
            round(EndPeriodRevenue - StartPeriodRevenue, 2) as RevenueGrowthDollars,
            round(StartTtmRevenue, 2) as StartTtmRevenue,
            round(EndTtmRevenue, 2) as EndTtmRevenue,
            case
                when ? >= ?
                    and StartTtmRevenue >= ?
                    and EndTtmRevenue >= ?
                    and StartTtmInvoiceCount >= ?
                    and EndTtmInvoiceCount >= ?
                then round((pow(EndTtmRevenue / StartTtmRevenue, 12.0 / ?) - 1) * 100, 2)
            end as Cagr,
            LastRevenuePeriod,
            FirstRevenuePeriod
        from summary
        where TotalRevenue > 0
        order by {order_by}, CustomerName
        limit ?
        """
    )
    params = [
        *customer_summary_params(start_month, end_month),
        start_month,
        CAGR_MIN_START_MONTH,
        MIN_TTM_CAGR_BASELINE_REVENUE,
        MIN_TTM_CAGR_BASELINE_REVENUE,
        MIN_TTM_CAGR_INVOICE_COUNT,
        MIN_TTM_CAGR_INVOICE_COUNT,
        month_span(start_month, end_month),
        limit,
    ]
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [row_to_dict(row) for row in rows]


def fetch_periods(customer_ids=None, start_month=MIN_MONTH, end_month=MAX_MONTH):
    params = [*POSTED_STATUSES, start_month, end_month]
    where_customer = ""
    if customer_ids:
        placeholders = ",".join("?" for _ in customer_ids)
        where_customer = f"and CustomerId in ({placeholders})"
        params.extend(customer_ids)

    sql = f"""
        select
            CustomerId,
            coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
            coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo,
            substr(ActivityDate, 1, 7) as Period,
            round(sum(coalesce(TotalInvoice, 0)), 2) as Revenue,
            count(distinct InvoiceDocId) as InvoiceCount,
            round(sum(coalesce(TotalInvoice, 0)) / nullif(count(distinct InvoiceDocId), 0), 2)
                as AverageInvoiceValue
        from InvoiceHeader
        where {posted_month_filter()} {where_customer}
        group by CustomerId, CustomerName, CustomerNo, Period
        order by CustomerName, Period
    """
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [row_to_dict(row) for row in rows]


def fetch_concentration(start_month, end_month):
    sql = (
        customer_summary_cte()
        + """
        select
            CustomerId,
            CustomerName,
            TotalRevenue,
            sum(TotalRevenue) over () as AllRevenue,
            row_number() over (order by TotalRevenue desc) as RevenueRank
        from summary
        where TotalRevenue > 0
        """
    )
    with get_connection() as connection:
        rows = [
            row_to_dict(row)
            for row in connection.execute(sql, customer_summary_params(start_month, end_month)).fetchall()
        ]

    total_revenue = sum(as_float(row["TotalRevenue"]) for row in rows)

    def revenue_for_ranks(lo, hi):
        return sum(
            as_float(row["TotalRevenue"])
            for row in rows
            if lo <= as_float(row["RevenueRank"]) <= hi
        )

    # Mutually exclusive bands (partition total revenue) for doughnut chart / summary.
    top_5 = revenue_for_ranks(1, 5)
    ranks_6_10 = revenue_for_ranks(6, 10)
    ranks_11_20 = revenue_for_ranks(11, 20)
    total_rounded = round(total_revenue, 2)
    rev_top5 = round(top_5, 2)
    rev_6_10 = round(ranks_6_10, 2)
    rev_11_20 = round(ranks_11_20, 2)
    rev_other = round(max(total_rounded - rev_top5 - rev_6_10 - rev_11_20, 0), 2)

    buckets = [
        {
            "Label": "Top 5",
            "Revenue": rev_top5,
            "Share": round((rev_top5 / total_revenue) * 100, 2) if total_revenue else 0,
        },
        {
            "Label": "Ranks 6–10",
            "Revenue": rev_6_10,
            "Share": round((rev_6_10 / total_revenue) * 100, 2) if total_revenue else 0,
        },
        {
            "Label": "Ranks 11–20",
            "Revenue": rev_11_20,
            "Share": round((rev_11_20 / total_revenue) * 100, 2) if total_revenue else 0,
        },
        {
            "Label": "All Other Customers",
            "Revenue": rev_other,
            "Share": round((rev_other / total_revenue) * 100, 2) if total_revenue else 0,
        },
    ]
    return {"totalRevenue": round(total_revenue, 2), "buckets": buckets}


def fetch_retention(start_month, end_month):
    sql = (
        customer_summary_cte()
        + """
        select
            s.CustomerId,
            s.CustomerName,
            s.CustomerNo,
            round(s.TotalRevenue, 2) as TotalRevenue,
            round(s.EndPeriodRevenue, 2) as EndPeriodRevenue,
            round(s.StartPeriodRevenue, 2) as StartPeriodRevenue,
            round(s.EndPeriodRevenue - s.StartPeriodRevenue, 2) as RevenueGrowthDollars,
            s.LastRevenuePeriod,
            (
                select max(ActivityDate)
                from InvoiceHeader ih
                where ih.CustomerId = s.CustomerId
                    and ih.Status in (?, ?)
                    and substr(ih.ActivityDate, 1, 7) between ? and ?
                    and ih.ActivityDate is not null
            ) as LastPurchaseDate
        from summary s
        where s.TotalRevenue > 0
            and (s.EndPeriodRevenue = 0 or s.EndPeriodRevenue < s.StartPeriodRevenue)
        order by
            case when s.EndPeriodRevenue = 0 then 0 else 1 end,
            (s.EndPeriodRevenue - s.StartPeriodRevenue),
            s.TotalRevenue desc
        limit 25
        """
    )
    params = [*customer_summary_params(start_month, end_month), *POSTED_STATUSES, start_month, end_month]
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [row_to_dict(row) for row in rows]


def fetch_customer_detail(customer_id, start_month=MIN_MONTH, end_month=MAX_MONTH):
    periods = fetch_periods([customer_id], start_month, end_month)
    invoice_type_sql = f"""
        select
            coalesce(nullif(trim(InvoiceType), ''), 'unknown') as InvoiceType,
            round(sum(coalesce(TotalInvoice, 0)), 2) as Revenue,
            count(distinct InvoiceDocId) as InvoiceCount
        from InvoiceHeader
        where {posted_month_filter()} and CustomerId = ?
        group by InvoiceType
        order by Revenue desc
    """
    recent_sql = """
        select
            InvoiceDocId,
            coalesce(nullif(trim(InvoiceNo), ''), nullif(trim(DocNo), ''), cast(InvoiceDocId as text))
                as InvoiceNumber,
            ActivityDate,
            Status,
            InvoiceType,
            round(coalesce(TotalInvoice, 0), 2) as TotalInvoice,
            round(coalesce((
                select sum(coalesce(p.Amount, 0))
                from Payment p
                where p.InvoiceDocId = InvoiceHeader.InvoiceDocId
            ), 0), 2) as PaymentTotal,
            case
                when Status = 'voided' then 'Voided'
                when abs(coalesce(TotalInvoice, 0)) < 0.01 then 'No Charge'
                when coalesce((
                    select sum(coalesce(p.Amount, 0))
                    from Payment p
                    where p.InvoiceDocId = InvoiceHeader.InvoiceDocId
                ), 0) = 0 then 'Unpaid'
                when coalesce((
                    select sum(coalesce(p.Amount, 0))
                    from Payment p
                    where p.InvoiceDocId = InvoiceHeader.InvoiceDocId
                ), 0) > coalesce(TotalInvoice, 0) + 0.01 then 'Overpaid / Credit'
                when coalesce((
                    select sum(coalesce(p.Amount, 0))
                    from Payment p
                    where p.InvoiceDocId = InvoiceHeader.InvoiceDocId
                ), 0) >= coalesce(TotalInvoice, 0) - 0.01 then 'Paid'
                else 'Partially Paid'
            end as PaymentStatus
        from InvoiceHeader
        where Status in (?, ?)
            and substr(ActivityDate, 1, 7) between ? and ?
            and CustomerId = ?
            and (
                abs(coalesce(TotalInvoice, 0)) >= 0.01
                or exists (
                    select 1
                    from InvoiceDetail id
                    where id.InvoiceDocId = InvoiceHeader.InvoiceDocId
                )
            )
        order by ActivityDate desc
    """
    summary_sql = """
        select
            CustomerId,
            coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
            coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo,
            max(ActivityDate) as LastPurchaseDate,
            round(sum(coalesce(TotalInvoice, 0)), 2) as LifetimePostedRevenue,
            count(distinct InvoiceDocId) as LifetimePostedInvoiceCount
        from InvoiceHeader
        where Status in (?, ?) and substr(ActivityDate, 1, 7) between ? and ? and CustomerId = ?
        group by CustomerId, CustomerName, CustomerNo
    """
    with get_connection() as connection:
        invoice_types = [
            row_to_dict(row)
            for row in connection.execute(
                invoice_type_sql, [*POSTED_STATUSES, start_month, end_month, customer_id]
            ).fetchall()
        ]
        recent_invoices = [
            row_to_dict(row)
            for row in connection.execute(
                recent_sql, [*POSTED_STATUSES, start_month, end_month, customer_id]
            ).fetchall()
        ]
        summary_row = connection.execute(
            summary_sql, [*POSTED_STATUSES, start_month, end_month, customer_id]
        ).fetchone()
    return {
        "summary": row_to_dict(summary_row) if summary_row else None,
        "periods": periods,
        "invoiceTypes": invoice_types,
        "recentInvoices": recent_invoices,
    }


def fetch_dashboard(rank_by="total_revenue", limit=20, start_month=MIN_MONTH, end_month=MAX_MONTH):
    start_month, end_month = resolve_month_range(start_month, end_month)
    leaderboard = fetch_leaderboard(rank_by, limit, start_month, end_month)
    customer_ids = [row["CustomerId"] for row in leaderboard]
    return {
        "periods": month_labels(start_month, end_month),
        "settings": {
            "minMonth": MIN_MONTH,
            "maxMonth": MAX_MONTH,
            "startMonth": start_month,
            "endMonth": end_month,
            "cagrMinStartMonth": CAGR_MIN_START_MONTH,
            "postedStatuses": POSTED_STATUSES,
            "minTtmCagrBaselineRevenue": MIN_TTM_CAGR_BASELINE_REVENUE,
            "minTtmCagrInvoiceCount": MIN_TTM_CAGR_INVOICE_COUNT,
        },
        "leaderboard": leaderboard,
        "periodMetrics": fetch_periods(customer_ids, start_month, end_month),
        "concentration": fetch_concentration(start_month, end_month),
        "retention": fetch_retention(start_month, end_month),
    }


def fetch_parts_sales_kpis(start_month=MIN_MONTH, end_month=MAX_MONTH):
    start_month, end_month = resolve_month_range(start_month, end_month)
    sql = """
        select
            round(sum(coalesce(sp.NetExt, 0)), 2) as PartsRevenue,
            round(sum(coalesce(sp.Qty, 0)), 2) as QuantitySold,
            round(sum(coalesce(sp.NetExt, 0) - (coalesce(sp.AvgCost, 0) * coalesce(sp.Qty, 0))), 2)
                as EstimatedGrossMargin,
            count(distinct ih.InvoiceDocId) as PartsInvoiceCount
        from SalePart sp
        join InvoiceDetail id on id.ItemId = sp.ItemId
        join InvoiceHeader ih on ih.InvoiceDocId = id.InvoiceDocId
        where ih.Status in (?, ?)
            and substr(ih.ActivityDate, 1, 7) between ? and ?
    """
    with get_connection() as connection:
        row = connection.execute(sql, [*POSTED_STATUSES, start_month, end_month]).fetchone()

    parts_revenue = as_float(row["PartsRevenue"] if row else 0)
    estimated_margin = as_float(row["EstimatedGrossMargin"] if row else 0)
    invoice_count = int(row["PartsInvoiceCount"] if row and row["PartsInvoiceCount"] else 0)
    return {
        "startMonth": start_month,
        "endMonth": end_month,
        "partsRevenue": round(parts_revenue, 2),
        "quantitySold": as_float(row["QuantitySold"] if row else 0),
        "estimatedGrossMargin": round(estimated_margin, 2),
        "estimatedMarginPercent": round((estimated_margin / parts_revenue) * 100, 2)
        if parts_revenue
        else 0,
        "partsInvoiceCount": invoice_count,
        "averagePartsRevenuePerInvoice": round(parts_revenue / invoice_count, 2)
        if invoice_count
        else 0,
    }


def fetch_parts_sales_tables(start_month=MIN_MONTH, end_month=MAX_MONTH, limit=75):
    start_month, end_month = resolve_month_range(start_month, end_month)
    limit = min(max(int(limit), 1), 200)
    base_cte = """
        with part_sales as (
            select
                sp.PartId,
                sp.PartNo,
                coalesce(nullif(trim(sp.Description), ''), nullif(trim(pm.Description), ''), 'Unknown Part')
                    as Description,
                substr(ih.ActivityDate, 1, 7) as Period,
                ih.ActivityDate,
                ih.InvoiceDocId,
                coalesce(sp.Qty, 0) as Qty,
                coalesce(sp.NetExt, 0) as NetExt,
                coalesce(sp.AvgCost, 0) as AvgCost
            from SalePart sp
            join InvoiceDetail id on id.ItemId = sp.ItemId
            join InvoiceHeader ih on ih.InvoiceDocId = id.InvoiceDocId
            left join PartMaster pm on pm.PartId = sp.PartId
            where ih.Status in (?, ?)
                and substr(ih.ActivityDate, 1, 7) between ? and ?
        ),
        stocking_policy as (
            select
                PartId,
                max(coalesce(MinStock, 0)) as MinStock,
                max(coalesce(MaxStock, 0)) as MaxStock
            from PartLocation
            group by PartId
        ),
        part_summary as (
            select
                ps.PartId,
                ps.PartNo,
                ps.Description,
                round(sum(ps.Qty), 2) as QuantitySold,
                round(sum(ps.NetExt), 2) as PartsRevenue,
                round(sum(ps.NetExt - (ps.AvgCost * ps.Qty)), 2) as EstimatedGrossMargin,
                round(
                    sum(ps.NetExt - (ps.AvgCost * ps.Qty)) / nullif(sum(ps.NetExt), 0) * 100,
                    2
                ) as EstimatedMarginPercent,
                count(distinct ps.InvoiceDocId) as InvoiceCount,
                count(distinct ps.Period) as ActiveMonths,
                max(ps.ActivityDate) as LastSoldDate,
                coalesce(spol.MinStock, 0) as MinStock,
                coalesce(spol.MaxStock, 0) as MaxStock,
                case
                    when coalesce(spol.MinStock, 0) > 0 or coalesce(spol.MaxStock, 0) > 0
                    then 'Configured'
                    else 'Missing'
                end as StockingPolicyStatus
            from part_sales ps
            left join stocking_policy spol on spol.PartId = ps.PartId
            group by ps.PartId, ps.PartNo, ps.Description
        )
    """
    queries = {
        "topByRevenue": base_cte
        + """
            select *
            from part_summary
            where PartsRevenue > 0
            order by PartsRevenue desc, QuantitySold desc
            limit ?
        """,
        "topByQuantity": base_cte
        + """
            select *
            from part_summary
            where QuantitySold > 0
            order by QuantitySold desc, PartsRevenue desc
            limit ?
        """,
        "missingStockingPolicy": base_cte
        + """
            select *
            from part_summary
            where QuantitySold > 0 and StockingPolicyStatus = 'Missing'
            order by QuantitySold desc, PartsRevenue desc
            limit ?
        """,
    }
    params = [*POSTED_STATUSES, start_month, end_month, limit]
    with get_connection() as connection:
        return {
            "startMonth": start_month,
            "endMonth": end_month,
            "topByRevenue": [
                row_to_dict(row) for row in connection.execute(queries["topByRevenue"], params).fetchall()
            ],
            "topByQuantity": [
                row_to_dict(row) for row in connection.execute(queries["topByQuantity"], params).fetchall()
            ],
            "missingStockingPolicy": [
                row_to_dict(row)
                for row in connection.execute(queries["missingStockingPolicy"], params).fetchall()
            ],
        }


def fetch_part_trend(part_id, start_month=MIN_MONTH, end_month=MAX_MONTH):
    start_month, end_month = resolve_month_range(start_month, end_month)
    sql = """
        select
            sp.PartId,
            sp.PartNo,
            coalesce(nullif(trim(sp.Description), ''), nullif(trim(pm.Description), ''), 'Unknown Part')
                as Description,
            substr(ih.ActivityDate, 1, 7) as Period,
            round(sum(coalesce(sp.NetExt, 0)), 2) as PartsRevenue,
            round(sum(coalesce(sp.NetExt, 0) - (coalesce(sp.AvgCost, 0) * coalesce(sp.Qty, 0))), 2)
                as EstimatedGrossMargin,
            round(sum(coalesce(sp.Qty, 0)), 2) as QuantitySold,
            count(distinct ih.InvoiceDocId) as InvoiceCount
        from SalePart sp
        join InvoiceDetail id on id.ItemId = sp.ItemId
        join InvoiceHeader ih on ih.InvoiceDocId = id.InvoiceDocId
        left join PartMaster pm on pm.PartId = sp.PartId
        where sp.PartId = ?
            and ih.Status in (?, ?)
            and substr(ih.ActivityDate, 1, 7) between ? and ?
        group by
            sp.PartId,
            sp.PartNo,
            coalesce(nullif(trim(sp.Description), ''), nullif(trim(pm.Description), ''), 'Unknown Part'),
            Period
        order by Period
    """
    with get_connection() as connection:
        rows = [
            row_to_dict(row)
            for row in connection.execute(
                sql, [part_id, *POSTED_STATUSES, start_month, end_month]
            ).fetchall()
        ]
    part = None
    if rows:
        first = rows[0]
        part = {
            "partId": first["PartId"],
            "partNo": first["PartNo"],
            "description": first["Description"],
        }
    return {
        "startMonth": start_month,
        "endMonth": end_month,
        "periods": month_labels(start_month, end_month),
        "part": part,
        "series": rows,
    }


def money(value):
    return f"${as_float(value):,.2f}"


def find_customer(customer_id=None):
    if not customer_id:
        return None
    sql = """
        select
            CustomerId,
            coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
            coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo
        from InvoiceHeader
        where CustomerId = ?
        group by CustomerId, CustomerName, CustomerNo
        limit 1
    """
    with get_connection() as connection:
        row = connection.execute(sql, [customer_id]).fetchone()
    return row_to_dict(row) if row else None


def customer_name_tokens(value):
    stop_words = {
        "a",
        "about",
        "and",
        "biggest",
        "by",
        "change",
        "changed",
        "customer",
        "customers",
        "decline",
        "declined",
        "decrease",
        "did",
        "earn",
        "earned",
        "explain",
        "from",
        "generating",
        "growth",
        "had",
        "highest",
        "how",
        "in",
        "increase",
        "invoice",
        "invoices",
        "llc",
        "inc",
        "largest",
        "most",
        "much",
        "revenue",
        "sales",
        "spike",
        "top",
        "the",
        "was",
        "were",
        "what",
        "we",
        "why",
    }
    words = re.findall(r"[a-z0-9]+", value.lower())
    return [word for word in words if word not in stop_words and not re.fullmatch(r"\d{4}", word)]


def extract_year(value):
    match = re.search(r"\b(20\d{2}|19\d{2})\b", value)
    if not match:
        return None
    year = int(match.group(1))
    if year < 2017 or year > 2026:
        return None
    return year


def extract_customer_phrase(value):
    cleaned = re.sub(r"\b(20\d{2}|19\d{2})\b", " ", value, flags=re.IGNORECASE)
    match = re.search(
        r"(?:from|for|customer)\s+(.+?)(?:\s+(?:in|during|for|between|from)\b|[?.!]|$)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return cleaned


def find_customer_by_question(question):
    tokens = customer_name_tokens(extract_customer_phrase(question))
    if not tokens:
        return None, []

    where = " and ".join("lower(CustomerName) like ?" for _ in tokens)
    params = [f"%{token}%" for token in tokens]
    sql = f"""
        select
            CustomerId,
            coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
            coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo,
            count(distinct InvoiceDocId) as InvoiceCount,
            sum(coalesce(TotalInvoice, 0)) as TotalRevenue
        from InvoiceHeader
        where CustomerName is not null and {where}
        group by CustomerId, CustomerName, CustomerNo
        order by TotalRevenue desc
        limit 5
    """
    with get_connection() as connection:
        matches = [row_to_dict(row) for row in connection.execute(sql, params).fetchall()]
    return (matches[0] if matches else None), matches


def fetch_customer_revenue_for_period(customer_id, start_month, end_month):
    sql = """
        select
            round(sum(coalesce(TotalInvoice, 0)), 2) as PostedRevenue,
            count(distinct InvoiceDocId) as InvoiceCount,
            max(ActivityDate) as LastPurchaseDate
        from InvoiceHeader
        where CustomerId = ?
            and Status in (?, ?)
            and substr(ActivityDate, 1, 7) between ? and ?
    """
    with get_connection() as connection:
        row = connection.execute(
            sql, [customer_id, *POSTED_STATUSES, start_month, end_month]
        ).fetchone()
    return row_to_dict(row) if row else None


def fetch_customer_ranking(metric, start_month, end_month, limit=5, descending=True):
    metric_sql = {
        "revenue": "sum(coalesce(TotalInvoice, 0))",
        "invoice_count": "count(distinct InvoiceDocId)",
        "average_invoice": "sum(coalesce(TotalInvoice, 0)) / nullif(count(distinct InvoiceDocId), 0)",
    }.get(metric, "sum(coalesce(TotalInvoice, 0))")
    direction = "desc" if descending else "asc"
    sql = f"""
        select
            CustomerId,
            coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
            coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo,
            round(sum(coalesce(TotalInvoice, 0)), 2) as TotalRevenue,
            count(distinct InvoiceDocId) as InvoiceCount,
            round(sum(coalesce(TotalInvoice, 0)) / nullif(count(distinct InvoiceDocId), 0), 2)
                as AverageInvoiceValue
        from InvoiceHeader
        where Status in (?, ?) and substr(ActivityDate, 1, 7) between ? and ?
        group by CustomerId, CustomerName, CustomerNo
        having TotalRevenue > 0
        order by {metric_sql} {direction}, CustomerName
        limit ?
    """
    with get_connection() as connection:
        rows = connection.execute(
            sql, [*POSTED_STATUSES, start_month, end_month, limit]
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def fetch_top_customer_invoices(customer_id, start_month, end_month, limit=5):
    sql = """
        select
            InvoiceDocId,
            coalesce(nullif(trim(InvoiceNo), ''), nullif(trim(DocNo), ''), cast(InvoiceDocId as text))
                as InvoiceNumber,
            ActivityDate,
            InvoiceType,
            round(coalesce(TotalInvoice, 0), 2) as TotalInvoice
        from InvoiceHeader
        where CustomerId = ?
            and Status in (?, ?)
            and substr(ActivityDate, 1, 7) between ? and ?
            and abs(coalesce(TotalInvoice, 0)) >= 0.01
        order by TotalInvoice desc
        limit ?
    """
    with get_connection() as connection:
        rows = connection.execute(
            sql, [customer_id, *POSTED_STATUSES, start_month, end_month, limit]
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def fetch_lost_customers(year, limit=10):
    previous_start = f"{year - 1}-01"
    previous_end = f"{year - 1}-12"
    current_start = f"{year}-01"
    current_end = f"{year}-12"
    sql = """
        with previous_year as (
            select
                CustomerId,
                coalesce(nullif(trim(CustomerName), ''), 'Unknown Customer') as CustomerName,
                coalesce(nullif(trim(CustomerNo), ''), '') as CustomerNo,
                round(sum(coalesce(TotalInvoice, 0)), 2) as PreviousRevenue,
                count(distinct InvoiceDocId) as PreviousInvoiceCount
            from InvoiceHeader
            where Status in (?, ?) and substr(ActivityDate, 1, 7) between ? and ?
            group by CustomerId, CustomerName, CustomerNo
            having PreviousRevenue > 0
        ),
        current_year as (
            select CustomerId, round(sum(coalesce(TotalInvoice, 0)), 2) as CurrentRevenue
            from InvoiceHeader
            where Status in (?, ?) and substr(ActivityDate, 1, 7) between ? and ?
            group by CustomerId
        )
        select
            py.CustomerId,
            py.CustomerName,
            py.CustomerNo,
            py.PreviousRevenue,
            py.PreviousInvoiceCount,
            coalesce(cy.CurrentRevenue, 0) as CurrentRevenue
        from previous_year py
        left join current_year cy on cy.CustomerId = py.CustomerId
        where coalesce(cy.CurrentRevenue, 0) = 0
        order by py.PreviousRevenue desc
        limit ?
    """
    params = [
        *POSTED_STATUSES,
        previous_start,
        previous_end,
        *POSTED_STATUSES,
        current_start,
        current_end,
        limit,
    ]
    with get_connection() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [row_to_dict(row) for row in rows]


def fetch_customer_yoy(customer_id, year):
    previous_start = f"{year - 1}-01"
    previous_end = f"{year - 1}-12"
    current_start = f"{year}-01"
    current_end = f"{year}-12"
    sql = """
        select
            round(sum(case when substr(ActivityDate, 1, 7) between ? and ? then coalesce(TotalInvoice, 0) else 0 end), 2)
                as PreviousRevenue,
            count(distinct case when substr(ActivityDate, 1, 7) between ? and ? then InvoiceDocId end)
                as PreviousInvoiceCount,
            round(sum(case when substr(ActivityDate, 1, 7) between ? and ? then coalesce(TotalInvoice, 0) else 0 end), 2)
                as CurrentRevenue,
            count(distinct case when substr(ActivityDate, 1, 7) between ? and ? then InvoiceDocId end)
                as CurrentInvoiceCount
        from InvoiceHeader
        where CustomerId = ?
            and Status in (?, ?)
            and substr(ActivityDate, 1, 7) between ? and ?
    """
    params = [
        previous_start,
        previous_end,
        previous_start,
        previous_end,
        current_start,
        current_end,
        current_start,
        current_end,
        customer_id,
        *POSTED_STATUSES,
        previous_start,
        current_end,
    ]
    with get_connection() as connection:
        row = connection.execute(sql, params).fetchone()
    return row_to_dict(row) if row else None


def answer_customer_chat(question, start_month=MIN_MONTH, end_month=MAX_MONTH, customer_id=None):
    start_month, end_month = resolve_month_range(start_month, end_month)
    text = (question or "").strip()
    lowered = text.lower()
    suggestions = [
        "Who are the top customers?",
        "Which customers are declining?",
        "How many active customers are in this period?",
        "What is the revenue concentration?",
    ]

    if not text:
        return {
            "answer": "Ask me about top customers, active customers, revenue concentration, declining customers, or the currently selected customer.",
            "suggestions": suggestions,
        }

    if any(word in lowered for word in ("help", "what can", "examples")):
        return {
            "answer": (
                "I can answer customer analytics questions from the SQLite database using the "
                f"current period, {start_month} through {end_month}. Try asking about top customers, "
                "active customer counts, declining customers, revenue concentration, or the selected customer."
            ),
            "suggestions": suggestions,
        }

    year = extract_year(text)
    asks_revenue = any(word in lowered for word in ("revenue", "earn", "earned", "sales", "sold"))
    asks_ranking = any(word in lowered for word in ("top", "best", "largest", "highest", "most"))
    asks_lowest = any(word in lowered for word in ("bottom", "lowest", "least", "smallest"))

    asks_lost = any(word in lowered for word in ("lost", "lose", "churn", "dormant"))
    if year and asks_lost and "customer" in lowered:
        rows = fetch_lost_customers(year, 10)
        if not rows:
            return {
                "answer": f"I did not find customers with posted revenue in {year - 1} and no posted revenue in {year}.",
                "suggestions": suggestions,
            }
        lines = [
            f"{idx}. {row['CustomerName']}: {money(row['PreviousRevenue'])} in {year - 1}, {row['PreviousInvoiceCount']:,} invoices"
            for idx, row in enumerate(rows, start=1)
        ]
        return {
            "answer": (
                f"Customers lost in {year}, defined as revenue in {year - 1} and no posted revenue in {year}, are:\n"
                + "\n".join(lines)
            ),
            "suggestions": suggestions,
        }

    asks_top_invoices = (
        year
        and "invoice" in lowered
        and any(word in lowered for word in ("biggest", "largest", "top", "highest"))
    )
    if asks_top_invoices:
        matched_customer, matches = find_customer_by_question(text)
        if not matched_customer:
            return {"answer": "we can't answer that right now. try another question!", "suggestions": suggestions}
        rows = fetch_top_customer_invoices(matched_customer["CustomerId"], f"{year}-01", f"{year}-12", 5)
        if not rows:
            return {
                "answer": f"I did not find posted invoices for {matched_customer['CustomerName']} in {year}.",
                "suggestions": suggestions,
            }
        lines = [
            f"{idx}. Invoice {row['InvoiceNumber']} on {row['ActivityDate'][:10]} ({row['InvoiceType']}): {money(row['TotalInvoice'])}"
            for idx, row in enumerate(rows, start=1)
        ]
        return {
            "answer": f"Top invoices for {matched_customer['CustomerName']} in {year} are:\n" + "\n".join(lines),
            "suggestions": suggestions,
        }

    asks_yoy_explanation = (
        year
        and any(word in lowered for word in ("why", "explain", "change", "changed", "decline", "declined", "growth", "grew", "increase", "decrease"))
        and asks_revenue
    )
    if asks_yoy_explanation:
        matched_customer, matches = find_customer_by_question(text)
        if not matched_customer:
            return {"answer": "we can't answer that right now. try another question!", "suggestions": suggestions}
        metrics = fetch_customer_yoy(matched_customer["CustomerId"], year)
        previous_revenue = as_float(metrics["PreviousRevenue"] if metrics else 0)
        current_revenue = as_float(metrics["CurrentRevenue"] if metrics else 0)
        previous_count = int(metrics["PreviousInvoiceCount"] if metrics else 0)
        current_count = int(metrics["CurrentInvoiceCount"] if metrics else 0)
        revenue_change = current_revenue - previous_revenue
        pct_change = (revenue_change / previous_revenue * 100) if previous_revenue else None
        previous_avg = previous_revenue / previous_count if previous_count else 0
        current_avg = current_revenue / current_count if current_count else 0
        count_change = current_count - previous_count
        avg_change = current_avg - previous_avg
        pct_text = f" ({pct_change:.1f}%)" if pct_change is not None else ""
        direction = "increased" if revenue_change >= 0 else "decreased"
        driver_parts = [
            f"invoice count changed from {previous_count:,} to {current_count:,} ({count_change:+,})",
            f"average invoice changed from {money(previous_avg)} to {money(current_avg)} ({money(avg_change)})",
        ]
        return {
            "answer": (
                f"{matched_customer['CustomerName']} revenue {direction} by {money(abs(revenue_change))}{pct_text} "
                f"from {year - 1} to {year}: {money(previous_revenue)} to {money(current_revenue)}.\n"
                + "\n".join(driver_parts)
            ),
            "suggestions": suggestions,
        }

    if asks_ranking or asks_lowest:
        period_start = f"{year}-01" if year else start_month
        period_end = f"{year}-12" if year else end_month
        if "invoice" in lowered and not asks_revenue:
            metric = "invoice_count"
            metric_label = "posted invoice count"
        elif "average" in lowered or "avg" in lowered:
            metric = "average_invoice"
            metric_label = "average posted invoice value"
        else:
            metric = "revenue"
            metric_label = "posted revenue"
        rows = fetch_customer_ranking(metric, period_start, period_end, 5, descending=not asks_lowest)
        if not rows:
            return {
                "answer": f"I did not find posted customer activity from {period_start} through {period_end}.",
                "suggestions": suggestions,
            }
        top_row = rows[0]
        if re.search(r"\b(who|which)\b", lowered) and not re.search(r"\btop\s+\d+", lowered):
            if metric == "invoice_count":
                value = f"{top_row['InvoiceCount']:,} posted invoices"
            elif metric == "average_invoice":
                value = f"{money(top_row['AverageInvoiceValue'])} average posted invoice value"
            else:
                value = f"{money(top_row['TotalRevenue'])} in posted revenue"
            return {
                "answer": (
                    f"The {'lowest' if asks_lowest else 'top'} customer by {metric_label} "
                    f"from {period_start} through {period_end} was {top_row['CustomerName']}, "
                    f"with {value}."
                ),
                "suggestions": suggestions,
            }
        lines = [
            f"{idx}. {row['CustomerName']}: {money(row['TotalRevenue'])}, {row['InvoiceCount']:,} invoices, {money(row['AverageInvoiceValue'])} avg invoice"
            for idx, row in enumerate(rows, start=1)
        ]
        return {
            "answer": (
                f"{'Lowest' if asks_lowest else 'Top'} customers by {metric_label} "
                f"from {period_start} through {period_end} are:\n" + "\n".join(lines)
            ),
            "suggestions": suggestions,
        }

    if year and asks_revenue:
        matched_customer, matches = find_customer_by_question(text)
        if not matched_customer:
            return {
                "answer": "we can't answer that right now. try another question!",
                "suggestions": suggestions,
            }
        period_start = f"{year}-01"
        period_end = f"{year}-12"
        metrics = fetch_customer_revenue_for_period(
            matched_customer["CustomerId"], period_start, period_end
        )
        revenue = metrics["PostedRevenue"] if metrics else 0
        invoice_count = metrics["InvoiceCount"] if metrics else 0
        answer = (
            f"{matched_customer['CustomerName']} generated {money(revenue)} in posted revenue "
            f"during {year}, across {invoice_count:,} finalized/archived invoices."
        )
        if len(matches) > 1:
            other_matches = ", ".join(match["CustomerName"] for match in matches[1:3])
            answer += f" I matched this to the closest customer name; other possible matches include {other_matches}."
        return {"answer": answer, "suggestions": suggestions}

    if "active" in lowered and "customer" in lowered:
        sql = """
            select
                count(distinct CustomerId) as ActiveCustomers,
                count(distinct InvoiceDocId) as PostedInvoices,
                round(sum(coalesce(TotalInvoice, 0)), 2) as PostedRevenue
            from InvoiceHeader
            where Status in (?, ?) and substr(ActivityDate, 1, 7) between ? and ?
        """
        with get_connection() as connection:
            row = connection.execute(sql, [*POSTED_STATUSES, start_month, end_month]).fetchone()
        return {
            "answer": (
                f"From {start_month} through {end_month}, there were "
                f"{row['ActiveCustomers']:,} active customers with at least one posted invoice. "
                f"Those customers generated {row['PostedInvoices']:,} posted invoices and "
                f"{money(row['PostedRevenue'])} in posted revenue."
            ),
            "suggestions": suggestions,
        }

    if any(word in lowered for word in ("declin", "dormant", "at risk", "lost")):
        rows = fetch_retention(start_month, end_month)[:5]
        if not rows:
            answer = "I did not find declining or dormant customers for the selected period."
        else:
            lines = [
                f"{idx}. {row['CustomerName']}: {money(row['RevenueGrowthDollars'])} growth, last purchase {row['LastPurchaseDate'][:10] if row['LastPurchaseDate'] else 'N/A'}"
                for idx, row in enumerate(rows, start=1)
            ]
            answer = "The most notable declining or dormant customers are:\n" + "\n".join(lines)
        return {"answer": answer, "suggestions": suggestions}

    if any(word in lowered for word in ("concentration", "concentrated", "share", "depend")):
        concentration = fetch_concentration(start_month, end_month)
        lines = [
            f"{bucket['Label']}: {money(bucket['Revenue'])}, {bucket['Share']}%"
            for bucket in concentration["buckets"]
        ]
        return {
            "answer": (
                f"Total posted customer revenue for {start_month} through {end_month} is "
                f"{money(concentration['totalRevenue'])}.\n" + "\n".join(lines)
            ),
            "suggestions": suggestions,
        }

    selected_customer = find_customer(customer_id)
    asks_selected_customer = selected_customer and any(
        phrase in lowered
        for phrase in ("this customer", "selected customer", "current customer", "their", "customer")
    )
    if asks_selected_customer:
        detail = fetch_customer_detail(customer_id, start_month, end_month)
        summary = detail["summary"]
        if not summary:
            answer = "The selected customer does not have posted invoices in the current period."
        else:
            answer = (
                f"{summary['CustomerName']} generated {money(summary['LifetimePostedRevenue'])} "
                f"across {summary['LifetimePostedInvoiceCount']:,} posted invoices from "
                f"{start_month} through {end_month}. Last purchase was "
                f"{summary['LastPurchaseDate'][:10] if summary['LastPurchaseDate'] else 'N/A'}."
            )
        return {"answer": answer, "suggestions": suggestions}

    if any(word in lowered for word in ("top", "best", "largest", "highest")):
        rows = fetch_leaderboard("total_revenue", 5, start_month, end_month)
        lines = [
            f"{idx}. {row['CustomerName']}: {money(row['TotalRevenue'])}, {row['InvoiceCount']:,} invoices"
            for idx, row in enumerate(rows, start=1)
        ]
        return {
            "answer": (
                f"Top customers by posted revenue from {start_month} through {end_month} are:\n"
                + "\n".join(lines)
            ),
            "suggestions": suggestions,
        }

    return {
        "answer": "we can't answer that right now. try another question!",
        "suggestions": suggestions,
    }


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed_path = urlparse(path).path
        if parsed_path == "/":
            return str(STATIC_DIR / "index.html")
        return str(STATIC_DIR / parsed_path.lstrip("/"))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message, status=500):
        self.send_json({"error": message}, status=status)

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return super().do_GET()

        try:
            query = parse_qs(parsed.query)
            if parsed.path == "/api/customer-dashboard":
                rank_by = query.get("rankBy", ["total_revenue"])[0]
                limit = min(max(int(query.get("limit", ["20"])[0]), 1), 50)
                start_month = query.get("startMonth", [MIN_MONTH])[0]
                end_month = query.get("endMonth", [MAX_MONTH])[0]
                return self.send_json(fetch_dashboard(rank_by, limit, start_month, end_month))
            if parsed.path == "/api/parts-sales-kpis":
                start_month = query.get("startMonth", [MIN_MONTH])[0]
                end_month = query.get("endMonth", [MAX_MONTH])[0]
                return self.send_json(fetch_parts_sales_kpis(start_month, end_month))
            if parsed.path == "/api/parts-sales-tables":
                start_month = query.get("startMonth", [MIN_MONTH])[0]
                end_month = query.get("endMonth", [MAX_MONTH])[0]
                limit = min(max(int(query.get("limit", ["75"])[0]), 1), 200)
                return self.send_json(fetch_parts_sales_tables(start_month, end_month, limit))
            if parsed.path == "/api/part-trend":
                part_id = int(query.get("partId", ["0"])[0] or 0)
                if part_id <= 0:
                    return self.send_error_json("partId is required", status=400)
                start_month = query.get("startMonth", [MIN_MONTH])[0]
                end_month = query.get("endMonth", [MAX_MONTH])[0]
                return self.send_json(fetch_part_trend(part_id, start_month, end_month))
            if parsed.path == "/api/customer-detail":
                customer_id = int(query.get("customerId", ["0"])[0])
                if customer_id <= 0:
                    return self.send_error_json("customerId is required", status=400)
                start_month = query.get("startMonth", [MIN_MONTH])[0]
                end_month = query.get("endMonth", [MAX_MONTH])[0]
                start_month, end_month = resolve_month_range(start_month, end_month)
                return self.send_json(fetch_customer_detail(customer_id, start_month, end_month))
            if parsed.path == "/api/customer-chat":
                question = query.get("q", [""])[0]
                start_month = query.get("startMonth", [MIN_MONTH])[0]
                end_month = query.get("endMonth", [MAX_MONTH])[0]
                customer_id = int(query.get("customerId", ["0"])[0] or 0)
                return self.send_json(
                    answer_customer_chat(question, start_month, end_month, customer_id or None)
                )
            return self.send_error_json("API endpoint not found", status=404)
        except Exception as exc:
            return self.send_error_json(str(exc), status=500)


def main():
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")
    server = ThreadingHTTPServer(("127.0.0.1", 8000), DashboardRequestHandler)
    print("Customer Insights Dashboard running at http://127.0.0.1:8000")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
