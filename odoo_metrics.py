"""
Odoo metrics computation. Returns structured data for API/frontend.
Credentials via environment variables: ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD
"""
import os
import xmlrpc.client
from datetime import datetime
from collections import defaultdict


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
            'fields': ['id', 'date_order', 'amount_total', 'partner_id'],
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
        dt = datetime.strptime(order['date_order'], "%Y-%m-%d %H:%M:%S")
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
        monthly_new_customers[first_month].append(customer_name_map[partner_id])

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
            "last_order_date": last_order_dt.strftime("%Y-%m-%d") if last_order_dt else None,
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
        new_customer_names = monthly_new_customers[month]
        engraving_rate_month = (len(monthly_engraved_orders[month]) / orders_count * 100) if orders_count > 0 else 0

        if previous_aov is None:
            aov_growth_pct = None
            aov_growth_display = "N/A"
        else:
            aov_growth_pct = (((aov - previous_aov) / previous_aov) * 100) if previous_aov > 0 else 0
            aov_growth_display = f"{aov_growth_pct:.2f}%"

        monthly_breakdown.append({
            "month": month,
            "revenue": round(revenue, 2),
            "orders": orders_count,
            "unique_customers": unique_count,
            "new_customers": new_customer_names,
            "new_customers_count": len(new_customer_names),
            "aov": round(aov, 2),
            "aov_growth_pct": round(aov_growth_pct, 2) if aov_growth_pct is not None else None,
            "aov_growth_display": aov_growth_display,
            "engraving_rate": round(engraving_rate_month, 2),
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
    for label, _ in segment_definitions:
        row = segment_agg[label]
        revenue = row["revenue"]
        margin = row["margin"]
        margin_pct = (margin / revenue * 100) if revenue > 0 else None
        segment_rows.append({
            "segment": label,
            "customers": row["customers"],
            "revenue": round(revenue, 2),
            "margin": round(margin, 2),
            "margin_pct": round(margin_pct, 2) if margin_pct is not None else None,
        })
        if label != "1 order":
            repeat_revenue += revenue
            repeat_margin += margin
    repeat_revenue_share_pct = (repeat_revenue / total_sales * 100) if total_sales > 0 else None

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
    }
