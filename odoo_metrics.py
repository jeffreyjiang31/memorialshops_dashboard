"""
Odoo metrics computation. Returns structured data for API/frontend.
Credentials via environment variables: ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD
"""
import os
import xmlrpc.client
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# US Pacific Time: UTC-8 (standard) / UTC-7 (daylight).
# Using a fixed UTC-8 offset keeps it simple and avoids pytz dependency.
_PST = timezone(timedelta(hours=-8))


def _utc_to_local(dt_naive):
    """Treat a naive datetime as UTC and convert to PST (UTC-8)."""
    return dt_naive.replace(tzinfo=timezone.utc).astimezone(_PST)


def get_config():
    cfg = {
        "url": os.environ.get("ODOO_URL"),
        "db": os.environ.get("ODOO_DB"),
        "username": os.environ.get("ODOO_USERNAME"),
        "password": os.environ.get("ODOO_PASSWORD"),
    }
    missing = [k.upper() for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(
            "Missing required environment variables: "
            + ", ".join(f"ODOO_{m}" if not m.startswith("ODOO_") else m for m in missing)
        )
    return cfg


def fetch_metrics(config=None):
    if config is None:
        config = get_config()
    url = config["url"]
    db = config["db"]
    username = config["username"]
    password = config["password"]

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, password, {})
    if not uid:
        raise Exception("Authentication failed")

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    orders = models.execute_kw(
        db, uid, password,
        'sale.order', 'search_read',
        [[('state', 'in', ['sale', 'done'])]],
        {
            'fields': ['id', 'name', 'date_order', 'amount_total', 'partner_id'],
            'order': 'date_order asc'
        }
    )

    order_ids = [o['id'] for o in orders]
    total_orders = len(order_ids)

    margin_available = False
    if order_ids:
        line_model_fields = models.execute_kw(
            db, uid, password,
            'sale.order.line', 'fields_get',
            [],
            {'attributes': ['type']}
        )
        order_line_fields = ['order_id', 'product_id', 'product_uom_qty']
        if 'price_subtotal' in line_model_fields:
            order_line_fields.append('price_subtotal')
        if 'margin' in line_model_fields:
            order_line_fields.append('margin')
            margin_available = True
        if 'price_subtotal' in line_model_fields:
            order_line_fields.append('price_subtotal')
        if 'purchase_price' in line_model_fields:
            order_line_fields.append('purchase_price')
        if not margin_available and 'price_subtotal' in line_model_fields and 'purchase_price' in line_model_fields:
            margin_available = True

        order_lines = models.execute_kw(
            db, uid, password,
            'sale.order.line', 'search_read',
            [[('order_id', 'in', order_ids)]],
            {'fields': order_line_fields}
        )
    else:
        order_lines = []

    monthly_revenue = defaultdict(float)
    monthly_orders = defaultdict(int)
    monthly_customers = defaultdict(set)
    monthly_new_customers = defaultdict(list)
    monthly_engraved_orders = defaultdict(set)
    monthly_frequency_days_sum = defaultdict(float)
    monthly_frequency_pairs = defaultdict(int)

    customer_stats = defaultdict(lambda: {"orders": 0, "revenue": 0, "margin": 0, "last_order": None})
    customer_order_dates = defaultdict(list)

    first_order_date_by_customer = {}
    customer_name_map = {}
    order_to_customer = {}
    all_customers = set()
    total_sales = 0
    total_product_units_sold = 0
    total_engraved_units = 0
    orders_with_engraving = set()
    order_month_map = {}

    for order in orders:
        order_id = order['id']
        partner_id = order['partner_id'][0]
        partner_name = order['partner_id'][1]
        dt = _utc_to_local(datetime.strptime(order['date_order'], "%Y-%m-%d %H:%M:%S"))
        month_key = dt.strftime("%Y-%m")

        order_to_customer[order_id] = partner_id
        order_month_map[order_id] = month_key
        total_sales += order['amount_total']
        monthly_revenue[month_key] += order['amount_total']
        monthly_orders[month_key] += 1
        monthly_customers[month_key].add(partner_id)
        all_customers.add(partner_id)
        customer_name_map[partner_id] = partner_name
        customer_stats[partner_id]["orders"] += 1
        customer_stats[partner_id]["revenue"] += order['amount_total']
        customer_order_dates[partner_id].append(dt)
        last_order_dt = customer_stats[partner_id]["last_order"]
        if last_order_dt is None or dt > last_order_dt:
            customer_stats[partner_id]["last_order"] = dt

        prev_dt = first_order_date_by_customer.get(partner_id)
        if prev_dt is None or dt < prev_dt:
            first_order_date_by_customer[partner_id] = dt

    for partner_id, first_dt in first_order_date_by_customer.items():
        first_month = first_dt.strftime("%Y-%m")
        orders_count = customer_stats[partner_id]["orders"]
        monthly_new_customers[first_month].append({
            "name": customer_name_map[partner_id],
            "segment": _segment_from_orders(orders_count),
        })

    product_ids = list(set(line['product_id'][0] for line in order_lines))
    if product_ids:
        products = models.execute_kw(
            db, uid, password,
            'product.product', 'read',
            [product_ids],
            {'fields': ['default_code', 'name']}
        )
        product_map = {p['id']: p for p in products}
    else:
        product_map = {}

    order_lines_by_order = defaultdict(list)
    for line in order_lines:
        order_id = line['order_id'][0]
        product_id = line['product_id'][0]
        product_data = product_map.get(product_id, {})
        product_name = product_data.get('name', f"Product {product_id}")
        qty = line.get('product_uom_qty') or 0
        subtotal = line.get('price_subtotal') if 'price_subtotal' in line else None
        order_lines_by_order[order_id].append({
            "product_name": product_name,
            "qty": round(qty, 2),
            "subtotal": round(subtotal, 2) if subtotal is not None else None,
        })

    for line in order_lines:
        order_id = line['order_id'][0]
        partner_id = order_to_customer[order_id]
        month_key = order_month_map[order_id]
        product_id = line['product_id'][0]
        product_data = product_map.get(product_id, {})
        product_code = product_data.get('default_code')
        product_name = product_data.get('name', "")
        if margin_available:
            if 'margin' in line:
                line_margin = line.get('margin') or 0
            elif 'price_subtotal' in line and 'purchase_price' in line:
                qty = line.get('product_uom_qty') or 0
                line_margin = (line.get('price_subtotal') or 0) - ((line.get('purchase_price') or 0) * qty)
            else:
                line_margin = 0
            customer_stats[partner_id]["margin"] += line_margin

        if product_code == "ENG-SRV":
            total_engraved_units += line['product_uom_qty']
            orders_with_engraving.add(order_id)
            monthly_engraved_orders[month_key].add(order_id)
        elif "Fedex Ground" in product_name or "Standard Delivery" in product_name:
            continue
        else:
            total_product_units_sold += line['product_uom_qty']

    overall_aov = total_sales / total_orders if total_orders > 0 else 0
    engraving_rate = (len(orders_with_engraving) / total_orders * 100) if total_orders > 0 else 0
    users_count = sum(1 for stats in customer_stats.values() if stats["orders"] >= 2)

    sorted_customers = sorted(
        customer_stats.items(),
        key=lambda x: x[1]["revenue"],
        reverse=True
    )

    customer_avg_days_between_orders = {}
    customer_gap_count = {}
    all_repeat_customer_avg_days = []
    all_repeat_order_gaps = []
    for partner_id, order_dates in customer_order_dates.items():
        sorted_dates = sorted(order_dates)
        if len(sorted_dates) < 2:
            customer_avg_days_between_orders[partner_id] = None
            customer_gap_count[partner_id] = 0
            continue

        gaps = []
        for i in range(1, len(sorted_dates)):
            gap_days = (sorted_dates[i] - sorted_dates[i - 1]).total_seconds() / 86400
            gaps.append(gap_days)
            month_key = sorted_dates[i].strftime("%Y-%m")
            monthly_frequency_days_sum[month_key] += gap_days
            monthly_frequency_pairs[month_key] += 1

        avg_gap = sum(gaps) / len(gaps)
        customer_avg_days_between_orders[partner_id] = avg_gap
        customer_gap_count[partner_id] = len(gaps)
        all_repeat_customer_avg_days.append(avg_gap)
        all_repeat_order_gaps.extend(gaps)

    def format_customer_record(partner_id, stats):
        orders_count = stats["orders"]
        total_value = stats["revenue"]
        aov = total_value / orders_count if orders_count > 0 else 0
        last_order_dt = stats["last_order"]
        avg_days_between_orders = customer_avg_days_between_orders.get(partner_id)
        return {
            "name": customer_name_map.get(partner_id, f"Customer {partner_id}"),
            "orders": orders_count,
            "total_order_value": round(total_value, 2),
            "average_order_value": round(aov, 2),
            "total_margin": round(stats.get("margin", 0), 2),
            "last_order_date": last_order_dt.strftime("%Y-%m-%dT%H:%M:%S") if last_order_dt else None,
            "avg_days_between_orders": round(avg_days_between_orders, 2) if avg_days_between_orders is not None else None,
        }

    all_customer_records = [format_customer_record(partner_id, stats) for partner_id, stats in sorted_customers]
    top_customers = all_customer_records[:15]
    users_two_plus_records = [record for record in all_customer_records if record["orders"] >= 2]
    one_order_records = [record for record in all_customer_records if record["orders"] == 1]

    sorted_months = sorted(monthly_revenue.keys())
    previous_aov = None
    monthly_breakdown = []
    for month in sorted_months:
        revenue = monthly_revenue[month]
        orders_count = monthly_orders[month]
        aov = revenue / orders_count if orders_count > 0 else 0
        unique_count = len(monthly_customers[month])
        new_customers_list = monthly_new_customers[month]
        engraved_orders_month = len(monthly_engraved_orders[month])
        engraving_rate_month = (engraved_orders_month / orders_count * 100) if orders_count > 0 else 0

        if previous_aov is None:
            aov_growth_pct = None
            aov_growth_display = "N/A"
        else:
            aov_growth_pct = (((aov - previous_aov) / previous_aov) * 100) if previous_aov > 0 else 0
            aov_growth_display = f"{aov_growth_pct:.2f}%"

        new_customers_2_plus = sum(
            1 for c in new_customers_list
            if isinstance(c, dict) and c.get("segment") != "1 order"
        )
        monthly_breakdown.append({
            "month": month,
            "revenue": round(revenue, 2),
            "orders": orders_count,
            "unique_customers": unique_count,
            "new_customers": new_customers_list,
            "new_customers_count": len(new_customers_list),
            "new_customers_2_plus": new_customers_2_plus,
            "aov": round(aov, 2),
            "aov_growth_pct": round(aov_growth_pct, 2) if aov_growth_pct is not None else None,
            "aov_growth_display": aov_growth_display,
            "engraving_rate": round(engraving_rate_month, 2),
            "engraved_orders": engraved_orders_month,
        })
        previous_aov = aov

    unique_customer_count = len(all_customers)
    avg_customer_lifetime_revenue = (total_sales / unique_customer_count) if unique_customer_count > 0 else 0
    monthly_frequency_trend = [
        round(monthly_frequency_days_sum[m] / monthly_frequency_pairs[m], 2) if monthly_frequency_pairs[m] > 0 else None
        for m in sorted_months
    ]
    power_users_avg_days = [r["avg_days_between_orders"] for r in users_two_plus_records if r["avg_days_between_orders"] is not None]
    avg_days_power_users = (sum(power_users_avg_days) / len(power_users_avg_days)) if power_users_avg_days else None
    overall_avg_days_repeat_customers = (
        sum(all_repeat_customer_avg_days) / len(all_repeat_customer_avg_days)
        if all_repeat_customer_avg_days else None
    )
    overall_avg_days_between_orders = (
        sum(all_repeat_order_gaps) / len(all_repeat_order_gaps)
        if all_repeat_order_gaps else None
    )

    segment_definitions = [
        ("1 order", lambda n: n == 1),
        ("2-5 orders", lambda n: 2 <= n <= 5),
        ("6-20 orders", lambda n: 6 <= n <= 20),
        ("20+ orders", lambda n: n > 20),
    ]
    segment_agg = {label: {"segment": label, "customers": 0, "revenue": 0.0, "margin": 0.0} for label, _ in segment_definitions}
    for _, stats in customer_stats.items():
        orders_count = stats["orders"]
        for label, predicate in segment_definitions:
            if predicate(orders_count):
                segment_agg[label]["customers"] += 1
                segment_agg[label]["revenue"] += stats["revenue"]
                segment_agg[label]["margin"] += stats.get("margin", 0.0)
                break

    segment_rows = []
    repeat_revenue = 0.0
    repeat_margin = 0.0
    for label, predicate in segment_definitions:
        row = segment_agg[label]
        revenue = row["revenue"]
        margin = row["margin"]
        margin_pct = (margin / revenue * 100) if revenue > 0 else None
        customer_list = [
            {"name": r["name"], "orders": r["orders"], "total_order_value": r["total_order_value"]}
            for r in all_customer_records
            if predicate(r["orders"])
        ]
        segment_rows.append({
            "segment": label,
            "customers": row["customers"],
            "revenue": round(revenue, 2),
            "margin": round(margin, 2),
            "margin_pct": round(margin_pct, 2) if margin_pct is not None else None,
            "customer_list": customer_list,
        })
        if label != "1 order":
            repeat_revenue += revenue
            repeat_margin += margin
    repeat_revenue_share_pct = (repeat_revenue / total_sales * 100) if total_sales > 0 else None

    bills_data = _fetch_bills_for_orders(db, uid, password, models, orders, order_to_customer, customer_name_map)

    daily_sales = _daily_sales_for_chart(orders)
    _add_daily_bills_to_chart(daily_sales, bills_data)

    # Concentration & risk: top-N revenue, churn impact, 80% customer count
    concentration = _build_concentration(all_customer_records, total_sales)

    # Forecasting: run rate, growth, naive and trend-based short-term forecast
    chart_data = {
        "months": sorted_months,
        "revenues": [round(monthly_revenue[m], 2) for m in sorted_months],
        "orders": [monthly_orders[m] for m in sorted_months],
    }
    forecast = _build_forecast(daily_sales, chart_data)

    # Build monthly cumulative series for overview card drill-down charts
    customer_orders_by_month = defaultdict(lambda: defaultdict(int))
    for order in orders:
        dt = _utc_to_local(datetime.strptime(order['date_order'], "%Y-%m-%d %H:%M:%S"))
        m = dt.strftime("%Y-%m")
        pid = order['partner_id'][0]
        customer_orders_by_month[pid][m] += 1

    cum_customers = []
    cum_users_2plus = []
    cum_orders = []
    cum_revenue = []
    cum_clv = []
    seen_customers = set()
    running_orders_total = 0
    running_revenue_total = 0.0
    customer_cumulative_orders = defaultdict(int)
    for m in sorted_months:
        for pid in monthly_customers[m]:
            seen_customers.add(pid)
            customer_cumulative_orders[pid] += customer_orders_by_month[pid].get(m, 0)
        running_orders_total += monthly_orders[m]
        running_revenue_total += monthly_revenue[m]
        users_2plus = sum(1 for pid in seen_customers if customer_cumulative_orders[pid] >= 2)
        cum_customers.append(len(seen_customers))
        cum_users_2plus.append(users_2plus)
        cum_orders.append(running_orders_total)
        cum_revenue.append(round(running_revenue_total, 2))
        cum_clv.append(round(running_revenue_total / len(seen_customers), 2) if seen_customers else 0)

    overview_series = {
        "months": sorted_months,
        "cum_revenue": cum_revenue,
        "cum_customers": cum_customers,
        "cum_users_2plus": cum_users_2plus,
        "cum_orders": cum_orders,
        "cum_clv": cum_clv,
        "monthly_aov": [round(monthly_revenue[m] / monthly_orders[m], 2) if monthly_orders[m] > 0 else 0 for m in sorted_months],
        "monthly_engraving_rate": [mb["engraving_rate"] for mb in monthly_breakdown],
    }

    return {
        "master": {
            "total_sales": round(total_sales, 2),
            "total_orders": total_orders,
            "unique_customers": unique_customer_count,
            "avg_customer_lifetime_revenue": round(avg_customer_lifetime_revenue, 2),
            "users_count": users_count,
            "overall_aov": round(overall_aov, 2),
            "total_product_units_sold": int(total_product_units_sold),
            "total_engraved_units": int(total_engraved_units),
            "engraving_rate": round(engraving_rate, 2),
        },
        "top_customers": top_customers,
        "customer_lists": {
            "unique_customers": all_customer_records,
            "users_2_plus": users_two_plus_records,
            "one_order": one_order_records,
        },
        "order_frequency": {
            "one_time_customers": len(one_order_records),
            "power_users_count": len(users_two_plus_records),
            "avg_days_power_users": round(avg_days_power_users, 2) if avg_days_power_users is not None else None,
            "avg_days_repeat_customers": round(overall_avg_days_repeat_customers, 2) if overall_avg_days_repeat_customers is not None else None,
            "avg_days_between_orders_overall": round(overall_avg_days_between_orders, 2) if overall_avg_days_between_orders is not None else None,
            "trend_months": sorted_months,
            "trend_avg_days": monthly_frequency_trend,
            "trend_pairs": [monthly_frequency_pairs[m] for m in sorted_months],
        },
        "power_user_distribution": {
            "segments": segment_rows,
            "margin_available": margin_available,
            "repeat_revenue": round(repeat_revenue, 2),
            "repeat_revenue_share_pct": round(repeat_revenue_share_pct, 2) if repeat_revenue_share_pct is not None else None,
            "repeat_margin": round(repeat_margin, 2),
        },
        "monthly": monthly_breakdown,
        "chart": {
            "months": sorted_months,
            "revenues": [round(monthly_revenue[m], 2) for m in sorted_months],
            "orders": [monthly_orders[m] for m in sorted_months],
        },
        "sales": [
            _sale_row_with_segment(o, order_lines_by_order, order_to_customer, customer_stats)
            for o in sorted(orders, key=lambda x: x["date_order"], reverse=True)
        ],
        "daily_sales": daily_sales,
        "bills": bills_data,
        "concentration": concentration,
        "forecast": forecast,
        "overview_series": overview_series,
    }


