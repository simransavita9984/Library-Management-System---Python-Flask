import sqlite3
import os
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify, g)

app = Flask(__name__)
app.secret_key = 'library_secret_key_2024'

DATABASE = os.path.join(os.path.dirname(__file__), 'library.db')
FINE_PER_DAY = 5.0
CATEGORIES = ['Science', 'Economics', 'Fiction', 'Children', 'Personal Development']

# ─── DATABASE ─────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()

def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

def init_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            membership_id TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            contact_number TEXT NOT NULL,
            contact_address TEXT NOT NULL,
            aadhar_card TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            membership_type TEXT DEFAULT '6months',
            is_active INTEGER DEFAULT 1,
            fine_pending REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            author TEXT NOT NULL,
            category TEXT NOT NULL,
            item_type TEXT DEFAULT 'Book',
            status TEXT DEFAULT 'Available',
            cost REAL NOT NULL,
            procurement_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_no TEXT NOT NULL,
            book_name TEXT NOT NULL,
            membership_id TEXT NOT NULL,
            issue_date TEXT NOT NULL,
            return_date TEXT NOT NULL,
            actual_return_date TEXT,
            fine_calculated REAL DEFAULT 0,
            fine_paid INTEGER DEFAULT 0,
            remarks TEXT,
            status TEXT DEFAULT 'Active'
        );
    """)
    db.commit()

    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.execute("INSERT INTO users (username,password,name,is_admin) VALUES (?,?,?,?)",
                   ('adm', 'adm', 'Admin User', 1))
        db.execute("INSERT INTO users (username,password,name,is_admin) VALUES (?,?,?,?)",
                   ('user', 'user', 'Regular User', 0))

    if db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0:
        sample = [
            ('SC(B)000001','A Brief History of Time','Stephen Hawking','Science','Book',350.0,'2023-01-10'),
            ('SC(B)000002','The Selfish Gene','Richard Dawkins','Science','Book',299.0,'2023-02-05'),
            ('FC(B)000001',"Harry Potter and the Sorcerer's Stone",'J.K. Rowling','Fiction','Book',450.0,'2023-03-15'),
            ('FC(B)000002','The Alchemist','Paulo Coelho','Fiction','Book',250.0,'2023-04-20'),
            ('EC(B)000001','Rich Dad Poor Dad','Robert Kiyosaki','Economics','Book',399.0,'2023-05-01'),
            ('CH(B)000001',"Charlotte's Web",'E.B. White','Children','Book',199.0,'2023-06-10'),
            ('PD(B)000001','Atomic Habits','James Clear','Personal Development','Book',499.0,'2023-07-01'),
            ('SC(M)000001','Interstellar','Christopher Nolan','Science','Movie',200.0,'2023-08-01'),
            ('FC(M)000001','Inception','Christopher Nolan','Fiction','Movie',180.0,'2023-09-01'),
        ]
        for b in sample:
            db.execute("INSERT INTO books (serial_no,name,author,category,item_type,cost,procurement_date) VALUES (?,?,?,?,?,?,?)", b)

    if db.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] == 0:
        db.execute("""INSERT INTO memberships
            (membership_id,first_name,last_name,contact_number,contact_address,aadhar_card,start_date,end_date,membership_type)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            ('MEM00001','Ravi','Kumar','9876543210','123 MG Road, Kanpur','1234-5678-9012','2024-01-01','2024-07-01','6months'))
        db.execute("""INSERT INTO memberships
            (membership_id,first_name,last_name,contact_number,contact_address,aadhar_card,start_date,end_date,membership_type)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            ('MEM00002','Priya','Sharma','9876500000','456 Civil Lines, Kanpur','9876-5432-1098','2024-03-01','2025-03-01','1year'))

    db.commit()
    db.close()

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'warning')
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_page'))
        if not session.get('is_admin'):
            flash('Admin access required.', 'danger')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated

def gen_membership_id():
    row = query("SELECT COUNT(*) as c FROM memberships", one=True)
    return f"MEM{row['c']+1:05d}"

def gen_serial_no(item_type, category):
    prefix_map = {'Science':'SC','Economics':'EC','Fiction':'FC',
                  'Children':'CH','Personal Development':'PD'}
    tc = 'B' if item_type == 'Book' else 'M'
    prefix = prefix_map.get(category, 'GN')
    row = query("SELECT COUNT(*) as c FROM books WHERE category=? AND item_type=?",
                (category, item_type), one=True)
    return f"{prefix}({tc}){row['c']+1:06d}"

def calc_fine(return_date_str):
    today = datetime.today().date()
    ret = datetime.strptime(return_date_str, '%Y-%m-%d').date()
    return max(0.0, (today - ret).days * FINE_PER_DAY) if today > ret else 0.0

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('login_page'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = query("SELECT * FROM users WHERE username=? AND password=? AND is_active=1",
                     (username, password), one=True)
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['name'] = user['name']
            session['is_admin'] = bool(user['is_admin'])
            return redirect(url_for('home'))
        flash('Invalid credentials. Please try again.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return render_template('logout.html')

@app.route('/home')
@login_required
def home():
    if session.get('is_admin'):
        return render_template('admin_home.html', categories=CATEGORIES)
    return render_template('user_home.html', categories=CATEGORIES)

# ─── TRANSACTIONS ─────────────────────────────────────────────────────────────

@app.route('/transactions')
@login_required
def transactions():
    return render_template('transactions.html')

@app.route('/book_available', methods=['GET', 'POST'])
@login_required
def book_available():
    books, searched = [], False
    book_names = [r['name'] for r in query("SELECT DISTINCT name FROM books ORDER BY name")]
    authors = [r['author'] for r in query("SELECT DISTINCT author FROM books ORDER BY author")]
    if request.method == 'POST':
        name = request.form.get('book_name', '').strip()
        author = request.form.get('author', '').strip()
        if not name and not author:
            flash('Please enter Book Name or Author before searching.', 'warning')
            return render_template('book_available.html', books=[], searched=False,
                                   book_names=book_names, authors=authors)
        sql, args = "SELECT * FROM books WHERE 1=1", []
        if name:
            sql += " AND name LIKE ?"
            args.append(f'%{name}%')
        if author:
            sql += " AND author LIKE ?"
            args.append(f'%{author}%')
        books, searched = query(sql, args), True
    return render_template('book_available.html', books=books, searched=searched,
                           book_names=book_names, authors=authors)

@app.route('/book_issue', methods=['GET', 'POST'])
@login_required
def book_issue():
    books = query("SELECT * FROM books WHERE status='Available' ORDER BY name")
    if request.method == 'POST':
        serial_no     = request.form.get('serial_no', '').strip()
        issue_date_s  = request.form.get('issue_date', '').strip()
        return_date_s = request.form.get('return_date', '').strip()
        membership_id = request.form.get('membership_id', '').strip()
        remarks       = request.form.get('remarks', '').strip()

        if not serial_no or not issue_date_s or not return_date_s or not membership_id:
            flash('Book, Membership ID, Issue Date and Return Date are required.', 'danger')
            return render_template('book_issue.html', books=books)

        book = query("SELECT * FROM books WHERE serial_no=? AND status='Available'", (serial_no,), one=True)
        if not book:
            flash('Book not found or already issued.', 'danger')
            return render_template('book_issue.html', books=books)

        issue_date  = datetime.strptime(issue_date_s, '%Y-%m-%d').date()
        return_date = datetime.strptime(return_date_s, '%Y-%m-%d').date()
        today       = datetime.today().date()

        if issue_date < today:
            flash('Issue date cannot be in the past.', 'danger')
            return render_template('book_issue.html', books=books)
        if return_date > issue_date + timedelta(days=15):
            flash('Return date cannot be more than 15 days from issue date.', 'danger')
            return render_template('book_issue.html', books=books)

        execute("INSERT INTO issues (serial_no,book_name,membership_id,issue_date,return_date,remarks) VALUES (?,?,?,?,?,?)",
                (serial_no, book['name'], membership_id, issue_date_s, return_date_s, remarks))
        execute("UPDATE books SET status='Issued' WHERE serial_no=?", (serial_no,))
        flash('Book issued successfully!', 'success')
        return redirect(url_for('confirmation'))
    return render_template('book_issue.html', books=books)

@app.route('/get_book_info')
@login_required
def get_book_info():
    serial_no = request.args.get('serial_no', '')
    book = query("SELECT * FROM books WHERE serial_no=?", (serial_no,), one=True)
    return jsonify({'author': book['author'], 'name': book['name']} if book else {'author':'','name':''})

@app.route('/return_book', methods=['GET', 'POST'])
@login_required
def return_book():
    serial_nos = [r['serial_no'] for r in query("SELECT serial_no FROM books WHERE status='Issued'")]
    if request.method == 'POST':
        serial_no     = request.form.get('serial_no', '').strip()
        return_date_s = request.form.get('return_date', '').strip()
        remarks       = request.form.get('remarks', '').strip()

        if not serial_no:
            flash('Serial No is required.', 'danger')
            return render_template('return_book.html', serial_nos=serial_nos)

        issue = query("SELECT * FROM issues WHERE serial_no=? AND status='Active'", (serial_no,), one=True)
        if not issue:
            flash('No active issue found for this serial no.', 'danger')
            return render_template('return_book.html', serial_nos=serial_nos)

        session['return_serial']  = serial_no
        session['return_date']    = return_date_s or issue['return_date']
        session['return_remarks'] = remarks
        return redirect(url_for('pay_fine'))
    return render_template('return_book.html', serial_nos=serial_nos)

@app.route('/get_issue_info')
@login_required
def get_issue_info():
    serial_no = request.args.get('serial_no', '')
    issue = query("SELECT * FROM issues WHERE serial_no=? AND status='Active'", (serial_no,), one=True)
    if issue:
        book = query("SELECT author FROM books WHERE serial_no=?", (serial_no,), one=True)
        return jsonify({'book_name': issue['book_name'],
                        'author': book['author'] if book else '',
                        'issue_date': issue['issue_date'],
                        'return_date': issue['return_date']})
    return jsonify({})

@app.route('/pay_fine', methods=['GET', 'POST'])
@login_required
def pay_fine():
    serial_no = session.get('return_serial')
    if not serial_no:
        return redirect(url_for('return_book'))

    issue = query("SELECT * FROM issues WHERE serial_no=? AND status='Active'", (serial_no,), one=True)
    if not issue:
        flash('Issue record not found.', 'danger')
        return redirect(url_for('transactions'))

    book = query("SELECT * FROM books WHERE serial_no=?", (serial_no,), one=True)
    today = datetime.today().date()
    fine  = calc_fine(issue['return_date'])
    overdue_days = max(0, (today - datetime.strptime(issue['return_date'], '%Y-%m-%d').date()).days)

    if request.method == 'POST':
        fine_paid = request.form.get('fine_paid') == 'on'
        remarks   = request.form.get('remarks', '')

        if fine > 0 and not fine_paid:
            flash('Fine must be marked as paid before returning the book.', 'danger')
            return render_template('pay_fine.html', issue=issue, book=book,
                                   fine=fine, today=today, overdue_days=overdue_days)

        actual_return = session.get('return_date', today.isoformat())
        execute("""UPDATE issues SET actual_return_date=?,fine_calculated=?,fine_paid=?,
                   remarks=?,status='Returned' WHERE serial_no=? AND status='Active'""",
                (actual_return, fine, 1 if fine_paid else 0, remarks, serial_no))
        execute("UPDATE books SET status='Available' WHERE serial_no=?", (serial_no,))
        session.pop('return_serial', None)
        session.pop('return_date', None)
        flash('Book returned successfully!', 'success')
        return redirect(url_for('confirmation'))

    return render_template('pay_fine.html', issue=issue, book=book,
                           fine=fine, today=today, overdue_days=overdue_days)

# ─── REPORTS ──────────────────────────────────────────────────────────────────

@app.route('/reports')
@login_required
def reports():
    return render_template('reports.html')

@app.route('/reports/books')
@login_required
def report_books():
    books = query("SELECT * FROM books WHERE item_type='Book' ORDER BY name")
    return render_template('report_books.html', books=books, title='Master List of Books')

@app.route('/reports/movies')
@login_required
def report_movies():
    movies = query("SELECT * FROM books WHERE item_type='Movie' ORDER BY name")
    return render_template('report_books.html', books=movies, title='Master List of Movies')

@app.route('/reports/memberships')
@login_required
def report_memberships():
    return render_template('report_memberships.html',
                           memberships=query("SELECT * FROM memberships ORDER BY membership_id"))

@app.route('/reports/active_issues')
@login_required
def report_active_issues():
    issues = query("SELECT * FROM issues WHERE status='Active' ORDER BY issue_date DESC")
    return render_template('report_issues.html', issues=issues, title='Active Issues')

@app.route('/reports/overdue')
@login_required
def report_overdue():
    today = datetime.today().date().isoformat()
    raw = query("SELECT * FROM issues WHERE status='Active' AND return_date < ? ORDER BY return_date", (today,))
    issues = []
    for i in raw:
        d = dict(i)
        d['fine_calculated'] = calc_fine(i['return_date'])
        issues.append(d)
    return render_template('report_overdue.html', issues=issues)

@app.route('/reports/issue_requests')
@login_required
def report_issue_requests():
    return render_template('report_issue_requests.html',
                           issues=query("SELECT * FROM issues ORDER BY id DESC"))

# ─── MAINTENANCE ──────────────────────────────────────────────────────────────

@app.route('/maintenance')
@admin_required
def maintenance():
    return render_template('maintenance.html')

@app.route('/maintenance/add_membership', methods=['GET', 'POST'])
@admin_required
def add_membership():
    if request.method == 'POST':
        fn    = request.form.get('first_name','').strip()
        ln    = request.form.get('last_name','').strip()
        con   = request.form.get('contact','').strip()
        addr  = request.form.get('address','').strip()
        aadh  = request.form.get('aadhar','').strip()
        st    = request.form.get('start_date','').strip()
        mtype = request.form.get('membership_type','6months')

        if not all([fn, ln, con, addr, aadh, st]):
            flash('All fields are required.', 'danger')
            return render_template('add_membership.html')

        start  = datetime.strptime(st, '%Y-%m-%d').date()
        months = {'6months':6,'1year':12,'2years':24}
        end    = start + timedelta(days=30 * months.get(mtype, 6))
        mem_id = gen_membership_id()
        execute("""INSERT INTO memberships
            (membership_id,first_name,last_name,contact_number,contact_address,
             aadhar_card,start_date,end_date,membership_type)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (mem_id, fn, ln, con, addr, aadh, st, end.isoformat(), mtype))
        flash(f'Membership added! ID: {mem_id}', 'success')
        return redirect(url_for('confirmation'))
    return render_template('add_membership.html')

