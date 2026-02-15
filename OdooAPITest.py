"""
CLI entry point: run this file to print metrics to the console.
For the web dashboard, run: flask --app app run
"""
from odoo_metrics import fetch_metrics

data = fetch_metrics()
m = data["master"]
customers = data["top_customers"]
monthly = data["monthly"]

print("\n==========================================")
print("        Memorial Shops Master Metrics")
print("==========================================")
print(f"Total Lifetime Sales: ${m['total_sales']:,.2f}")
print(f"Total Orders: {m['total_orders']}")
print(f"Total Unique Customers: {m['unique_customers']}")
print(f"Avg Customer Lifetime Revenue: ${m['avg_customer_lifetime_revenue']:,.2f}")
print(f"Users (2+ Orders): {m['users_count']}")
print(f"Overall AOV: ${m['overall_aov']:.2f}")
print(f"Total Product Units Sold (excl. engraving + shipping): {m['total_product_units_sold']}")
print(f"Total Engraved Units Sold: {m['total_engraved_units']}")
print(f"Overall Engraving Rate: {m['engraving_rate']:.2f}%")
print("==========================================\n")

print("Top 15 Customers by Revenue:\n")
for c in customers:
    print(f"{c['name']}")
    print(f"  Orders Placed: {c['orders']}")
    print(f"  Total Order Value: ${c['revenue']:,.2f}")
    print(f"  Engraving % of Orders: {c['engraving_pct']:.2f}%\n")

print("Monthly Breakdown:\n")
for mo in monthly:
    print(f"{mo['month']}")
    print(f"  Revenue: ${mo['revenue']:,.2f}")
    print(f"  Orders: {mo['orders']}")
    print(f"  Unique Customers: {mo['unique_customers']}")
    print(f"  New Customers: {mo['new_customers_count']}")
    if mo['new_customers']:
        print("    New Customer List:")
        for name in mo['new_customers']:
            print(f"      - {name}")
    print(f"  AOV: ${mo['aov']:,.2f}")
    print(f"  AOV MoM Growth: {mo['aov_growth_display']}")
    print(f"  Engraving Rate: {mo['engraving_rate']:.2f}%\n")