def _build_concentration(all_customer_records, total_sales):
    """Build concentration and risk metrics from customer list (already sorted by revenue desc)."""
    total_sales = float(total_sales or 0)
    if not all_customer_records or total_sales <= 0:
        return {
            "top_1_revenue": 0,
            "top_1_pct": 0,
            "top_5_revenue": 0,
            "top_5_pct": 0,
            "top_10_revenue": 0,
            "top_10_pct": 0,
            "top_20_revenue": 0,
            "top_20_pct": 0,
            "revenue_if_top_1_churn": total_sales,
            "revenue_if_top_5_churn": total_sales,
            "impact_if_top_1_churn": 0,
            "impact_if_top_5_churn": 0,
            "customers_for_80_pct": 0,
            "top_accounts": [],
        }
    revenues = [float(r.get("total_order_value") or 0) for r in all_customer_records]
    top_1 = sum(revenues[:1])
    top_5 = sum(revenues[:5])
    top_10 = sum(revenues[:10])
    top_20 = sum(revenues[:20])
    pct = lambda x: round(100 * x / total_sales, 2) if total_sales else 0
    # Customers needed to reach 80% of revenue
    cumul = 0
    customers_for_80 = 0
    target_80 = 0.8 * total_sales
    for i, rev in enumerate(revenues):
        cumul += rev
        if cumul >= target_80:
            customers_for_80 = i + 1
            break
    if customers_for_80 == 0 and revenues:
        customers_for_80 = len(revenues)
    top_accounts = [
        {
            "rank": i + 1,
            "name": r.get("name") or "",
            "revenue": round(r.get("total_order_value") or 0, 2),
            "pct": pct(r.get("total_order_value") or 0),
        }
        for i, r in enumerate(all_customer_records[:20])
    ]
    return {
        "top_1_revenue": round(top_1, 2),
        "top_1_pct": pct(top_1),
        "top_5_revenue": round(top_5, 2),
        "top_5_pct": pct(top_5),
        "top_10_revenue": round(top_10, 2),
        "top_10_pct": pct(top_10),
        "top_20_revenue": round(top_20, 2),
        "top_20_pct": pct(top_20),
        "revenue_if_top_1_churn": round(total_sales - top_1, 2),
        "revenue_if_top_5_churn": round(total_sales - top_5, 2),
        "impact_if_top_1_churn": round(top_1, 2),
        "impact_if_top_5_churn": round(top_5, 2),
        "customers_for_80_pct": customers_for_80,
        "top_accounts": top_accounts,
    }


