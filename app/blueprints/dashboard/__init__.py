"""
Dashboard Blueprint - Main dashboard and overview pages
"""
from flask import Blueprint, render_template, jsonify, current_app, request
from app.extensions import login_required
from app.services.database import get_db
from datetime import date
import datetime as dt

dashboard_bp = Blueprint('dashboard', __name__)


# ── Page routes ──────────────────────────────────────────────────────────────

@dashboard_bp.route('/')
@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', active='dashboard')


@dashboard_bp.route('/cashflow')
@login_required
def cashflow():
    return render_template('cashflow.html', active='cashflow')


# ── API: Summary ─────────────────────────────────────────────────────────────

@dashboard_bp.route('/api/summary')
@login_required
def api_summary():
    db = get_db(current_app)
    net_worth = db.execute('SELECT SUM(balance) FROM accounts').fetchone()[0] or 0
    cash = db.execute("SELECT SUM(balance) FROM accounts WHERE type='checking' OR type='savings'").fetchone()[0] or 0
    investments = db.execute("SELECT SUM(balance) FROM accounts WHERE type='investment'").fetchone()[0] or 0
    income = db.execute(
        "SELECT SUM(amount) FROM transactions WHERE amount > 0 AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now')"
    ).fetchone()[0] or 0
    expenses = db.execute(
        "SELECT SUM(ABS(amount)) FROM transactions WHERE amount < 0 AND strftime('%Y-%m', date) = strftime('%Y-%m', 'now')"
    ).fetchone()[0] or 0
    return jsonify({
        'net_worth': round(net_worth, 2),
        'cash': round(cash, 2),
        'investments': round(investments, 2),
        'monthly_income': round(income, 2),
        'monthly_expenses': round(expenses, 2),
        'monthly_savings': round(income - expenses, 2)
    })


@dashboard_bp.route('/api/networth')
@login_required
def api_networth():
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM net_worth_history ORDER BY date ASC').fetchall()
    return jsonify([dict(r) for r in rows])


@dashboard_bp.route('/api/cashflow')
@login_required
def api_cashflow():
    db = get_db(current_app)
    rows = db.execute(
        'SELECT strftime("%Y-%m", date) as month, '
        'SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) as income, '
        'SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) as expenses '
        'FROM transactions GROUP BY month ORDER BY month DESC LIMIT 12'
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ── Alerts / Notifications ───────────────────────────────────────────────────

@dashboard_bp.route('/api/alerts')
@login_required
def api_alerts():
    db = get_db(current_app)
    alerts = []
    month = date.today().strftime('%Y-%m')

    # Over-budget categories
    cats = db.execute('SELECT * FROM budget_categories').fetchall()
    spent_rows = db.execute(
        "SELECT category, SUM(ABS(amount)) as spent FROM transactions "
        "WHERE amount < 0 AND strftime('%Y-%m', date) = ? GROUP BY category",
        (month,)
    ).fetchall()
    spent_map = {r['category']: r['spent'] for r in spent_rows}
    rollover_rows = db.execute(
        'SELECT category, rollover FROM budget_rollover WHERE month=?', (month,)
    ).fetchall()
    rollover_map = {r['category']: r['rollover'] for r in rollover_rows}
    for c in cats:
        spent = spent_map.get(c['category'], 0)
        effective = c['budgeted'] + rollover_map.get(c['category'], 0)
        if effective > 0 and spent > effective:
            over = round(spent - effective, 2)
            alerts.append({
                'type': 'over_budget', 'severity': 'warning',
                'message': f"{c['icon']} {c['category']} is ${over:,.2f} over budget"
            })
        elif effective > 0 and spent > effective * 0.9:
            alerts.append({
                'type': 'near_budget', 'severity': 'info',
                'message': f"{c['icon']} {c['category']} is at {round(spent/effective*100)}% of budget"
            })

    # Large transactions (> $500) in past 7 days
    week_ago = (date.today() - dt.timedelta(days=7)).isoformat()
    large = db.execute(
        'SELECT description, amount, date FROM transactions WHERE ABS(amount) > 500 AND date >= ? ORDER BY date DESC LIMIT 5',
        (week_ago,)
    ).fetchall()
    for t in large:
        alerts.append({
            'type': 'large_transaction', 'severity': 'info',
            'message': f"Large transaction: {t['description']} -- ${abs(t['amount']):,.2f} on {t['date']}"
        })

    # Overdue recurring bills
    today_str = date.today().isoformat()
    overdue = db.execute(
        'SELECT name, next_date FROM recurring WHERE active=1 AND next_date != "" AND next_date < ?',
        (today_str,)
    ).fetchall()
    for r in overdue:
        alerts.append({
            'type': 'overdue_bill', 'severity': 'warning',
            'message': f"Overdue bill: {r['name']} was due {r['next_date']}"
        })

    return jsonify(alerts)