@app.route('/maintenance/update_membership', methods=['GET', 'POST'])
@admin_required
def update_membership():
    member = None
    if request.method == 'POST':
        action = request.form.get('action')
        mem_id = request.form.get('membership_id','').strip()
        member = query("SELECT * FROM memberships WHERE membership_id=?", (mem_id,), one=True)
        if not member:
            flash('Membership not found.', 'danger')
            return render_template('update_membership.html', member=None)
        if action == 'search':
            return render_template('update_membership.html', member=member)
        elif action == 'update':
            if request.form.get('remove') == 'on':
                execute("UPDATE memberships SET is_active=0 WHERE membership_id=?", (mem_id,))
                flash('Membership cancelled.', 'success')
            else:
                ext = request.form.get('extension','6months')
                months = {'6months':6,'1year':12,'2years':24}
                cur_end = datetime.strptime(member['end_date'], '%Y-%m-%d').date()
                new_end = cur_end + timedelta(days=30 * months.get(ext, 6))
                execute("UPDATE memberships SET end_date=? WHERE membership_id=?",
                        (new_end.isoformat(), mem_id))
                flash('Membership extended.', 'success')
            return redirect(url_for('confirmation'))
    return render_template('update_membership.html', member=member)

@app.route('/maintenance/add_book', methods=['GET', 'POST'])
@admin_required
def add_book():
    if request.method == 'POST':
        itype  = request.form.get('item_type','Book')
        name   = request.form.get('name','').strip()
        author = request.form.get('author','').strip()
        cat    = request.form.get('category','').strip()
        cost   = request.form.get('cost','0').strip()
        proc   = request.form.get('procurement_date','').strip()
        qty    = request.form.get('quantity','1').strip()

        if not all([name, author, cat, cost, proc]):
            flash('All fields are required.', 'danger')
            return render_template('add_book.html', categories=CATEGORIES)

        qty = int(qty) if qty.isdigit() and int(qty) > 0 else 1
        for _ in range(qty):
            sn = gen_serial_no(itype, cat)
            execute("INSERT INTO books (serial_no,name,author,category,item_type,cost,procurement_date) VALUES (?,?,?,?,?,?,?)",
                    (sn, name, author, cat, itype, float(cost), proc))
        flash(f'{qty} {itype}(s) added!', 'success')
        return redirect(url_for('confirmation'))
    return render_template('add_book.html', categories=CATEGORIES)