def _build_forecast(daily_sales, chart_data):
    """Build run rate, growth, and short-term revenue + profit forecast from daily and monthly series."""
    days = daily_sales.get("days") or []
    revenues = daily_sales.get("revenues") or []
    daily_bills = daily_sales.get("daily_bills") or []
    months = chart_data.get("months") or []
    monthly_revenues = chart_data.get("revenues") or []
    n = len(days)
    # Revenue windows
    last_30d = sum(revenues[-30:]) if n >= 30 else sum(revenues)
    last_90d = sum(revenues[-90:]) if n >= 90 else sum(revenues)
    prev_90d = sum(revenues[-180:-90]) if n >= 180 else 0
    last_12m = sum(monthly_revenues[-12:]) if len(monthly_revenues) >= 12 else sum(monthly_revenues)
    # COGS (bills) windows
    bills_30d = sum(daily_bills[-30:]) if len(daily_bills) >= 30 else sum(daily_bills)
    bills_90d = sum(daily_bills[-90:]) if len(daily_bills) >= 90 else sum(daily_bills)
    # Profit windows
    profit_30d = last_30d - bills_30d
    profit_90d = last_90d - bills_90d
    # Run rates
    run_rate_30d = last_30d * 12 if n >= 30 else None
    run_rate_90d = last_90d * 4 if n >= 90 else None
    profit_run_rate_30d = profit_30d * 12 if n >= 30 else None
    profit_run_rate_90d = profit_90d * 4 if n >= 90 else None
    # Profit margin %
    profit_margin_30d_pct = (profit_30d / last_30d * 100) if last_30d > 0 else None
    profit_margin_90d_pct = (profit_90d / last_90d * 100) if last_90d > 0 else None
    # Growth
    growth_90d_pct = ((last_90d - prev_90d) / prev_90d * 100) if n >= 180 and prev_90d and prev_90d > 0 else None
    # Naive: next period = last period
    forecast_next_30d_naive = last_30d
    forecast_next_90d_naive = last_90d
    # Trend: apply 90d growth to next 30d and 90d
    if growth_90d_pct is not None and n >= 90:
        factor_90 = 1 + (growth_90d_pct / 100)
        factor_30 = factor_90 ** (1 / 3)
        forecast_next_30d_trend = round(last_30d * factor_30, 2)
        forecast_next_90d_trend = round(last_90d * factor_90, 2)
    else:
        forecast_next_30d_trend = last_30d
        forecast_next_90d_trend = last_90d
    return {
        "last_30d_revenue": round(last_30d, 2),
        "last_90d_revenue": round(last_90d, 2),
        "prev_90d_revenue": round(prev_90d, 2) if prev_90d else 0,
        "last_12m_revenue": round(last_12m, 2),
        "last_30d_bills": round(bills_30d, 2),
        "last_90d_bills": round(bills_90d, 2),
        "last_30d_profit": round(profit_30d, 2),
        "last_90d_profit": round(profit_90d, 2),
        "profit_margin_30d_pct": round(profit_margin_30d_pct, 2) if profit_margin_30d_pct is not None else None,
        "profit_margin_90d_pct": round(profit_margin_90d_pct, 2) if profit_margin_90d_pct is not None else None,
        "run_rate_30d": round(run_rate_30d, 2) if run_rate_30d is not None else None,
        "run_rate_90d": round(run_rate_90d, 2) if run_rate_90d is not None else None,
        "profit_run_rate_30d": round(profit_run_rate_30d, 2) if profit_run_rate_30d is not None else None,
        "profit_run_rate_90d": round(profit_run_rate_90d, 2) if profit_run_rate_90d is not None else None,
        "growth_90d_pct": round(growth_90d_pct, 2) if growth_90d_pct is not None else None,
        "forecast_next_30d_naive": round(forecast_next_30d_naive, 2),
        "forecast_next_90d_naive": round(forecast_next_90d_naive, 2),
        "forecast_next_30d_trend": round(forecast_next_30d_trend, 2),
        "forecast_next_90d_trend": round(forecast_next_90d_trend, 2),
        "days_of_data": n,
    }


