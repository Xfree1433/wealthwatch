import sqlite3
import os
from datetime import date, timedelta
import random

def get_db(app):
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    institution TEXT,
    type TEXT NOT NULL CHECK(type IN ('checking','savings','credit','investment','real_estate','loan')),
    balance REAL NOT NULL DEFAULT 0,
    last_updated TEXT DEFAULT (date('now')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,  -- positive=income, negative=expense
    notes TEXT,
    tags TEXT DEFAULT '',  -- comma-separated tags
    transfer_id INTEGER,  -- links paired transfer transactions
    is_split INTEGER DEFAULT 0,  -- 1 if this is a split child
    split_parent_id INTEGER,  -- parent transaction for splits
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS budget_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL UNIQUE,
    budgeted REAL NOT NULL DEFAULT 0,
    icon TEXT DEFAULT '💰'
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target_amount REAL NOT NULL,
    current_amount REAL NOT NULL DEFAULT 0,
    target_date TEXT,
    icon TEXT DEFAULT '🎯',
    color TEXT DEFAULT '#4CAF50'
);

CREATE TABLE IF NOT EXISTS recurring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    frequency TEXT NOT NULL CHECK(frequency IN ('monthly','weekly','annual')),
    category TEXT NOT NULL,
    next_date TEXT,
    account_id INTEGER,
    active INTEGER DEFAULT 1,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS net_worth_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    net_worth REAL NOT NULL,
    assets REAL NOT NULL DEFAULT 0,
    liabilities REAL NOT NULL DEFAULT 0,
    cash REAL NOT NULL DEFAULT 0,
    investments REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    shares REAL NOT NULL DEFAULT 0,
    cost_basis REAL NOT NULL DEFAULT 0,
    current_price REAL NOT NULL DEFAULT 0,
    asset_class TEXT DEFAULT 'stock',  -- stock, bond, etf, mutual_fund, crypto, cash, other
    last_updated TEXT DEFAULT (date('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    uploaded_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transaction_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,         -- substring match on description (case-insensitive)
    category TEXT NOT NULL,
    tags TEXT DEFAULT '',
    priority INTEGER DEFAULT 0     -- higher = checked first
);

CREATE TABLE IF NOT EXISTS budget_rollover (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    month TEXT NOT NULL,           -- YYYY-MM
    rollover REAL NOT NULL DEFAULT 0,
    UNIQUE(category, month)
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_account_id ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_net_worth_date ON net_worth_history(date);
"""

MOCK_ACCOUNTS = [
    ('Chase Checking', 'Chase', 'checking', 5402.17),
    ("Melena's Checking", 'Chase', 'checking', 15493.62),
    ('Joint Savings', 'Ally', 'savings', 50119.21),
    ('Fidelity Brokerage', 'Fidelity', 'investment', 341533.90),
    ('Roth IRA', 'Fidelity', 'investment', 88240.00),
    ('Home - Kingwood', 'Zillow', 'real_estate', 385000.00),
    ('Chase Sapphire', 'Chase', 'credit', -4210.50),
    ('Auto Loan', 'Capital One', 'loan', -18200.00),
]

CATEGORIES = [
    ('Housing', 2200, '🏠'),
    ('Groceries', 800, '🛒'),
    ('Dining Out', 400, '🍽️'),
    ('Transportation', 350, '🚗'),
    ('Utilities', 250, '⚡'),
    ('Healthcare', 200, '🏥'),
    ('Entertainment', 150, '🎬'),
    ('Shopping', 300, '🛍️'),
    ('Travel', 500, '✈️'),
    ('Subscriptions', 120, '📱'),
    ('Personal Care', 100, '💇'),
    ('Education', 50, '📚'),
]

GOALS = [
    ('Emergency Fund', 50000, 50119.21, '2025-12-31', '🛡️', '#2196F3'),
    ('New Vehicle', 35000, 12000, '2026-06-01', '🚙', '#FF9800'),
    ('Vacation Fund', 8000, 3200, '2025-08-01', '✈️', '#9C27B0'),
    ('Home Renovation', 20000, 7500, '2026-12-31', '🏠', '#4CAF50'),
]

RECURRING = [
    ('Mortgage', 2100.00, 'monthly', 'Housing', 1),
    ('Electricity', 145.00, 'monthly', 'Utilities', 1),
    ('Internet', 79.99, 'monthly', 'Utilities', 1),
    ('Netflix', 22.99, 'monthly', 'Subscriptions', 1),
    ('Spotify', 10.99, 'monthly', 'Subscriptions', 1),
    ('Gym Membership', 49.00, 'monthly', 'Personal Care', 1),
    ('Car Insurance', 185.00, 'monthly', 'Transportation', 1),
    ('AWS / Hosting', 35.00, 'monthly', 'Subscriptions', 1),
    ('Home Insurance', 1650.00, 'annual', 'Housing', 1),
    ('Amazon Prime', 139.00, 'annual', 'Subscriptions', 1),
]

SAMPLE_TRANSACTIONS = [
    ('HEB Grocery', 'Groceries', -187.43),
    ('Chick-fil-A', 'Dining Out', -24.87),
    ('Shell Gas Station', 'Transportation', -68.50),
    ('Amazon', 'Shopping', -54.99),
    ('Netflix', 'Subscriptions', -22.99),
    ('Target', 'Shopping', -112.34),
    ('Starbucks', 'Dining Out', -8.75),
    ('CVS Pharmacy', 'Healthcare', -34.20),
    ("Chili's Restaurant", 'Dining Out', -67.80),
    ('Exxon Gas', 'Transportation', -55.00),
    ('Whole Foods', 'Groceries', -95.22),
    ('Costco', 'Groceries', -210.44),
    ('H&M', 'Shopping', -78.00),
    ('Spotify', 'Subscriptions', -10.99),
    ('Gym Membership', 'Personal Care', -49.00),
    ('Electric Bill', 'Utilities', -143.50),
    ('Internet Bill', 'Utilities', -79.99),
    ('Doctor Visit Copay', 'Healthcare', -40.00),
    ('Movie Tickets', 'Entertainment', -38.50),
    ('Home Depot', 'Shopping', -145.67),
    ('Paycheck - Pilot Water', 'Income', 6250.00),
    ('PF9 Consulting', 'Income', 1500.00),
    ('Interest - Ally Savings', 'Income', 42.17),
    ('Mortgage Payment', 'Housing', -2100.00),
    ('Car Insurance', 'Transportation', -185.00),
]

def seed_mock_data(conn):
    for name, institution, acct_type, balance in MOCK_ACCOUNTS:
        conn.execute(
            'INSERT OR IGNORE INTO accounts (name, institution, type, balance) VALUES (?,?,?,?)',
            (name, institution, acct_type, balance)
        )
    for cat, budgeted, icon in CATEGORIES:
        conn.execute(
            'INSERT OR IGNORE INTO budget_categories (category, budgeted, icon) VALUES (?,?,?)',
            (cat, budgeted, icon)
        )
    for name, target, current, tdate, icon, color in GOALS:
        conn.execute(
            'INSERT OR IGNORE INTO goals (name, target_amount, current_amount, target_date, icon, color) VALUES (?,?,?,?,?,?)',
            (name, target, current, tdate, icon, color)
        )
    today = date.today()
    for i, (name, amount, freq, cat, acct_id) in enumerate(RECURRING):
        next_date = (today + timedelta(days=random.randint(1, 30))).isoformat()
        conn.execute(
            'INSERT OR IGNORE INTO recurring (name, amount, frequency, category, next_date, account_id) VALUES (?,?,?,?,?,?)',
            (name, amount, freq, cat, next_date, acct_id)
        )
    checking_id = conn.execute("SELECT id FROM accounts WHERE name='Chase Checking'").fetchone()[0]
    for days_back in range(90, 0, -1):
        txn_date = (today - timedelta(days=days_back)).isoformat()
        count = random.randint(0, 3)
        for _ in range(count):
            desc, cat, amount = random.choice(SAMPLE_TRANSACTIONS)
            if amount < 0:
                amount = round(amount * random.uniform(0.8, 1.2), 2)
            conn.execute(
                'INSERT INTO transactions (account_id, date, description, category, amount) VALUES (?,?,?,?,?)',
                (checking_id, txn_date, desc, cat, amount)
            )

    # Seed investment holdings
    brokerage_id = conn.execute("SELECT id FROM accounts WHERE name='Fidelity Brokerage'").fetchone()[0]
    ira_id = conn.execute("SELECT id FROM accounts WHERE name='Roth IRA'").fetchone()[0]
    mock_holdings = [
        (brokerage_id, 'VTI', 'Vanguard Total Stock Market ETF', 450, 89100, 225.80, 'etf'),
        (brokerage_id, 'VXUS', 'Vanguard Total Intl Stock ETF', 300, 15600, 58.20, 'etf'),
        (brokerage_id, 'BND', 'Vanguard Total Bond Market ETF', 200, 14800, 72.50, 'bond'),
        (brokerage_id, 'AAPL', 'Apple Inc.', 100, 15200, 198.50, 'stock'),
        (brokerage_id, 'MSFT', 'Microsoft Corp.', 50, 18500, 425.30, 'stock'),
        (brokerage_id, 'GOOGL', 'Alphabet Inc.', 30, 4200, 178.90, 'stock'),
        (ira_id, 'FXAIX', 'Fidelity 500 Index', 120, 52000, 195.80, 'mutual_fund'),
        (ira_id, 'FTBFX', 'Fidelity Total Bond', 300, 3100, 9.85, 'bond'),
        (ira_id, 'FSPSX', 'Fidelity Intl Index', 200, 8400, 46.20, 'mutual_fund'),
    ]
    for acct, sym, name, shares, basis, price, aclass in mock_holdings:
        conn.execute(
            'INSERT OR IGNORE INTO holdings (account_id, symbol, name, shares, cost_basis, current_price, asset_class) VALUES (?,?,?,?,?,?,?)',
            (acct, sym, name, shares, basis, price, aclass)
        )

    # Seed net worth history for past 12 months
    base_nw = 863378.40  # sum of mock account balances
    for months_back in range(12, 0, -1):
        d = (today.replace(day=1) - timedelta(days=months_back * 30)).replace(day=1)
        drift = base_nw + random.uniform(-15000, 25000) * (12 - months_back) / 12
        assets = drift + abs(random.uniform(0, 5000))
        liabilities = 22410.50 + random.uniform(-500, 500)
        conn.execute(
            'INSERT OR IGNORE INTO net_worth_history (date, net_worth, assets, liabilities, cash, investments) VALUES (?,?,?,?,?,?)',
            (d.isoformat(), round(drift, 2), round(assets, 2), round(liabilities, 2),
             round(71015.0 + random.uniform(-2000, 3000), 2),
             round(429773.90 + random.uniform(-10000, 15000), 2))
        )

    conn.commit()

def snapshot_net_worth(app):
    """Record today's net worth from current account balances."""
    conn = get_db(app)
    today_str = date.today().isoformat()
    row = conn.execute(
        "SELECT SUM(balance) as nw, "
        "SUM(CASE WHEN type NOT IN ('credit','loan') THEN balance ELSE 0 END) as assets, "
        "SUM(CASE WHEN type IN ('credit','loan') THEN ABS(balance) ELSE 0 END) as liabilities, "
        "SUM(CASE WHEN type IN ('checking','savings') THEN balance ELSE 0 END) as cash, "
        "SUM(CASE WHEN type='investment' THEN balance ELSE 0 END) as investments "
        "FROM accounts"
    ).fetchone()
    if row and row['nw'] is not None:
        conn.execute(
            'INSERT OR REPLACE INTO net_worth_history (date, net_worth, assets, liabilities, cash, investments) VALUES (?,?,?,?,?,?)',
            (today_str, round(row['nw'], 2), round(row['assets'], 2), round(row['liabilities'], 2),
             round(row['cash'], 2), round(row['investments'], 2))
        )
        conn.commit()
    conn.close()

def _migrate(conn):
    """Run schema migrations for existing databases."""
    # Migrate: drop 'spent' column from budget_categories if present
    bud_cols = [r[1] for r in conn.execute('PRAGMA table_info(budget_categories)').fetchall()]
    if 'spent' in bud_cols:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS budget_categories_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL UNIQUE,
                budgeted REAL NOT NULL DEFAULT 0,
                icon TEXT DEFAULT '💰'
            );
            INSERT OR IGNORE INTO budget_categories_new (id, category, budgeted, icon)
                SELECT id, category, budgeted, icon FROM budget_categories;
            DROP TABLE budget_categories;
            ALTER TABLE budget_categories_new RENAME TO budget_categories;
        """)
    # Migrate: add new columns to transactions
    txn_cols = [r[1] for r in conn.execute('PRAGMA table_info(transactions)').fetchall()]
    for col, default in [('tags', "''"), ('transfer_id', 'NULL'), ('is_split', '0'), ('split_parent_id', 'NULL')]:
        if col not in txn_cols:
            conn.execute(f'ALTER TABLE transactions ADD COLUMN {col} {"TEXT" if col == "tags" else "INTEGER"} DEFAULT {default}')
    # Seed default transaction rules if table is empty
    rule_count = conn.execute('SELECT COUNT(*) FROM transaction_rules').fetchone()[0]
    if rule_count == 0:
        default_rules = [
            ('HEB', 'Groceries', '', 10), ('Whole Foods', 'Groceries', '', 10),
            ('Costco', 'Groceries', '', 10), ('Kroger', 'Groceries', '', 10),
            ('Walmart', 'Groceries', '', 5), ('Target', 'Shopping', '', 5),
            ('Amazon', 'Shopping', '', 5), ('Home Depot', 'Shopping', '', 5),
            ('Chick-fil-A', 'Dining Out', '', 10), ('Starbucks', 'Dining Out', '', 10),
            ("Chili's", 'Dining Out', '', 10), ('McDonald', 'Dining Out', '', 10),
            ('Shell', 'Transportation', '', 10), ('Exxon', 'Transportation', '', 10),
            ('Chevron', 'Transportation', '', 10),
            ('Netflix', 'Subscriptions', '', 10), ('Spotify', 'Subscriptions', '', 10),
            ('CVS', 'Healthcare', '', 10), ('Walgreens', 'Healthcare', '', 10),
            ('Paycheck', 'Income', '', 10), ('Direct Deposit', 'Income', '', 10),
            ('Electric', 'Utilities', '', 8), ('Internet', 'Utilities', '', 8),
            ('Water Bill', 'Utilities', '', 8), ('Gas Bill', 'Utilities', '', 8),
            ('Mortgage', 'Housing', '', 10), ('Rent', 'Housing', '', 10),
            ('Insurance', 'Transportation', '', 5),
        ]
        for pattern, cat, tags, priority in default_rules:
            conn.execute('INSERT INTO transaction_rules (pattern, category, tags, priority) VALUES (?,?,?,?)',
                         (pattern, cat, tags, priority))
    conn.commit()

def process_recurring(app):
    """Auto-post recurring transactions whose next_date has passed."""
    conn = get_db(app)
    today_str = date.today().isoformat()
    due = conn.execute(
        'SELECT * FROM recurring WHERE active=1 AND next_date != "" AND next_date <= ?',
        (today_str,)
    ).fetchall()
    posted = 0
    for r in due:
        acct_id = r['account_id'] or conn.execute('SELECT id FROM accounts LIMIT 1').fetchone()['id']
        conn.execute(
            'INSERT INTO transactions (account_id, date, description, category, amount, notes) VALUES (?,?,?,?,?,?)',
            (acct_id, r['next_date'], r['name'], r['category'], -abs(r['amount']), 'Auto-posted recurring')
        )
        conn.execute('UPDATE accounts SET balance = balance - ?, last_updated = ? WHERE id = ?',
                     (abs(r['amount']), today_str, acct_id))
        # Advance next_date
        nd = date.fromisoformat(r['next_date'])
        if r['frequency'] == 'monthly':
            m = nd.month + 1
            y = nd.year + (m - 1) // 12
            m = ((m - 1) % 12) + 1
            try:
                nd = nd.replace(year=y, month=m)
            except ValueError:
                import calendar
                nd = nd.replace(year=y, month=m, day=calendar.monthrange(y, m)[1])
        elif r['frequency'] == 'weekly':
            nd += timedelta(days=7)
        elif r['frequency'] == 'annual':
            try:
                nd = nd.replace(year=nd.year + 1)
            except ValueError:
                nd = nd.replace(year=nd.year + 1, day=28)
        conn.execute('UPDATE recurring SET next_date=? WHERE id=?', (nd.isoformat(), r['id']))
        posted += 1
    if posted:
        conn.commit()
    conn.close()
    return posted

def auto_categorize(app, description):
    """Match description against transaction rules. Returns (category, tags) or (None, None)."""
    conn = get_db(app)
    rules = conn.execute('SELECT * FROM transaction_rules ORDER BY priority DESC').fetchall()
    conn.close()
    desc_lower = description.lower()
    for r in rules:
        if r['pattern'].lower() in desc_lower:
            return r['category'], r['tags'] or ''
    return None, None

def compute_rollover(app, category, month_str):
    """Get rollover amount for a category in a given month."""
    conn = get_db(app)
    row = conn.execute(
        'SELECT rollover FROM budget_rollover WHERE category=? AND month=?',
        (category, month_str)
    ).fetchone()
    conn.close()
    return row['rollover'] if row else 0.0

def save_rollover(app):
    """Calculate and store rollover from previous month into current month."""
    conn = get_db(app)
    today = date.today()
    # Previous month
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    prev_month = f'{prev_y:04d}-{prev_m:02d}'
    curr_month = today.strftime('%Y-%m')

    cats = conn.execute('SELECT * FROM budget_categories').fetchall()
    spent_rows = conn.execute(
        "SELECT category, SUM(ABS(amount)) as spent FROM transactions "
        "WHERE amount < 0 AND strftime('%Y-%m', date) = ? GROUP BY category",
        (prev_month,)
    ).fetchall()
    spent_map = {r['category']: r['spent'] for r in spent_rows}

    for c in cats:
        prev_rollover_row = conn.execute(
            'SELECT rollover FROM budget_rollover WHERE category=? AND month=?',
            (c['category'], prev_month)
        ).fetchone()
        prev_rollover = prev_rollover_row['rollover'] if prev_rollover_row else 0.0
        effective_budget = c['budgeted'] + prev_rollover
        spent = spent_map.get(c['category'], 0.0)
        new_rollover = round(max(effective_budget - spent, 0), 2)
        if new_rollover > 0:
            conn.execute(
                'INSERT OR REPLACE INTO budget_rollover (category, month, rollover) VALUES (?,?,?)',
                (c['category'], curr_month, new_rollover)
            )
    conn.commit()
    conn.close()

def init_db(app):
    conn = get_db(app)
    conn.executescript(SCHEMA)
    _migrate(conn)
    count = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
    if count == 0:
        seed_mock_data(conn)
    conn.close()