@app.route('/maintenance/update_book', methods=['GET', 'POST'])
@admin_required
def update_book():
    book  = None
    books = query("SELECT serial_no,name FROM books ORDER BY name")
    if request.method == 'POST':
        action    = request.form.get('action')
        serial_no = request.form.get('serial_no','').strip()
        book = query("SELECT * FROM books WHERE serial_no=?", (serial_no,), one=True)
        if not book:
            flash('Book not found.', 'danger')
            return render_template('update_book.html', book=None, books=books, categories=CATEGORIES)
        if action == 'search':
            return render_template('update_book.html', book=book, books=books, categories=CATEGORIES)
        elif action == 'update':
            new_name   = request.form.get('name', book['name']).strip()
            new_status = request.form.get('status', book['status'])
            execute("UPDATE books SET name=?,status=? WHERE serial_no=?",
                    (new_name, new_status, serial_no))
            flash('Book updated.', 'success')
            return redirect(url_for('confirmation'))
    return render_template('update_book.html', book=book, books=books, categories=CATEGORIES)

@app.route('/maintenance/user_management', methods=['GET', 'POST'])
@admin_required
def user_management():
    all_users = query("SELECT * FROM users ORDER BY name")
    if request.method == 'POST':
        utype     = request.form.get('user_type','new')
        name      = request.form.get('name','').strip()
        username  = request.form.get('username','').strip()
        password  = request.form.get('password','').strip()
        is_active = 1 if request.form.get('is_active') == 'on' else 0
        is_admin  = 1 if request.form.get('is_admin') == 'on' else 0

        if not name:
            flash('Name is required.', 'danger')
            return render_template('user_management.html', users=all_users)

        if utype == 'new':
            if not username or not password:
                flash('Username and password required for new users.', 'danger')
                return render_template('user_management.html', users=all_users)
            if query("SELECT id FROM users WHERE username=?", (username,), one=True):
                flash('Username already exists.', 'danger')
                return render_template('user_management.html', users=all_users)
            execute("INSERT INTO users (username,password,name,is_admin,is_active) VALUES (?,?,?,?,?)",
                    (username, password, name, is_admin, is_active))
        else:
            user = query("SELECT * FROM users WHERE username=?", (username,), one=True)
            if not user:
                flash('User not found.', 'danger')
                return render_template('user_management.html', users=all_users)
            if password:
                execute("UPDATE users SET name=?,is_admin=?,is_active=?,password=? WHERE username=?",
                        (name, is_admin, is_active, password, username))
            else:
                execute("UPDATE users SET name=?,is_admin=?,is_active=? WHERE username=?",
                        (name, is_admin, is_active, username))
        flash('User saved.', 'success')
        return redirect(url_for('confirmation'))
    return render_template('user_management.html', users=all_users)

@app.route('/confirmation')
@login_required
def confirmation():
    return render_template('confirmation.html')

@app.route('/cancel')
@login_required
def cancel():
    return render_template('cancel.html')

# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