def _fetch_bills_for_orders(db, uid, password, models, orders, order_to_customer, customer_name_map):
    """Fetch vendor bills (in_invoice/in_refund) and link to sales orders via invoice_origin or purchase_id."""
    def _empty_bills():
        so_id_to_name = {o["id"]: (o.get("name") or "").strip() for o in orders}
        total_sales = sum(round(o.get("amount_total") or 0, 2) for o in orders)
        return {
            "order_bills": {},
            "summary": {
                "total_bill_amount": 0,
                "total_bills_count": 0,
                "orders_with_bills": 0,
                "orders_without_bills": len(orders),
                "total_orders": len(orders),
                "total_sales_linked": round(total_sales, 2),
                "cost_of_sales_ratio_pct": None,
            },
            "rows": [
                {
                    "order_id": o["id"],
                    "order_name": so_id_to_name.get(o["id"], ""),
                    "order_date": _utc_to_local(datetime.strptime(o["date_order"], "%Y-%m-%d %H:%M:%S")).strftime("%Y-%m-%dT%H:%M:%S") if o.get("date_order") else "",
                    "customer": customer_name_map.get(order_to_customer.get(o["id"]), ""),
                    "order_total": round(o.get("amount_total") or 0, 2),
                    "bill_count": 0,
                    "bills_total": 0,
                    "bills": [],
                    "margin_approx": None,
                }
                for o in sorted(orders, key=lambda x: (x.get("date_order") or "", x["id"]), reverse=True)
            ],
        }

    try:
        so_id_to_name = {o["id"]: (o.get("name") or "").strip() for o in orders}
        so_name_to_id = {name: oid for oid, name in so_id_to_name.items() if name}
        order_bills = defaultdict(list)
        bill_fields = ["id", "name", "ref", "invoice_origin", "partner_id", "amount_total", "state", "invoice_date", "move_type"]
        try:
            move_fields = models.execute_kw(db, uid, password, "account.move", "fields_get", [], {"attributes": ["type"]})
            if "payment_state" in move_fields:
                bill_fields.append("payment_state")
            if "amount_residual" in move_fields:
                bill_fields.append("amount_residual")
            if "purchase_id" in move_fields:
                bill_fields.append("purchase_id")
        except Exception:
            pass

        po_name_to_so_id = {}
        po_id_to_so_id = {}
        try:
            pos = models.execute_kw(
                db, uid, password,
                "purchase.order", "search_read",
                [[]],
                {"fields": ["id", "name", "origin"]}
            )
            for po in pos:
                origin = (po.get("origin") or "").strip()
                if origin in so_name_to_id:
                    po_name_to_so_id[(po.get("name") or "").strip()] = so_name_to_id[origin]
                    po_id_to_so_id[po["id"]] = so_name_to_id[origin]
        except Exception:
            pos = []

        try:
            moves = models.execute_kw(
                db, uid, password,
                "account.move", "search_read",
                [[("move_type", "in", ["in_invoice", "in_refund"])]],
                {"fields": bill_fields, "order": "invoice_date desc"}
            )
        except Exception:
            moves = []

        for m in moves:
            origin_ref = (m.get("invoice_origin") or m.get("ref") or "").strip()
            so_id = None
            if m.get("purchase_id") and po_id_to_so_id:
                so_id = po_id_to_so_id.get(m["purchase_id"][0])
            if so_id is None and origin_ref in so_name_to_id:
                so_id = so_name_to_id[origin_ref]
            if so_id is None and origin_ref in po_name_to_so_id:
                so_id = po_name_to_so_id[origin_ref]
            if so_id is None:
                continue
            partner = m.get("partner_id") or (0, "")
            vendor_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else str(partner)
            inv_date = m.get("invoice_date")
            if inv_date and isinstance(inv_date, str) and len(inv_date) > 10:
                inv_date = inv_date[:10]
            order_bills[so_id].append({
                "id": m["id"],
                "name": m.get("name") or "",
                "ref": m.get("ref") or "",
                "vendor": vendor_name,
                "amount_total": round(m.get("amount_total") or 0, 2),
                "amount_residual": round(m.get("amount_residual") or 0, 2) if "amount_residual" in m else None,
                "state": m.get("state") or "",
                "payment_state": m.get("payment_state") if m.get("payment_state") else None,
                "invoice_date": inv_date,
                "move_type": m.get("move_type") or "in_invoice",
            })

        total_bill_amount = 0.0
        total_bills_count = 0
        orders_with_bills = 0
        all_bills_flat = []
        for o in orders:
            oid = o["id"]
            so_name = so_id_to_name.get(oid, "")
            customer_id = order_to_customer.get(oid)
            customer_name = customer_name_map.get(customer_id, "") if customer_id else ""
            order_total = round(o.get("amount_total") or 0, 2)
            bills = order_bills.get(oid, [])
            bills_total = round(sum(b["amount_total"] for b in bills), 2)
            total_bill_amount += bills_total
            total_bills_count += len(bills)
            if bills:
                orders_with_bills += 1
            order_date = _utc_to_local(datetime.strptime(o["date_order"], "%Y-%m-%d %H:%M:%S")).strftime("%Y-%m-%dT%H:%M:%S") if o.get("date_order") else ""
            all_bills_flat.append({
                "order_id": oid,
                "order_name": so_name,
                "order_date": order_date,
                "customer": customer_name,
                "order_total": order_total,
                "bill_count": len(bills),
                "bills_total": bills_total,
                "bills": bills,
                "margin_approx": round(order_total - bills_total, 2) if bills_total else None,
            })

        all_bills_flat.sort(key=lambda x: (x["order_date"] or "", x["order_id"]), reverse=True)
        orders_without_bills = len(orders) - orders_with_bills
        total_sales_for_bills = sum(o["order_total"] for o in all_bills_flat)
        cost_ratio_pct = (total_bill_amount / total_sales_for_bills * 100) if total_sales_for_bills > 0 else None

        return {
            "order_bills": {k: v for k, v in order_bills.items()},
            "summary": {
                "total_bill_amount": round(total_bill_amount, 2),
                "total_bills_count": total_bills_count,
                "orders_with_bills": orders_with_bills,
                "orders_without_bills": orders_without_bills,
                "total_orders": len(orders),
                "total_sales_linked": round(total_sales_for_bills, 2),
                "cost_of_sales_ratio_pct": round(cost_ratio_pct, 2) if cost_ratio_pct is not None else None,
            },
            "rows": all_bills_flat,
        }
    except Exception:
        return _empty_bills()


