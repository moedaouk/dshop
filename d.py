# -*- coding: utf-8 -*-
import os, sqlite3, uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Optional, Tuple
from flask import Flask, request, redirect, url_for, render_template, session, flash
from werkzeug.utils import secure_filename
from jinja2 import FileSystemLoader

# -------- ReportLab + Arabic shaping --------
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rlcanvas
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

def ar_shape(text: str) -> str:
    try:
        if not text: return ""
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return text or ""

def has_ar(s: Optional[str]) -> bool:
    if not s: return False
    return any(0x0600 <= ord(ch) <= 0x06FF for ch in s)

# --------------- Config ----------------
APP_TITLE = "D-Inventory (Web)"
DB_FILE = "inventory.db"
STATIC_DIR = "static"
IMG_DIR = os.path.join(STATIC_DIR, "images").replace("\\", "/")
FONTS_DIR = "fonts"
ALLOWED_EXT = {"png","jpg","jpeg","gif","bmp"}

USERS = {
    "daouk": {"password": "killerkk88", "role": "admin"},
    "user":  {"password": "123",       "role": "standard"},
}

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)

app = Flask(__name__, static_folder=STATIC_DIR, template_folder="templates")
app.jinja_loader = FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates"))
app.secret_key = "replace-with-strong-secret"
# --------------- Auth gate (login required) --------------
from flask import abort

PUBLIC_ENDPOINTS = {"login", "static"}

@app.before_request
def _require_login():
    # allow static and login
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    # Some blueprints / None endpoints safety
    if not session.get("username"):
        # Force everyone to the login page first
        return redirect(url_for("login"))


# --------------- DB helpers ----------------
def db():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c

def ensure_column(table: str, name: str, type_sql: str):
    c = db(); cur = c.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if name not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {type_sql}")
        c.commit()
    c.close()

