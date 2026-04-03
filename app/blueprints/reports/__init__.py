"""
Reports Blueprint - Budget, Goals, Rules, Investments, Spending Trends, Backup/Restore
"""
from flask import Blueprint, render_template, jsonify, request, Response, current_app
from app.extensions import login_required, _safe, _err, _validate_date
from app.services.database import get_db, snapshot_net_worth, save_rollover
from app.services.licensing import get_license_status
from functools import wraps
from datetime import date

reports_bp = Blueprint('reports', __name__)


def _licensed_only(f):
    """Block feature entirely in demo mode."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        lic = get_license_status()
        if not lic['licensed']:
            return _err('This feature requires a license. Activate to unlock.', 403)
        return f(*args, **kwargs)
    return wrapped


# ── Page routes ──────────────────────────────────────────────────────────────

@reports_bp.route('/budget')
@login_required
def budget():
    return render_template('budget.html', active='budget')


@reports_bp.route('/goals')
@login_required
def goals():
    return render_template('goals.html', active='goals')


@reports_bp.route('/reports')
@login_required
def reports():
    return render_template('reports.html', active='reports')


@reports_bp.route('/rules')
@login_required
def rules():
    return render_template('rules.html', active='rules')


@reports_bp.route('/investments')
@login_required
def investments():
    return render_template('investments.html', active='investments')


# ── API: Budget ──────────────────────────────────────────────────────────────

@reports_bp.route('/api/budget')
@login_required
def api_budget():
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM budget_categories ORDER BY category').fetchall()
    month = date.today().strftime('%Y-%m')
    spent_rows = db.execute(
        "SELECT category, SUM(ABS(amount)) as spent FROM transactions "
        "WHERE amount < 0 AND strftime('%Y-%m', date) = ? GROUP BY category",
        (month,)
    ).fetchall()
    spent_map = {r['category']: round(r['spent'], 2) for r in spent_rows}
    rollover_rows = db.execute(
        'SELECT category, rollover FROM budget_rollover WHERE month=?', (month,)
    ).fetchall()
    rollover_map = {r['category']: round(r['rollover'], 2) for r in rollover_rows}
    result = []
    for r in rows:
        d = dict(r)
        d['spent'] = spent_map.get(d['category'], 0.0)
        d['rollover'] = rollover_map.get(d['category'], 0.0)
        d['effective_budget'] = round(d['budgeted'] + d['rollover'], 2)
        result.append(d)
    return jsonify(result)


@reports_bp.route('/api/budget', methods=['POST'])
@login_required
def api_budget_create():
    d = request.get_json()
    if not d or not d.get('category', '').strip():
        return _err('Category name is required.')
    try:
        budgeted = round(float(d.get('budgeted', 0)), 2)
    except (ValueError, TypeError):
        return _err('Invalid budget amount.')
    db = get_db(current_app)
    try:
        cur = db.execute(
            'INSERT INTO budget_categories (category, budgeted, icon) VALUES (?,?,?)',
            (_safe(d['category']).strip(), budgeted, _safe(d.get('icon', '\U0001f4b0')).strip())
        )
    except db.IntegrityError:
        return _err('Category already exists.')
    db.commit()
    row = db.execute('SELECT * FROM budget_categories WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@reports_bp.route('/api/budget/<int:bid>', methods=['PUT'])
@login_required
def api_budget_update(bid):
    d = request.get_json()
    if not d or not d.get('category', '').strip():
        return _err('Category name is required.')
    try:
        budgeted = round(float(d.get('budgeted', 0)), 2)
    except (ValueError, TypeError):
        return _err('Invalid budget amount.')
    db = get_db(current_app)
    db.execute(
        'UPDATE budget_categories SET category=?, budgeted=?, icon=? WHERE id=?',
        (_safe(d['category']).strip(), budgeted, _safe(d.get('icon', '\U0001f4b0')).strip(), bid)
    )
    db.commit()
    row = db.execute('SELECT * FROM budget_categories WHERE id=?', (bid,)).fetchone()
    if not row:
        return _err('Budget category not found.', 404)
    return jsonify(dict(row))


@reports_bp.route('/api/budget/<int:bid>', methods=['DELETE'])
@login_required
def api_budget_delete(bid):
    db = get_db(current_app)
    db.execute('DELETE FROM budget_categories WHERE id=?', (bid,))
    db.commit()
    return jsonify({'ok': True})


@reports_bp.route('/api/budget/rollover', methods=['POST'])
@login_required
def api_budget_rollover():
    save_rollover(current_app)
    return jsonify({'ok': True})


# ── API: Goals ───────────────────────────────────────────────────────────────

@reports_bp.route('/api/goals')
@login_required
def api_goals():
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM goals ORDER BY target_date').fetchall()
    return jsonify([dict(r) for r in rows])


@reports_bp.route('/api/goals', methods=['POST'])
@login_required
def api_goals_create():
    d = request.get_json()
    if not d or not d.get('name', '').strip():
        return _err('Goal name is required.')
    try:
        target = round(float(d['target_amount']), 2)
        current = round(float(d.get('current_amount', 0)), 2)
    except (ValueError, TypeError, KeyError):
        return _err('Invalid amount.')
    db = get_db(current_app)
    cur = db.execute(
        'INSERT INTO goals (name, target_amount, current_amount, target_date, icon, color) VALUES (?,?,?,?,?,?)',
        (_safe(d['name']).strip(), target, current,
         _validate_date(d.get('target_date')) or '', _safe(d.get('icon', '\U0001f3af')), d.get('color', '#4CAF50'))
    )
    db.commit()
    row = db.execute('SELECT * FROM goals WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@reports_bp.route('/api/goals/<int:gid>', methods=['PUT'])
@login_required
def api_goals_update(gid):
    d = request.get_json()
    if not d or not d.get('name', '').strip():
        return _err('Goal name is required.')
    try:
        target = round(float(d['target_amount']), 2)
        current = round(float(d.get('current_amount', 0)), 2)
    except (ValueError, TypeError, KeyError):
        return _err('Invalid amount.')
    db = get_db(current_app)
    db.execute(
        'UPDATE goals SET name=?, target_amount=?, current_amount=?, target_date=?, icon=?, color=? WHERE id=?',
        (_safe(d['name']).strip(), target, current,
         _validate_date(d.get('target_date')) or '', _safe(d.get('icon', '\U0001f3af')), d.get('color', '#4CAF50'), gid)
    )
    db.commit()
    row = db.execute('SELECT * FROM goals WHERE id=?', (gid,)).fetchone()
    if not row:
        return _err('Goal not found.', 404)
    return jsonify(dict(row))


@reports_bp.route('/api/goals/<int:gid>', methods=['DELETE'])
@login_required
def api_goals_delete(gid):
    db = get_db(current_app)
    db.execute('DELETE FROM goals WHERE id=?', (gid,))
    db.commit()
    return jsonify({'ok': True})


# ── Transaction Rules CRUD ───────────────────────────────────────────────────

@reports_bp.route('/api/rules')
@login_required
def api_rules():
    db = get_db(current_app)
    rows = db.execute('SELECT * FROM transaction_rules ORDER BY priority DESC, pattern').fetchall()
    return jsonify([dict(r) for r in rows])


@reports_bp.route('/api/rules', methods=['POST'])
@login_required
def api_rules_create():
    d = request.get_json()
    if not d or not d.get('pattern', '').strip() or not d.get('category', '').strip():
        return _err('Pattern and category are required.')
    db = get_db(current_app)
    cur = db.execute(
        'INSERT INTO transaction_rules (pattern, category, tags, priority) VALUES (?,?,?,?)',
        (_safe(d['pattern']).strip(), _safe(d['category']).strip(),
         _safe(d.get('tags', '')).strip(), int(d.get('priority', 0)))
    )
    db.commit()
    row = db.execute('SELECT * FROM transaction_rules WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@reports_bp.route('/api/rules/<int:rid>', methods=['PUT'])
@login_required
def api_rules_update(rid):
    d = request.get_json()
    if not d or not d.get('pattern', '').strip() or not d.get('category', '').strip():
        return _err('Pattern and category are required.')
    db = get_db(current_app)
    db.execute(
        'UPDATE transaction_rules SET pattern=?, category=?, tags=?, priority=? WHERE id=?',
        (_safe(d['pattern']).strip(), _safe(d['category']).strip(),
         _safe(d.get('tags', '')).strip(), int(d.get('priority', 0)), rid)
    )
    db.commit()
    row = db.execute('SELECT * FROM transaction_rules WHERE id=?', (rid,)).fetchone()
    if not row:
        return _err('Rule not found.', 404)
    return jsonify(dict(row))


@reports_bp.route('/api/rules/<int:rid>', methods=['DELETE'])
@login_required
def api_rules_delete(rid):
    db = get_db(current_app)
    db.execute('DELETE FROM transaction_rules WHERE id=?', (rid,))
    db.commit()
    return jsonify({'ok': True})


# ── Spending Trends ──────────────────────────────────────────────────────────

@reports_bp.route('/api/spending-trends')
@login_required
def api_spending_trends():
    db = get_db(current_app)
    months_back = request.args.get('months', 6, type=int)
    rows = db.execute(
        "SELECT strftime('%Y-%m', date) as month, category, SUM(ABS(amount)) as spent "
        "FROM transactions WHERE amount < 0 "
        "GROUP BY month, category ORDER BY month DESC"
    ).fetchall()
    data = {}
    all_cats = set()
    for r in rows:
        m = r['month']
        if m not in data:
            data[m] = {}
        data[m][r['category']] = round(r['spent'], 2)
        all_cats.add(r['category'])

    months = sorted(data.keys(), reverse=True)[:months_back]
    months.reverse()
    return jsonify({
        'months': months,
        'categories': sorted(all_cats),
        'data': {m: data.get(m, {}) for m in months}
    })


# ── Data Backup / Restore ───────────────────────────────────────────────────

@reports_bp.route('/api/backup')
@login_required
@_licensed_only
def api_backup():
    import json as jsonlib
    db = get_db(current_app)
    tables = ['accounts', 'transactions', 'budget_categories', 'goals', 'recurring',
               'net_worth_history', 'transaction_rules', 'budget_rollover', 'holdings']
    backup = {}
    for table in tables:
        try:
            rows = db.execute(f'SELECT * FROM {table}').fetchall()
            backup[table] = [dict(r) for r in rows]
        except Exception:
            backup[table] = []
    data = jsonlib.dumps(backup, indent=2)
    return Response(data, mimetype='application/json',
                    headers={'Content-Disposition': 'attachment; filename=wealthwatch_backup.json'})


@reports_bp.route('/api/restore', methods=['POST'])
@login_required
@_licensed_only
def api_restore():
    import json as jsonlib
    f = request.files.get('file')
    if not f:
        return _err('No file uploaded.')
    try:
        data = jsonlib.loads(f.read().decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError):
        return _err('Invalid JSON file.')
    db = get_db(current_app)
    restored = {}
    table_cols = {
        'accounts': 'name, institution, type, balance, last_updated',
        'budget_categories': 'category, budgeted, icon',
        'goals': 'name, target_amount, current_amount, target_date, icon, color',
        'holdings': 'account_id, symbol, name, shares, cost_basis, current_price, asset_class',
    }
    for table, cols in table_cols.items():
        if table in data and data[table]:
            col_list = [c.strip() for c in cols.split(',')]
            for row in data[table]:
                vals = [row.get(c, '') for c in col_list]
                placeholders = ','.join(['?'] * len(vals))
                try:
                    db.execute(f'INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})', vals)
                except Exception:
                    continue
            restored[table] = len(data[table])
    db.commit()
    snapshot_net_worth(current_app)
    return jsonify({'ok': True, 'restored': restored})