def _segment_from_orders(orders_count):
    """Return segment label for a given order count (1 order, 2-5 orders, etc.)."""
    if orders_count == 1:
        return "1 order"
    if orders_count <= 5:
        return "2-5 orders"
    if orders_count <= 20:
        return "6-20 orders"
    return "20+ orders"


def _sale_row_with_segment(order, order_lines_by_order, order_to_customer, customer_stats):
    oid = order["id"]
    partner_id = order_to_customer.get(oid)
    orders_count = customer_stats.get(partner_id, {}).get("orders", 0) if partner_id else 0
    segment = _segment_from_orders(orders_count)
    return {
        "id": oid,
        "name": order.get("name") or "",
        "date": _utc_to_local(datetime.strptime(order["date_order"], "%Y-%m-%d %H:%M:%S")).strftime("%Y-%m-%dT%H:%M:%S"),
        "customer": order["partner_id"][1],
        "total": round(order["amount_total"], 2),
        "lines": order_lines_by_order.get(oid, []),
        "customer_orders": orders_count,
        "customer_segment": segment,
    }


def _daily_sales_for_chart(orders):
    """Aggregate orders by day (PST); fill gaps so every day from first order to today is present."""
    daily_revenue = defaultdict(float)
    for o in orders:
        dt = _utc_to_local(datetime.strptime(o["date_order"], "%Y-%m-%d %H:%M:%S"))
        day_key = dt.strftime("%Y-%m-%d")
        daily_revenue[day_key] += o["amount_total"]
    if not daily_revenue:
        return {"days": [], "revenues": [], "cumulative": []}
    first_day = datetime.strptime(min(daily_revenue.keys()), "%Y-%m-%d")
    today = _utc_to_local(datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    last_day = max(today, datetime.strptime(max(daily_revenue.keys()), "%Y-%m-%d").replace(tzinfo=today.tzinfo))
    all_days = []
    current = first_day.replace(tzinfo=today.tzinfo) if first_day.tzinfo is None else first_day
    while current <= last_day:
        all_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    revenues = [round(daily_revenue.get(d, 0.0), 2) for d in all_days]
    cumulative = []
    running = 0.0
    for r in revenues:
        running += r
        cumulative.append(round(running, 2))
    return {
        "days": all_days,
        "revenues": revenues,
        "cumulative": cumulative,
    }


def _add_daily_bills_to_chart(daily_sales, bills_data):
    """Add daily_bills and cumulative_bills to daily_sales, aligned by day."""
    rows = bills_data.get("rows") or []
    daily_bills_sum = defaultdict(float)
    for row in rows:
        day = (row.get("order_date") or "")[:10]
        if day:
            daily_bills_sum[day] += row.get("bills_total") or 0
    days = daily_sales.get("days") or []
    daily_bills = [round(daily_bills_sum[d], 2) for d in days]
    cumulative_bills = []
    running = 0.0
    for b in daily_bills:
        running += b
        cumulative_bills.append(round(running, 2))
    daily_sales["daily_bills"] = daily_bills
    daily_sales["cumulative_bills"] = cumulative_bills