def init_db():
    c = db(); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_number TEXT UNIQUE NOT NULL,
        description TEXT,
        price REAL,
        image_path TEXT,
        stock INTEGER DEFAULT 0,
        min_stock INTEGER DEFAULT 0
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS quotes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        username TEXT,
        customer_name TEXT,
        customer_phone TEXT,
        customer_notes TEXT,
        total REAL NOT NULL,
        file_path TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS quote_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_id INTEGER NOT NULL,
        part_number TEXT,
        description TEXT,
        qty INTEGER,
        price REAL,
        subtotal REAL
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS movements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        item_id INTEGER,
        part_number TEXT NOT NULL,
        qty_change INTEGER NOT NULL,
        reason TEXT,
        ref_quote_id INTEGER,
        note TEXT
    )""")
    c.commit(); c.close()
    ensure_column("items", "min_stock", "INTEGER DEFAULT 0")
    ensure_column("items", "category", "TEXT")
    ensure_column("quotes", "customer_notes", "TEXT")
init_db()

def normalize_image_paths():
    c = db(); cur = c.cursor()
    cur.execute("SELECT id, image_path FROM items WHERE image_path IS NOT NULL AND image_path != ''")
    rows = cur.fetchall()
    changed = 0
    for r in rows:
        p = (r["image_path"] or "").replace("\\","/")
        if not p: continue
        if p.startswith("static/"): continue
        fname = os.path.basename(p)
        if not fname: continue
        fixed = f"static/images/{fname}"
        cur.execute("UPDATE items SET image_path=? WHERE id=?", (fixed, r["id"])); changed += 1
    if changed: c.commit()
    c.close()
normalize_image_paths()

# --------------- Auth helpers --------------
def current_user():
    u = session.get("username")
    if not u: return None
    r = USERS.get(u)
    if not r: return None
    return {"username": u, "role": r["role"]}

def require_role(*roles):
    u = current_user()
    return bool(u and u["role"] in roles)

# --------------- Utility -------------------
def img_public_url(p: Optional[str]) -> str:
    if not p: return ""
    p = p.replace("\\","/")
    if p.startswith("static/"): return "/" + p
    if "/static/" in p:
        s = p[p.find("/static/"):]
        return s if s.startswith("/") else "/" + s
    fname = os.path.basename(p)
    return f"/static/images/{fname}" if fname else ""

def register_ar_font():
    candidates = [
        os.path.join(FONTS_DIR,"NotoNaskhArabic-Regular.ttf"),
        os.path.join(FONTS_DIR,"Amiri-Regular.ttf"),
        os.path.join(FONTS_DIR,"ScheherazadeNew-Regular.ttf"),
        os.path.join(FONTS_DIR,"Arial.ttf"),
        os.path.join(FONTS_DIR,"Tahoma.ttf"),
    ]
    win_fonts = os.path.join(os.environ.get("WINDIR","C:\\Windows"), "Fonts")
    for f in ["NotoNaskhArabic-Regular.ttf","Amiri-Regular.ttf","ScheherazadeNew-Regular.ttf",
              "Tahoma.ttf","Arial.ttf","Times New Roman.ttf"]:
        candidates.append(os.path.join(win_fonts, f))
    for path in candidates:
        try:
            if os.path.exists(path):
                name = os.path.splitext(os.path.basename(path))[0]
                pdfmetrics.registerFont(TTFont(name, path))
                return name
        except Exception:
            continue
    return None

def get_low_stock_count_and_list() -> Tuple[int, list]:
    c = db(); cur = c.cursor()
    cur.execute("""SELECT id, part_number, description, price, stock, min_stock, image_path
                   FROM items
                   WHERE (stock IS NOT NULL AND min_stock IS NOT NULL AND stock <= min_stock) OR (stock IS NULL OR stock = 0)
                   ORDER BY stock ASC, part_number COLLATE NOCASE""")
    rows = cur.fetchall(); c.close()
    return len(rows), rows

# --------------- Templates -----------------
# (moved to /templates files)
# --------------- Routes -----------------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u, p = request.form.get("username","").strip(), request.form.get("password","")
        if u in USERS and USERS[u]["password"] == p:
            session["username"] = u
            flash(f"Signed in as {u} ({USERS[u]['role']})","success")
            return redirect(url_for("home"))
        flash("Invalid credentials","danger")
    low_count, _ = get_low_stock_count_and_list()
    return render_template("login.html", title=APP_TITLE, u=current_user(), low_count=low_count)

@app.route("/logout")
def logout():
    session.clear(); flash("Signed out","info"); return redirect(url_for("home"))


@app.route("/")
def home():
    q = request.args.get("q","").strip().lower()
    cat = request.args.get("cat","").strip()
    c = db(); cur = c.cursor()
    # categories
    cur.execute("SELECT DISTINCT COALESCE(NULLIF(TRIM(category),''),'Uncategorized') AS category FROM items ORDER BY category COLLATE NOCASE")
    cats = [r[0] for r in cur.fetchall()]
    # items
    base_q = "SELECT * FROM items"
    where = []; params = []
    if q:
        where.append("(LOWER(part_number) LIKE ? OR LOWER(description) LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if cat and cat.lower() != "all":
        if cat.lower() == "uncategorized":
            where.append("(category IS NULL OR TRIM(category)='')")
        else:
            where.append("LOWER(category)=?"); params += [cat.lower()]
    if where: base_q += " WHERE " + " AND ".join(where)
    base_q += " ORDER BY part_number COLLATE NOCASE"
    cur.execute(base_q, tuple(params)); items = cur.fetchall(); c.close()
    items_ui = []
    for r in items:
        d = dict(r); d["image_url"] = img_public_url(d.get("image_path")); items_ui.append(d)
    cart_total = sum((float(it["price"] or 0) * int(it["qty"])) for it in session.get("cart", {}).values())
    low_count, _ = get_low_stock_count_and_list()
    return render_template("home.html", title=APP_TITLE, items=items_ui, u=current_user(), query=q, cart_total=cart_total, low_count=low_count, categories=cats, selected_cat=cat or "All")

def _save_upload(file_storage, part_number):
    if not file_storage or not file_storage.filename: return None
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext.replace(".","") not in ALLOWED_EXT: return None
    safe = secure_filename(part_number) or uuid.uuid4().hex
    fname = f"{safe}-{uuid.uuid4().hex[:6]}{ext}"
    dest = os.path.join(IMG_DIR, fname)
    file_storage.save(dest)
    return f"static/images/{fname}"

@app.route("/item/new", methods=["GET","POST"])
def item_new():
    if not require_role("admin","standard"):
        flash("Sign in to add items.","warning"); return redirect(url_for("home"))
    if request.method == "POST":
        part = request.form.get("part","").strip()
        desc = request.form.get("desc","").strip()
        price_raw = request.form.get("price","").strip()
        stock_raw = request.form.get("stock","0").strip()
        min_stock_raw = request.form.get("min_stock","0").strip()
        category = request.form.get("category","").strip()
        img = request.files.get("image")
        if not part:
            flash("Part number is required","danger"); return redirect(request.url)
        try: price = float(Decimal(price_raw)) if price_raw else None
        except (InvalidOperation, ValueError):
            flash("Invalid price","danger"); return redirect(request.url)
        try: stock = int(stock_raw); min_stock = int(min_stock_raw)
        except Exception:
            flash("Stock/min stock must be integers","danger"); return redirect(request.url)
        img_path = _save_upload(img, part)
        c = db()
        try:
            c.execute("""INSERT INTO items(part_number,description,price,image_path,stock,min_stock,category)
                         VALUES(?,?,?,?,?,?,?)""", (part, desc, price, img_path, stock, min_stock, category))
            c.commit(); flash("Item saved","success"); return redirect(url_for("home"))
        except sqlite3.IntegrityError:
            flash("Duplicate part number","danger")
        finally: c.close()
    low_count, _ = get_low_stock_count_and_list()
    return render_template("item_form.html", title=APP_TITLE, u=current_user(), item=None, low_count=low_count)

@app.route("/item/<int:item_id>/edit", methods=["GET","POST"])
def item_edit(item_id):
    if not require_role("admin"):
        flash("Only admin can edit items.","warning"); return redirect(url_for("home"))
    c = db(); cur = c.cursor(); cur.execute("SELECT * FROM items WHERE id=?", (item_id,)); item = cur.fetchone()
    if not item:
        c.close(); flash("Item not found","danger"); return redirect(url_for("home"))
    if request.method == "POST":
        part = request.form.get("part","").strip()
        desc = request.form.get("desc","").strip()
        price_raw = request.form.get("price","").strip()
        stock_raw = request.form.get("stock","0").strip()
        min_stock_raw = request.form.get("min_stock","0").strip()
        category = request.form.get("category","").strip()
        img = request.files.get("image")
        if not part:
            flash("Part number is required","danger"); return redirect(request.url)
        try: price = float(Decimal(price_raw)) if price_raw else None
        except (InvalidOperation, ValueError):
            flash("Invalid price","danger"); return redirect(request.url)
        try: stock = int(stock_raw); min_stock = int(min_stock_raw)
        except Exception:
            flash("Stock/min stock must be integers","danger"); return redirect(request.url)
        img_path = item["image_path"]; new_img = _save_upload(img, part)
        if new_img: img_path = new_img
        cur.execute("""UPDATE items SET part_number=?,description=?,price=?,image_path=?,stock=?,min_stock=?,category=? WHERE id=?""",
                    (part, desc, price, img_path, stock, min_stock, category, item_id))
        c.commit(); c.close(); flash("Item updated","success"); return redirect(url_for("home"))
    c.close(); low_count, _ = get_low_stock_count_and_list()
    return render_template("item_form.html", title=APP_TITLE, u=current_user(), item=item, low_count=low_count)

@app.route("/item/<int:item_id>/delete", methods=["POST"])
def item_delete(item_id):
    if not require_role("admin"):
        flash("Only admin can delete.","warning"); return redirect(url_for("home"))
    c = db(); c.execute("DELETE FROM items WHERE id=?", (item_id,)); c.commit(); c.close()
    flash("Item deleted","success"); return redirect(url_for("home"))

# ----------- Cart & Movements -----------
@app.route("/cart/add/<int:item_id>", methods=["POST"])
def cart_add(item_id):
    qty_raw = request.form.get("qty","1").strip()
    try: qty = int(qty_raw); assert qty>0
    except Exception: flash("Qty must be positive integer","danger"); return redirect(url_for("home"))
    c = db(); cur = c.cursor(); cur.execute("SELECT id,part_number,description,price,stock FROM items WHERE id=?", (item_id,))
    row = cur.fetchone(); c.close()
    if not row:
        flash("Item not found","danger"); return redirect(url_for("home"))
    if qty > int(row["stock"] or 0):
        flash(f"Not enough stock for {row['part_number']} (avail: {row['stock']})","warning"); return redirect(url_for("home"))
    cart = session.setdefault("cart", {}); key = str(item_id)
    if key in cart:
        if cart[key]["qty"] + qty > int(row["stock"] or 0):
            flash(f"Already in cart: {cart[key]['qty']}. Available: {row['stock']}","warning"); return redirect(url_for("home"))
        cart[key]["qty"] += qty
    else:
        cart[key] = {"part": row["part_number"], "desc": row["description"] or "", "price": float(row["price"] or 0), "qty": qty}
    session.modified = True; flash("Added to cart","success"); return redirect(url_for("home"))

@app.route("/cart/remove/<int:item_id>", methods=["POST"])
def cart_remove(item_id):
    cart = session.setdefault("cart", {}); cart.pop(str(item_id), None); session.modified = True
    flash("Removed from cart","info"); return redirect(url_for("home"))

@app.route("/cart/clear", methods=["POST"])
def cart_clear():
    session["cart"] = {}; flash("Cart cleared","info"); return redirect(url_for("home"))

# ---------- Export PDF (Sale) -----------
@app.route("/quote/export", methods=["POST"])
def quote_export():
    cart = session.get("cart", {})
    if not cart: flash("Cart is empty","warning"); return redirect(url_for("home"))
    # Verify stock
    c = db(); cur = c.cursor()
    for key, it in cart.items():
        cur.execute("SELECT stock,part_number FROM items WHERE id=?", (int(key),)); r = cur.fetchone()
        if not r or it["qty"] > int(r["stock"] or 0):
            c.close(); flash(f"Stock changed for {it['part']}","danger"); return redirect(url_for("home"))

    cust_name = request.form.get("cust_name","").strip()
    cust_phone = request.form.get("cust_phone","").strip()
    cust_notes = request.form.get("cust_notes","").strip()

    if not REPORTLAB_OK:
        flash("ReportLab not installed","danger"); return redirect(url_for("home"))

    out_name = f"quote_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    out_path = os.path.join(STATIC_DIR, out_name)
    cpdf = rlcanvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    header_h = 18*mm; cpdf.setFillColor(colors.lightgrey); cpdf.rect(0, height-header_h, width, header_h, fill=1, stroke=0)
    cpdf.setFillColor(colors.black); cpdf.setFont("Helvetica-Bold", 14); cpdf.drawCentredString(width/2, height-header_h+6*mm, "D-Inventory Quotation")
    y = height - header_h - 10*mm

    cpdf.setFont("Helvetica", 10); user = current_user()
    cpdf.drawString(20*mm, y, f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if user: cpdf.drawRightString(width-20*mm, y, f"User: {user['username']}"); y -= 8*mm

    # Customer block
    cpdf.setFont("Helvetica-Bold", 10); cpdf.drawString(20*mm, y, "Customer:"); cpdf.setFont("Helvetica",10)
    ar_font = register_ar_font()
    line_text = f"{cust_name}  |  {cust_phone}"
    if ar_font and (has_ar(cust_name) or has_ar(cust_phone)):
        cpdf.setFont(ar_font, 10); cpdf.drawString(42*mm, y, ar_shape(line_text)[:100]); cpdf.setFont("Helvetica",10)
    else:
        cpdf.drawString(42*mm, y, line_text[:100]); 
    y -= 6*mm
    if cust_notes:
        if ar_font and has_ar(cust_notes):
            cpdf.setFont(ar_font, 10); cpdf.drawString(42*mm, y, ar_shape(cust_notes)[:110]); cpdf.setFont("Helvetica",10)
        else:
            cpdf.drawString(42*mm, y, cust_notes[:110])
        y -= 6*mm
    y -= 2*mm

    x_cols = [20*mm, 60*mm, 135*mm, 155*mm, 175*mm]
    cpdf.setFont("Helvetica-Bold", 11); 
    for x, htxt in zip(x_cols, ["Part #","Description","Qty","Price","Subtotal"]): cpdf.drawString(x, y, htxt)
    y -= 6*mm; cpdf.line(20*mm, y, width-20*mm, y); y -= 6*mm

    total = 0.0
    for it in cart.values():
        part, desc, qty, price = it["part"], it["desc"], it["qty"], float(it["price"])
        subtotal = price * qty; total += subtotal
        cpdf.setFont("Helvetica",10); cpdf.drawString(x_cols[0], y, str(part))
        if ar_font and has_ar(desc or ""):
            cpdf.setFont(ar_font, 10); cpdf.drawRightString(x_cols[2]-2*mm, y, ar_shape(desc or "")); cpdf.setFont("Helvetica", 10)
        else:
            cpdf.drawString(x_cols[1], y, (desc or "")[:60])
        cpdf.drawRightString(x_cols[2]+15*mm, y, str(qty))
        cpdf.drawRightString(x_cols[3]+15*mm, y, f"{price:,.2f}")
        cpdf.drawRightString(x_cols[4]+15*mm, y, f"{subtotal:,.2f}")
        y -= 6*mm
        if y < 40*mm:
            cpdf.showPage(); cpdf.setFillColor(colors.lightgrey); cpdf.rect(0, height-header_h, width, header_h, fill=1, stroke=0)
            cpdf.setFillColor(colors.black); cpdf.setFont("Helvetica-Bold", 14); cpdf.drawCentredString(width/2, height-header_h+6*mm, "D-Inventory Quotation")
            y = height - header_h - 10*mm

    y -= 6*mm; cpdf.line(120*mm, y, width-20*mm, y); y -= 8*mm
    cpdf.setFont("Helvetica-Bold", 12); cpdf.drawRightString(175*mm, y, "Total:"); cpdf.drawRightString(width-20*mm, y, f"{total:,.2f}")
    cpdf.setFont("Helvetica",9); cpdf.setFillColor(colors.grey); cpdf.drawCentredString(width/2, 10*mm, "Generated by D-Inventory by M. Daouk"); cpdf.setFillColor(colors.black)
    cpdf.showPage(); cpdf.save()

    # Record sale + decrement stock
    cur.execute("""INSERT INTO quotes(created_at,username,customer_name,customer_phone,customer_notes,total,file_path)
                   VALUES(?,?,?,?,?,?,?)""",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 user["username"] if user else None,
                 cust_name, cust_phone, cust_notes, total, out_path.replace("\\","/")))
    qid = cur.lastrowid
    for key, it in cart.items():
        item_id = int(key)
        cur.execute("SELECT part_number,stock FROM items WHERE id=?", (item_id,)); r = cur.fetchone()
        cur.execute("""INSERT INTO quote_items(quote_id,part_number,description,qty,price,subtotal)
                       VALUES(?,?,?,?,?,?)""", (qid, r["part_number"], it["desc"], it["qty"], it["price"], it["price"]*it["qty"]))
        new_stock = int(r["stock"] or 0) - int(it["qty"]); cur.execute("UPDATE items SET stock=? WHERE id=?", (new_stock, item_id))
        cur.execute("""INSERT INTO movements(created_at,item_id,part_number,qty_change,reason,ref_quote_id,note)
                       VALUES(?,?,?,?,?,?,?)""", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), item_id, r["part_number"], -int(it["qty"]), "sale", qid, None))
    c.commit(); c.close(); session["cart"] = {}
    flash(f"Saved PDF & updated stock (Quote #{qid})","success"); return redirect(url_for("history"))

@app.route("/history")
def history():
    c = db(); cur = c.cursor(); cur.execute("SELECT id,created_at,username,customer_name,customer_phone,total,file_path FROM quotes ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall(); c.close()
    low_count, _ = get_low_stock_count_and_list()
    return render_template("history.html", title=APP_TITLE, rows=rows, u=current_user(), low_count=low_count)

@app.route("/history/<int:quote_id>")
def history_items(quote_id):
    c = db(); cur = c.cursor()
    cur.execute("SELECT part_number,description,qty,price,subtotal FROM quote_items WHERE quote_id=?", (quote_id,)); items = cur.fetchall()
    cur.execute("SELECT file_path FROM quotes WHERE id=?", (quote_id,)); pdf = cur.fetchone(); c.close()
    low_count, _ = get_low_stock_count_and_list()
    return render_template("history_items.html", title=APP_TITLE, items=items, quote_id=quote_id, pdf=pdf["file_path"] if pdf else None, u=current_user(), low_count=low_count)

@app.route("/movements")
def movements():
    start = request.args.get("start","").strip() or None; end = request.args.get("end","").strip() or None
    c = db(); cur = c.cursor()
    q = "SELECT created_at,part_number,qty_change,reason,COALESCE(ref_quote_id,''),COALESCE(note,'') FROM movements"
    params = ()
    if start and end: q += " WHERE date(created_at) BETWEEN ? AND ?"; params = (start, end)
    elif start: q += " WHERE date(created_at) >= ?"; params = (start,)
    elif end: q += " WHERE date(created_at) <= ?"; params = (end,)
    q += " ORDER BY created_at DESC"; cur.execute(q, params); rows = cur.fetchall(); c.close()
    low_count, _ = get_low_stock_count_and_list()
    return render_template("movements.html", title=APP_TITLE, rows=rows, u=current_user(), start=start or "", end=end or "", low_count=low_count)

# ---------------- Reports -----------------
@app.route("/reports/low-stock")
def report_low_stock():
    _, rows = get_low_stock_count_and_list()
    low_count = len(rows)
    return render_template("report_low.html", title=APP_TITLE, rows=rows, u=current_user(), low_count=low_count)

@app.route("/reports/top-selling")
def report_top_selling():
    start = request.args.get("start","").strip() or None
    end = request.args.get("end","").strip() or None
    c = db(); cur = c.cursor()
    q = """
    SELECT qi.part_number AS part_number,
           COALESCE(i.description, qi.description) AS description,
           SUM(qi.qty) AS qty,
           SUM(qi.subtotal) AS sales
    FROM quote_items qi
    LEFT JOIN quotes q ON q.id = qi.quote_id
    LEFT JOIN items i ON i.part_number = qi.part_number
    WHERE 1=1
    """
    params = []
    if start:
        q += " AND date(q.created_at) >= ?"
        params.append(start)
    if end:
        q += " AND date(q.created_at) <= ?"
        params.append(end)
    q += " GROUP BY qi.part_number ORDER BY qty DESC, sales DESC LIMIT 200"
    cur.execute(q, tuple(params)); rows = cur.fetchall(); c.close()
    low_count, _ = get_low_stock_count_and_list()
    return render_template("report_top.html", title=APP_TITLE, rows=rows, u=current_user(), start=start or "", end=end or "", low_count=low_count)

if __name__ == "__main__":
    app.jinja_loader = app.jinja_loader  # no-op to keep loader
    app.run(host="0.0.0.0", port=5000, debug=True)
