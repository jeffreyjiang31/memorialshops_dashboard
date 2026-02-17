"""
Memorial Shops Odoo Metrics – Web dashboard.
Run: flask --app app run
Then open http://127.0.0.1:5000
"""
import os
from flask import Flask, render_template, jsonify, make_response

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
APP_BUILD = "metrics-ui-2026-02-15"


@app.route("/")
def index():
    resp = render_template("index.html")
    r = make_response(resp)
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return r


@app.route("/api/metrics")
def api_metrics():
    try:
        from odoo_metrics import fetch_metrics
        data = fetch_metrics()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/crm")
def api_crm():
    try:
        from notion_crm import fetch_crm_contacts
        data = fetch_crm_contacts()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/version")
def api_version():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    return jsonify({
        "build": APP_BUILD,
        "app_file": os.path.abspath(__file__),
        "template_file": template_path,
        "template_mtime": os.path.getmtime(template_path),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
