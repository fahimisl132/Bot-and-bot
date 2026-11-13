# -*- coding: utf-8 -*-
"""
Shop Bot - Step 1
  * Main menu (customer side)
  * Admin panel
  * Product Management (Add / Edit / Set Price / Remove / Hide-Show)
  * Stock Management (Excel / CSV / Manual, view / export / delete / history)

Requirements:  pip install -r requirements.txt
Setup:         copy .env.example to .env and fill BOT_TOKEN and OWNER_ID
Run:           python shop_bot.py
"""
import asyncio
import csv
import html
import io
import json
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ============================== CONFIG ==============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env(path):
    """Tiny .env reader (KEY=VALUE per line) so no extra package is needed."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
_db_setting = os.getenv("DB_FILE", "shop.db")
DB_FILE = _db_setting if os.path.isabs(_db_setting) else os.path.join(BASE_DIR, _db_setting)
os.makedirs(os.path.dirname(DB_FILE) or BASE_DIR, exist_ok=True)
CUR = "$"
WELCOME = "👋 Welcome! Choose an option from the menu below."
# ====================================================================

logging.basicConfig(level=logging.INFO)

# ------------------------------ DATABASE ------------------------------
db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA foreign_keys=ON")
db.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
    tg_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, reg_date TEXT,
    balance REAL DEFAULT 0, total_deposit REAL DEFAULT 0, total_purchase REAL DEFAULT 0,
    referral_income REAL DEFAULT 0, referred_by INTEGER,
    is_reseller INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, lang TEXT DEFAULT 'en');
CREATE TABLE IF NOT EXISTS admins(tg_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS categories(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_sellable INTEGER DEFAULT 0,
    price REAL DEFAULT 0,
    hidden INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS stock(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    status TEXT DEFAULT 'available',
    added_at TEXT, added_by INTEGER, sold_at TEXT, sold_to INTEGER);
CREATE INDEX IF NOT EXISTS idx_stock_prod ON stock(product_id, status);
CREATE TABLE IF NOT EXISTS stock_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_path TEXT, action TEXT, qty INTEGER,
    admin_id INTEGER, created_at TEXT);
"""
)


def q(sql, args=()):
    return db.execute(sql, args).fetchall()


def q1(sql, args=()):
    return db.execute(sql, args).fetchone()


def run(sql, args=()):
    cur = db.execute(sql, args)
    db.commit()
    return cur


# Migration for databases created by an earlier version.
if "fields" not in [r["name"] for r in q("PRAGMA table_info(stock)")]:
    run("ALTER TABLE stock ADD COLUMN fields TEXT")


# ------------------------------ HELPERS ------------------------------
def esc(s):
    return html.escape(str(s))


def is_admin(uid):
    return bool(uid) and (
        uid == OWNER_ID or q1("SELECT 1 FROM admins WHERE tg_id=?", (uid,)) is not None
    )


def ikb(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
            for row in rows
        ]
    )


def ensure_user(u, ref=None):
    if q1("SELECT 1 FROM users WHERE tg_id=?", (u.id,)):
        run(
            "UPDATE users SET username=?, full_name=? WHERE tg_id=?",
            (u.username, u.full_name, u.id),
        )
        return
    if ref and (ref == u.id or not q1("SELECT 1 FROM users WHERE tg_id=?", (ref,))):
        ref = None
    run(
        "INSERT INTO users(tg_id, username, full_name, reg_date, referred_by) VALUES(?,?,?,?,?)",
        (u.id, u.username, u.full_name, datetime.now().strftime("%Y-%m-%d %H:%M"), ref),
    )


async def show(target, view):
    """Edit the callback message, or send a new message for a normal Message."""
    text, keyboard = view
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as e:
            if "not modified" not in str(e).lower():
                raise
    else:
        await target.answer(text, reply_markup=keyboard)


# --------------------------- CATEGORY (PRODUCT TREE) ---------------------------
def cat(cid):
    return q1("SELECT * FROM categories WHERE id=?", (cid,))


def children(pid):
    return q("SELECT * FROM categories WHERE parent_id IS ? ORDER BY position, id", (pid,))


def has_children(cid):
    return q1("SELECT 1 FROM categories WHERE parent_id=?", (cid,)) is not None


def path_of(cid):
    parts = []
    while cid:
        c = cat(cid)
        if not c:
            break
        parts.append(c["name"])
        cid = c["parent_id"]
    return " › ".join(reversed(parts))


def add_cat(pid, name):
    pos = q1(
        "SELECT COALESCE(MAX(position),0)+1 AS p FROM categories WHERE parent_id IS ?",
        (pid,),
    )["p"]
    return run(
        "INSERT INTO categories(parent_id, name, position) VALUES(?,?,?)",
        (pid, name, pos),
    ).lastrowid


def name_taken(pid, name, exclude=0):
    return (
        q1(
            "SELECT 1 FROM categories WHERE parent_id IS ? AND lower(name)=lower(?) AND id!=?",
            (pid, name, exclude),
        )
        is not None
    )


def count_descendants(cid):
    r = q1(
        "WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT c.id FROM categories c JOIN t ON c.parent_id=t.id) "
        "SELECT COUNT(*) AS n FROM t",
        (cid,),
    )
    return r["n"] - 1


def is_visible(cid):
    while cid:
        c = cat(cid)
        if not c or c["hidden"]:
            return False
        cid = c["parent_id"]
    return True


def node_icon(c):
    if has_children(c["id"]):
        return "📁"
    return "✅" if c["is_sellable"] else "🔹"


def valid_name(s):
    s = (s or "").strip()
    return s if 0 < len(s) <= 40 else None


def parse_price(s):
    try:
        v = float((s or "").strip().replace(",", ".").replace("$", ""))
    except ValueError:
        return None
    return v if 0 <= v < 1_000_000 else None


# ------------------------------ STATES ------------------------------
class AddProd(StatesGroup):
    name = State()
    choose = State()


class EditP(StatesGroup):
    rename = State()
    addsub = State()
    price = State()


# ------------------------------ ROUTERS ------------------------------
main_r = Router()
admin_r = Router()
admin_r.message.filter(F.from_user.id.func(is_admin))
admin_r.callback_query.filter(F.from_user.id.func(is_admin))

# ============================== CUSTOMER SIDE ==============================
MENU_ROWS = [
    ["👤 Profile", "🛒 Buy Product"],
    ["🎁 Referral", "💰 Deposit"],
    ["📦 My Order", "🆘 Support"],
    ["🤝 Apply as Reseller", "🌐 Language"],
]
SOON_TEXTS = [
    "🎁 Referral", "💰 Deposit", "📦 My Order", "🆘 Support",
    "🤝 Apply as Reseller", "🌐 Language",
]


def main_kb(uid):
    rows = [[KeyboardButton(text=t) for t in r] for r in MENU_ROWS]
    if is_admin(uid):
        rows.append([KeyboardButton(text="🛠 Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@main_r.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    ref = int(command.args) if command.args and command.args.isdigit() else None
    ensure_user(m.from_user, ref)
    await m.answer(WELCOME, reply_markup=main_kb(m.from_user.id))


@main_r.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Cancelled.", reply_markup=main_kb(m.from_user.id))


@main_r.message(Command("id"))
async def cmd_id(m: Message):
    await m.answer(f"Your Telegram ID: <code>{m.from_user.id}</code>")


@main_r.message(F.text == "👤 Profile")
async def menu_profile(m: Message, state: FSMContext):
    await state.clear()
    u = m.from_user
    ensure_user(u)
    r = q1("SELECT * FROM users WHERE tg_id=?", (u.id,))
    refs = q1("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (u.id,))["n"]
    uname = f"@{u.username}" if u.username else "—"
    await m.answer(
        "👤 <b>My Profile</b>\n\n"
        f"Telegram: {esc(u.full_name)}\n"
        f"Telegram ID: <code>{u.id}</code>\n"
        f"Username: {esc(uname)}\n"
        f"Registration: {r['reg_date']}\n"
        f"Wallet Balance: {CUR}{r['balance']:g}\n"
        f"Total Deposit: {CUR}{r['total_deposit']:g}\n"
        f"Total Purchase: {CUR}{r['total_purchase']:g}\n"
        f"Referral Income: {CUR}{r['referral_income']:g}\n"
        f"Total Referrals: {refs}\n"
        f"Reseller Status: {'✅ Reseller' if r['is_reseller'] else 'Regular customer'}"
    )


def r_buy(cid):
    if cid:
        c = cat(cid)
        if not c or not is_visible(cid):
            return "This product is not available.", ikb([[("⬅️ Back", "bp:0")]])
        if c["is_sellable"] and not has_children(cid):
            stock = stock_count(cid)
            text = (
                f"🛍 <b>{esc(path_of(cid))}</b>\n\n💵 Price: <b>{CUR}{c['price']:g}</b>\n"
                + (f"📦 In stock: <b>{stock}</b>" if stock else "❌ Out of stock")
            )
            return text, ikb(
                [[("🛒 Buy now", f"bp:buy:{cid}")],
                 [("⬅️ Back", f"bp:{c['parent_id'] or 0}")]]
            )
    kids = [
        k for k in children(cid or None)
        if not k["hidden"] and (k["is_sellable"] or has_children(k["id"]))
    ]
    if not kids:
        text = "🛒 <b>Buy Product</b>\n\nNo products available right now."
    else:
        text = (
            "🛒 <b>Buy Product</b>\n"
            + (f"📍 {esc(path_of(cid))}\n" if cid else "")
            + "\nChoose an option:"
        )
    rows = []
    for k in kids:
        label = (
            f"{k['name']} — {CUR}{k['price']:g}"
            if k["is_sellable"] and not has_children(k["id"])
            else f"📂 {k['name']}"
        )
        rows.append([(label, f"bp:{k['id']}")])
    if cid:
        parent_id = cat(cid)["parent_id"] or 0
        rows.append([("⬅️ Back", f"bp:{parent_id}")])
    return text, ikb(rows)


@main_r.message(F.text == "🛒 Buy Product")
async def menu_buy(m: Message, state: FSMContext):
    await state.clear()
    ensure_user(m.from_user)
    await show(m, r_buy(0))


@main_r.callback_query(F.data.regexp(r"^bp:\d+$"))
async def cb_buy_nav(cb: CallbackQuery):
    await show(cb, r_buy(int(cb.data.split(":")[1])))
    await cb.answer()


@main_r.callback_query(F.data.startswith("bp:buy:"))
async def cb_buy_now(cb: CallbackQuery):
    await cb.answer(
        "🚧 Stock & purchase will be enabled in the next step.",
        show_alert=True,
    )


@main_r.message(F.text.in_(SOON_TEXTS))
async def menu_soon(m: Message, state: FSMContext):
    await state.clear()
    ensure_user(m.from_user)
    await m.answer("🚧 This section will be added in the next step.")


# ============================== ADMIN PANEL ==============================
def r_admin():
    items = [
        ("📦 Product Management", "pm:home"),
        ("🗂 Stock Management", "sm:home"),
        ("🧾 Order Management", "soon:order"),
        ("💳 Payment Management", "soon:payment"),
        ("👥 User Management", "soon:user"),
        ("🎁 Referral Settings", "soon:ref"),
        ("🤝 Reseller Management", "soon:reseller"),
        ("🛡️ Admin Management", "soon:admin"),
        ("⚙️ Bot Settings", "soon:settings"),
        ("📊 Statistics", "soon:stats"),
    ]
    rows = [items[i:i + 2] for i in range(0, len(items), 2)]
    return "🛠 <b>ADMIN CONTROL PANEL</b>", ikb(rows)


@admin_r.message(F.text == "🛠 Admin Panel")
@admin_r.message(Command("admin"))
async def open_admin(m: Message, state: FSMContext):
    await state.clear()
    await show(m, r_admin())


@admin_r.callback_query(F.data == "adm:home")
async def cb_admin_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, r_admin())
    await cb.answer()


@admin_r.callback_query(F.data.startswith("soon:"))
async def cb_soon(cb: CallbackQuery):
    await cb.answer(
        "🚧 This section will be built in the next step.",
        show_alert=True,
    )


# ============================== PRODUCT MANAGEMENT ==============================
def r_menu():
    return "📦 <b>PRODUCT MANAGEMENT</b>", ikb(
        [
            [("➕ Add Product", "pm:add")],
            [("✏️ Edit Product", "pm:v:n:0")],
            [("💵 Set Price", "pm:v:p:0")],
            [("🗑️ Remove Product", "pm:v:d:0")],
            [("👁️ Hide / Show Product", "pm:v:h:0")],
            [("⬅️ Back", "adm:home")],
        ]
    )


def r_level(parent, sess):
    nodes = [c for c in children(parent) if c["id"] in sess]
    rows, line = [], []
    for c in nodes:
        line.append((f"{node_icon(c)} {c['name']}", f"ap:o:{c['id']}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([("➕ Add another", "ap:m"), ("✔️ Done", "ap:d")])
    rows.append([("⏹ Stop", "pm:home")])
    where = f"📍 <b>{esc(path_of(parent))}</b>\n" if parent else ""
    text = (
        f"{where}Select an option and proceed.\n\n"
        "• Tap an item to add sub-options inside it.\n"
        "• Press <b>Done</b> when the remaining items are final sellable products."
    )
    return text, ikb(rows)


def finish_level(parent, sess):
    for c in children(parent):
        if c["id"] in sess and not c["is_sellable"] and not has_children(c["id"]):
            run("UPDATE categories SET is_sellable=1 WHERE id=?", (c["id"],))
    cur = parent
    while cur is not None:
        up = cat(cur)["parent_id"]
        pending = [
            c for c in children(up)
            if c["id"] in sess and not c["is_sellable"] and not has_children(c["id"])
        ]
        if pending:
            return ("level", up)
        cur = up
    return None


def r_node(cid):
    back_menu = [("🏠 Product Management", "pm:home")]
    if cid == 0:
        kids = children(None)
        rows = [[(f"{node_icon(c)} {c['name']}", f"pm:v:n:{c['id']}")] for c in kids]
        rows.append([("➕ Add Product", "pm:add")])
        rows.append(back_menu)
        text = "✏️ <b>Edit Product</b>\n\n" + (
            "Select a product:" if kids else "No products yet."
        )
        return text, ikb(rows)
    c = cat(cid)
    if not c:
        return "Item not found.", ikb([back_menu])
    kids = children(cid)
    if kids:
        kind = "Folder (has options inside)"
    elif c["is_sellable"]:
        kind = "Sellable product"
    else:
        kind = "Empty (not sellable yet)"
    lines = [
        f"✏️ <b>{esc(c['name'])}</b>",
        f"📍 {esc(path_of(cid))}",
        f"Type: {kind}",
    ]
    if c["is_sellable"] and not kids:
        lines.append(f"Price: {CUR}{c['price']:g}")
    lines.append("Status: " + ("🙈 Hidden" if c["hidden"] else "👁 Visible"))
    rows = [[(f"{node_icon(k)} {k['name']}", f"pm:v:n:{k['id']}")] for k in kids]
    rows.append(
        [("✏️ Rename", f"pm:ren:{cid}"),
         ("➕ Add sub-option", f"pm:sub:{cid}")]
    )
    if c["is_sellable"] and not kids:
        rows.append([("💵 Set price", f"pm:pr:{cid}")])
    elif not kids:
        rows.append([("✅ Make sellable", f"pm:ms:{cid}")])
    rows.append(
        [("🙈 Hide" if not c["hidden"] else "👁 Show", f"pm:hv:{cid}:e"),
         ("🗑 Remove", f"pm:x:{cid}:e")]
    )
    rows.append(
        [("⬆️ Up", f"pm:v:n:{c['parent_id'] or 0}"),
         ("🏠 Menu", "pm:home")]
    )
    return "\n".join(lines), ikb(rows)


def r_browse(mode, pid):
    kids = children(pid or None)
    if mode == "d":
        title = (
            "🗑️ <b>Remove Product</b>\n"
            "Tap 🗑 next to the item you want to delete."
        )
    else:
        title = (
            "👁️ <b>Hide / Show Product</b>\n"
            "Hidden items are invisible to customers."
        )
    if pid:
        title += f"\n📍 {esc(path_of(pid))}"
    if not kids:
        title += "\n\nNothing here."
    rows = []
    for c in kids:
        label = f"{'🙈 ' if c['hidden'] else ''}{node_icon(c)} {c['name']}"
        left = (
            (label, f"pm:v:{mode}:{c['id']}")
            if has_children(c["id"])
            else (label, f"pm:info:{c['id']}")
        )
        if mode == "d":
            right = ("🗑", f"pm:x:{c['id']}:d")
        else:
            right = (
                "👁 Show" if c["hidden"] else "🙈 Hide",
                f"pm:hv:{c['id']}:h",
            )
        rows.append([left, right])
    nav = []
    if pid:
        nav.append(("⬆️ Up", f"pm:v:{mode}:{cat(pid)['parent_id'] or 0}"))
    nav.append(("🏠 Menu", "pm:home"))
    rows.append(nav)
    return title, ikb(rows)


PER_PAGE = 8


def r_price(page):
    items = q("SELECT * FROM categories WHERE is_sellable=1 ORDER BY id")
    if not items:
        return (
            "💵 <b>Set Price</b>\n\nNo sellable products yet. Add a product first.",
            ikb([[("🏠 Product Management", "pm:home")]]),
        )
    pages = (len(items) + PER_PAGE - 1) // PER_PAGE
    page = max(0, min(page, pages - 1))
    rows = []
    for c in items[page * PER_PAGE:(page + 1) * PER_PAGE]:
        rows.append(
            [(f"{path_of(c['id'])} • {CUR}{c['price']:g}", f"pm:ps:{c['id']}:{page}")]
        )
    nav = []
    if page > 0:
        nav.append(("◀️", f"pm:v:p:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"pm:v:p:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("🏠 Product Management", "pm:home")])
    return (
        f"💵 <b>Set Price</b>  (page {page + 1}/{pages})\n\n"
        "Select a sellable product:",
        ikb(rows),
    )


def render_view(key):
    kind, arg = key.split(":")
    arg = int(arg)
    if kind == "n":
        return r_node(arg)
    if kind in ("d", "h"):
        return r_browse(kind, arg)
    return r_price(arg)


def cancel_kb(key):
    return ikb([[("❌ Cancel", f"pm:v:{key}")]])


@admin_r.callback_query(F.data == "pm:home")
async def pm_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, r_menu())
    await cb.answer()


@admin_r.callback_query(F.data.startswith("pm:v:"))
async def pm_view(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, render_view(cb.data[5:]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("pm:info:"))
async def pm_info(cb: CallbackQuery):
    c = cat(int(cb.data.split(":")[2]))
    if not c:
        return await cb.answer("Not found", show_alert=True)
    kind = "Sellable product" if c["is_sellable"] else "Empty item"
    extra = f"\nPrice: {CUR}{c['price']:g}" if c["is_sellable"] else ""
    await cb.answer(f"{path_of(c['id'])}\n{kind}{extra}", show_alert=True)


# ---- ADD PRODUCT flow ----
@admin_r.callback_query(F.data == "pm:add")
async def pm_add(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddProd.name)
    await state.update_data(parent=0, sess=[])
    await show(
        cb,
        (
            "➕ <b>Add Product</b>\n\nEnter the name of the product:",
            ikb([[("❌ Cancel", "pm:home")]]),
        ),
    )
    await cb.answer()


@admin_r.message(AddProd.name, F.text)
async def add_name(m: Message, state: FSMContext):
    d = await state.get_data()
    parent = d["parent"] or None
    name = valid_name(m.text)
    if not name:
        return await m.answer("⚠️ The name must be 1–40 characters. Try again:")
    if name_taken(parent, name):
        return await m.answer(
            "⚠️ This name already exists here. Enter a different name:"
        )
    cid = add_cat(parent, name)
    sess = d["sess"] + [cid]
    await state.update_data(sess=sess)
    await state.set_state(AddProd.choose)
    await show(m, r_level(parent, sess))


@admin_r.callback_query(AddProd.choose, F.data.startswith("ap:o:"))
async def add_open(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    cid = int(cb.data.split(":")[2])
    c = cat(cid)
    if not c or cid not in d["sess"]:
        return await cb.answer("Not available", show_alert=True)
    await state.update_data(parent=cid)
    await state.set_state(AddProd.name)
    text = (
        f"📍 <b>{esc(path_of(cid))}</b>\n\n"
        f"Enter the name of the subcategory of <b>{esc(c['name'])}</b>:"
    )
    await show(cb, (text, ikb([[("⏹ Stop", "pm:home")]])))
    await cb.answer()


@admin_r.callback_query(AddProd.choose, F.data == "ap:m")
async def add_more(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    parent = d["parent"] or None
    await state.set_state(AddProd.name)
    if parent:
        text = (
            f"📍 <b>{esc(path_of(parent))}</b>\n\n"
            f"Enter the name of the another subcategory of "
            f"<b>{esc(cat(parent)['name'])}</b>:"
        )
    else:
        text = "Enter the name of the another product:"
    await show(cb, (text, ikb([[("⏹ Stop", "pm:home")]])))
    await cb.answer()


@admin_r.callback_query(AddProd.choose, F.data == "ap:d")
async def add_done(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    parent = d["parent"] or None
    result = finish_level(parent, d["sess"])
    if result is None:
        await state.clear()
        await show(
            cb,
            (
                "✅ <b>All products successfully added.</b>",
                ikb(
                    [[("➕ Add Product", "pm:add")],
                     [("🏠 Product Management", "pm:home")]]
                ),
            ),
        )
    else:
        up = result[1]
        await state.update_data(parent=up or 0)
        await show(cb, r_level(up, d["sess"]))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("ap:"))
async def add_expired(cb: CallbackQuery):
    await cb.answer(
        "This page expired. Use Edit Product to change items.",
        show_alert=True,
    )


# ---- EDIT actions ----
@admin_r.callback_query(F.data.startswith("pm:ren:"))
async def pm_ren(cb: CallbackQuery, state: FSMContext):
    cid = int(cb.data.split(":")[2])
    c = cat(cid)
    if not c:
        return await cb.answer("Not found", show_alert=True)
    await state.clear()
    await state.set_state(EditP.rename)
    await state.update_data(node=cid, back=f"n:{cid}")
    await show(
        cb,
        (
            f"✏️ Enter the new name for <b>{esc(c['name'])}</b>:",
            cancel_kb(f"n:{cid}"),
        ),
    )
    await cb.answer()


@admin_r.message(EditP.rename, F.text)
async def do_rename(m: Message, state: FSMContext):
    d = await state.get_data()
    c = cat(d["node"])
    name = valid_name(m.text)
    if not c:
        await state.clear()
        return await m.answer("Item not found.")
    if not name:
        return await m.answer("⚠️ The name must be 1–40 characters. Try again:")
    if name_taken(c["parent_id"], name, exclude=c["id"]):
        return await m.answer(
            "⚠️ This name already exists here. Enter a different name:"
        )
    run("UPDATE categories SET name=? WHERE id=?", (name, c["id"]))
    await state.clear()
    text, keyboard = r_node(c["id"])
    await m.answer("✅ Renamed.\n\n" + text, reply_markup=keyboard)


@admin_r.callback_query(F.data.startswith("pm:sub:"))
async def pm_sub(cb: CallbackQuery, state: FSMContext):
    cid = int(cb.data.split(":")[2])
    c = cat(cid)
    if not c:
        return await cb.answer("Not found", show_alert=True)
    await state.clear()
    await state.set_state(EditP.addsub)
    await state.update_data(node=cid)
    await show(
        cb,
        (
            f"➕ Enter the name of the new sub-option inside <b>{esc(c['name'])}</b>:",
            cancel_kb(f"n:{cid}"),
        ),
    )
    await cb.answer()


@admin_r.message(EditP.addsub, F.text)
async def do_addsub(m: Message, state: FSMContext):
    d = await state.get_data()
    c = cat(d["node"])
    name = valid_name(m.text)
    if not c:
        await state.clear()
        return await m.answer("Item not found.")
    if not name:
        return await m.answer("⚠️ The name must be 1–40 characters. Try again:")
    if name_taken(c["id"], name):
        return await m.answer(
            "⚠️ This name already exists here. Enter a different name:"
        )
    add_cat(c["id"], name)
    run("UPDATE categories SET is_sellable=0 WHERE id=?", (c["id"],))
    await state.clear()
    text, keyboard = r_node(c["id"])
    await m.answer(
        "✅ Added. (Open the new item and press <b>Make sellable</b> "
        "if it is a final product.)\n\n" + text,
        reply_markup=keyboard,
    )


@admin_r.callback_query(F.data.startswith("pm:ms:"))
async def pm_make_sellable(cb: CallbackQuery):
    cid = int(cb.data.split(":")[2])
    if cat(cid) and not has_children(cid):
        run("UPDATE categories SET is_sellable=1 WHERE id=?", (cid,))
    await show(cb, r_node(cid))
    await cb.answer("✅ Now sellable. Set its price next.")


@admin_r.callback_query(F.data.startswith("pm:pr:"))
async def pm_price_node(cb: CallbackQuery, state: FSMContext):
    cid = int(cb.data.split(":")[2])
    await start_price(cb, state, cid, f"n:{cid}")


@admin_r.callback_query(F.data.startswith("pm:ps:"))
async def pm_price_pick(cb: CallbackQuery, state: FSMContext):
    _, _, cid, page = cb.data.split(":")
    await start_price(cb, state, int(cid), f"p:{page}")


async def start_price(cb, state, cid, back):
    c = cat(cid)
    if not c or not c["is_sellable"]:
        return await cb.answer("Not a sellable product", show_alert=True)
    await state.clear()
    await state.set_state(EditP.price)
    await state.update_data(node=cid, back=back)
    text = (
        f"💵 <b>{esc(path_of(cid))}</b>\n"
        f"Current price: {CUR}{c['price']:g}\n\n"
        f"Enter the new price in {CUR} (example: 1.5):"
    )
    await show(cb, (text, cancel_kb(back)))
    await cb.answer()


@admin_r.message(EditP.price, F.text)
async def do_price(m: Message, state: FSMContext):
    d = await state.get_data()
    price = parse_price(m.text)
    if price is None:
        return await m.answer("⚠️ Enter a valid number, for example 1.5")
    run("UPDATE categories SET price=? WHERE id=?", (price, d["node"]))
    await state.clear()
    text, keyboard = render_view(d["back"])
    await m.answer(
        f"✅ Price saved: {CUR}{price:g}\n\n" + text,
        reply_markup=keyboard,
    )


@admin_r.callback_query(F.data.startswith("pm:hv:"))
async def pm_toggle_hide(cb: CallbackQuery):
    _, _, cid, mode = cb.data.split(":")
    cid = int(cid)
    c = cat(cid)
    if not c:
        return await cb.answer("Not found", show_alert=True)
    run(
        "UPDATE categories SET hidden=? WHERE id=?",
        (0 if c["hidden"] else 1, cid),
    )
    key = f"n:{cid}" if mode == "e" else f"h:{c['parent_id'] or 0}"
    await show(cb, render_view(key))
    await cb.answer(
        "🙈 Hidden from customers" if not c["hidden"]
        else "👁 Visible to customers"
    )


@admin_r.callback_query(F.data.startswith("pm:x:"))
async def pm_remove_ask(cb: CallbackQuery):
    _, _, cid, mode = cb.data.split(":")
    cid = int(cid)
    c = cat(cid)
    if not c:
        return await cb.answer("Not found", show_alert=True)
    n = count_descendants(cid)
    extra = (
        f"\nThis will also delete <b>{n}</b> item(s) inside it."
        if n else ""
    )
    st = stock_in_subtree(cid)
    if st:
        extra += (
            f"\n⚠️ It also holds <b>{st}</b> stock item(s) that will be deleted."
        )
    cancel = f"n:{cid}" if mode == "e" else f"d:{c['parent_id'] or 0}"
    await show(
        cb,
        (
            f"⚠️ Delete <b>{esc(path_of(cid))}</b>?{extra}\n"
            "This cannot be undone.",
            ikb(
                [[("✅ Yes, delete", f"pm:xx:{cid}:{mode}")],
                 [("❌ Cancel", f"pm:v:{cancel}")]]
            ),
        ),
    )
    await cb.answer()


@admin_r.callback_query(F.data.startswith("pm:xx:"))
async def pm_remove_do(cb: CallbackQuery):
    _, _, cid, mode = cb.data.split(":")
    cid = int(cid)
    c = cat(cid)
    if not c:
        return await cb.answer("Already deleted", show_alert=True)
    parent = c["parent_id"] or 0
    run("DELETE FROM categories WHERE id=?", (cid,))
    await show(
        cb,
        render_view(f"{'n' if mode == 'e' else 'd'}:{parent}"),
    )
    await cb.answer("🗑 Deleted")


# ============================== STOCK MANAGEMENT ==============================
MAX_ITEMS = 20000
MAX_FILE_BYTES = 5 * 1024 * 1024


class StockS(StatesGroup):
    file = State()
    manual = State()
    delnum = State()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def stock_count(pid, status="available"):
    return q1(
        "SELECT COUNT(*) AS n FROM stock WHERE product_id=? AND status=?",
        (pid, status),
    )["n"]


def stock_in_subtree(cid):
    r = q1(
        "WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT c.id FROM categories c JOIN t ON c.parent_id=t.id) "
        "SELECT COUNT(*) AS n FROM stock WHERE product_id IN (SELECT id FROM t)",
        (cid,),
    )
    return r["n"]


def log_stock(path, action, qty, admin_id):
    run(
        "INSERT INTO stock_history(product_path, action, qty, admin_id, created_at) "
        "VALUES(?,?,?,?,?)",
        (path, action, qty, admin_id, now_str()),
    )


class StockFileError(Exception):
    pass


def add_stock_items(pid, items, admin_id):
    """items is a list of (data, fields_json_or_None); duplicate rows are skipped."""
    existing = {
        r["data"] for r in q("SELECT data FROM stock WHERE product_id=?", (pid,))
    }
    new, duplicates = [], 0
    for data, fields in items:
        if data in existing:
            duplicates += 1
            continue
        existing.add(data)
        new.append((data, fields))
    if new:
        timestamp = now_str()
        db.executemany(
            "INSERT INTO stock(product_id, data, fields, added_at, added_by) "
            "VALUES(?,?,?,?,?)",
            [(pid, data, fields, timestamp, admin_id) for data, fields in new],
        )
        db.commit()
        log_stock(path_of(pid), "add", len(new), admin_id)
    return len(new), duplicates


def cell_to_str(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_items(raw_rows):
    """First non-empty row is headers; each following non-empty row is one item."""
    raw_rows = [list(row) for row in raw_rows]
    i = 0
    while i < len(raw_rows) and not any(str(c).strip() for c in raw_rows[i]):
        i += 1
    if i == len(raw_rows):
        raise StockFileError("The file is empty.")
    header = [str(c).strip() for c in raw_rows[i]]
    while header and not header[-1]:
        header.pop()
    if not header:
        raise StockFileError("The first row (column headers) is empty.")
    header = [h or f"Column {n}" for n, h in enumerate(header, 1)]
    items = []
    for row_number, row in enumerate(raw_rows[i + 1:], start=i + 2):
        row = [str(c) for c in row]
        if not any(c.strip() for c in row):
            continue
        if any(c.strip() for c in row[len(header):]):
            raise StockFileError(
                f"Row {row_number} has more columns than the header row. Nothing was added."
            )
        cells = row[:len(header)] + [""] * max(0, len(header) - len(row))
        fields_json = json.dumps(
            [[h, value] for h, value in zip(header, cells)],
            ensure_ascii=False,
        )
        items.append((fields_json, fields_json))
    if not items:
        raise StockFileError("No stock rows found below the header row.")
    return items


def parse_xlsx(raw):
    from openpyxl import load_workbook

    workbook = load_workbook(
        io.BytesIO(raw),
        read_only=True,
        data_only=True,
    )
    try:
        rows = [
            [cell_to_str(value) for value in row]
            for row in workbook.active.iter_rows(values_only=True)
        ]
    finally:
        workbook.close()
    return build_items(rows)


def parse_csv(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = (
        max(",;\t", key=lambda d: first.count(d))
        if any(d in first for d in ",;\t")
        else ","
    )
    return build_items(
        list(csv.reader(io.StringIO(text), delimiter=delimiter))
    )


def item_fields(row):
    return json.loads(row["fields"]) if row["fields"] else None


def item_display(row):
    fields = item_fields(row)
    return " | ".join(value for _, value in fields) if fields else row["data"]


def render_item(row):
    fields = item_fields(row)
    return (
        "\n".join(f"{header}: {value}" for header, value in fields)
        if fields else row["data"]
    )


def r_smenu():
    return "🗂 <b>STOCK MANAGEMENT</b>", ikb(
        [
            [("➕ Add Stock", "sm:p:add:0")],
            [("📋 View Stock", "sm:p:view:0")],
            [("🗑️ Delete Stock", "sm:p:del:0")],
            [("📜 Stock History", "sm:h:0")],
            [("⬅️ Back", "adm:home")],
        ]
    )


PICK_TITLES = {
    "add": "➕ <b>Add Stock</b>",
    "view": "📋 <b>View Stock</b>",
    "del": "🗑️ <b>Delete Stock</b>",
}


def r_pick(action, page):
    back = [("🗂 Stock Management", "sm:home")]
    items = q("SELECT id FROM categories WHERE is_sellable=1 ORDER BY id")
    if not items:
        return (
            f"{PICK_TITLES[action]}\n\nNo sellable products yet. Add a product first.",
            ikb([back]),
        )
    pages = (len(items) + PER_PAGE - 1) // PER_PAGE
    page = max(0, min(page, pages - 1))
    rows = [
        [(
            f"{path_of(item['id'])} • {stock_count(item['id'])}",
            f"sm:s:{action}:{item['id']}",
        )]
        for item in items[page * PER_PAGE:(page + 1) * PER_PAGE]
    ]
    nav = []
    if page > 0:
        nav.append(("◀️", f"sm:p:{action}:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"sm:p:{action}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(back)
    text = (
        f"{PICK_TITLES[action]}  (page {page + 1}/{pages})\n\n"
        "Select a product (the number is the available stock):"
    )
    return text, ikb(rows)


def r_type(pid):
    text = (
        "📦 <b>SELECT STOCK TYPE</b>\n\n"
        f"Product: <b>{esc(path_of(pid))}</b>\n"
        f"Available now: <b>{stock_count(pid)}</b>"
    )
    return text, ikb(
        [
            [("📊 Excel File (.xlsx)", f"sm:t:xlsx:{pid}")],
            [("📄 CSV File (.csv)", f"sm:t:csv:{pid}")],
            [("✍️ Manual Entry", f"sm:t:man:{pid}")],
            [("⬅️ Back", "sm:p:add:0")],
        ]
    )


def r_view(pid):
    available = stock_count(pid)
    sold = stock_count(pid, "sold")
    rows = q(
        "SELECT data, fields FROM stock WHERE product_id=? "
        "AND status='available' ORDER BY id LIMIT 10",
        (pid,),
    )
    preview = "\n".join(
        f"{i}. <code>{esc(item_display(row)[:60])}</code>"
        for i, row in enumerate(rows, 1)
    )
    text = (
        f"📋 <b>{esc(path_of(pid))}</b>\n\n"
        f"✅ Available: <b>{available}</b>\n"
        f"🛒 Sold: <b>{sold}</b>\n"
        f"📦 Total: <b>{available + sold}</b>"
    )
    if preview:
        text += f"\n\n<b>Next items to be sold:</b>\n{preview}"
    keyboard = []
    if available:
        keyboard.append([("📤 Export available", f"sm:ex:{pid}")])
    keyboard.append([("⬅️ Back", "sm:p:view:0")])
    return text, ikb(keyboard)


def r_del(pid):
    available = stock_count(pid)
    text = (
        f"🗑️ <b>{esc(path_of(pid))}</b>\n\n"
        f"Available stock: <b>{available}</b>\n"
        "(Sold items are never deleted here.)"
    )
    keyboard = []
    if available:
        keyboard.append(
            [(f"🗑 Delete ALL available ({available})", f"sm:da:{pid}")]
        )
        keyboard.append([("🔢 Delete newest N items", f"sm:dn:{pid}")])
    keyboard.append([("⬅️ Back", "sm:p:del:0")])
    return text, ikb(keyboard)


HIST_PER_PAGE = 10


def r_hist(page):
    total = q1("SELECT COUNT(*) AS n FROM stock_history")["n"]
    back = [("🗂 Stock Management", "sm:home")]
    if not total:
        return "📜 <b>Stock History</b>\n\nNo stock activity yet.", ikb([back])
    pages = (total + HIST_PER_PAGE - 1) // HIST_PER_PAGE
    page = max(0, min(page, pages - 1))
    rows = q(
        "SELECT * FROM stock_history ORDER BY id DESC LIMIT ? OFFSET ?",
        (HIST_PER_PAGE, page * HIST_PER_PAGE),
    )
    lines = []
    for row in rows:
        icon, word = (
            ("➕", "Added") if row["action"] == "add"
            else ("🗑", "Deleted")
        )
        lines.append(
            f"{row['created_at']}\n{icon} <b>{row['qty']}</b> — "
            f"{esc(row['product_path'])} (admin {row['admin_id']})"
        )
    nav = []
    if page > 0:
        nav.append(("◀️", f"sm:h:{page - 1}"))
    if page < pages - 1:
        nav.append(("▶️", f"sm:h:{page + 1}"))
    keyboard = ([nav] if nav else []) + [back]
    return (
        f"📜 <b>Stock History</b>  (page {page + 1}/{pages})\n\n"
        + "\n\n".join(lines),
        ikb(keyboard),
    )


@admin_r.callback_query(F.data == "sm:home")
async def sm_home(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, r_smenu())
    await cb.answer()


@admin_r.callback_query(F.data.startswith("sm:p:"))
async def sm_pick(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, action, page = cb.data.split(":")
    await show(cb, r_pick(action, int(page)))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("sm:h:"))
async def sm_history(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(cb, r_hist(int(cb.data.split(":")[2])))
    await cb.answer()


@admin_r.callback_query(F.data.startswith("sm:s:"))
async def sm_select(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    _, _, action, pid = cb.data.split(":")
    pid = int(pid)
    product = cat(pid)
    if not product or not product["is_sellable"]:
        return await cb.answer("Product not found", show_alert=True)
    await show(
        cb,
        {"add": r_type, "view": r_view, "del": r_del}[action](pid),
    )
    await cb.answer()


# ---- Add stock ----
@admin_r.callback_query(F.data.startswith("sm:t:"))
async def sm_type(cb: CallbackQuery, state: FSMContext):
    _, _, kind, pid = cb.data.split(":")
    pid = int(pid)
    product = cat(pid)
    if not product or not product["is_sellable"]:
        return await cb.answer("Product not found", show_alert=True)
    await state.clear()
    product_name = esc(path_of(pid))
    if kind == "man":
        await state.set_state(StockS.manual)
        text = (
            f"✍️ <b>Manual Entry</b>\nProduct: <b>{product_name}</b>\n\n"
            "Send the stock items. <b>One item per line.</b> "
            "You can send many lines in one message."
        )
    elif kind in ("xlsx", "csv"):
        await state.set_state(StockS.file)
        label = "Excel (.xlsx)" if kind == "xlsx" else "CSV (.csv)"
        text = (
            f"📎 <b>{label}</b>\nProduct: <b>{product_name}</b>\n\n"
            f"Send the <b>.{kind}</b> file now.\n\n"
            "• <b>Row 1 = column headers</b> (not a stock item)\n"
            "• Every next row = one stock item\n"
            "• Column order and values are kept as text values\n"
            f"• Max {MAX_ITEMS} rows, 5 MB"
        )
    else:
        return await cb.answer("Unknown stock type", show_alert=True)
    await state.update_data(pid=pid, kind=kind)
    await show(
        cb,
        (text, ikb([[("❌ Cancel", f"sm:s:add:{pid}")]])),
    )
    await cb.answer()


async def finish_add(message, state, pid, items):
    if not cat(pid):
        await state.clear()
        return await message.answer("Product not found.")
    if not items:
        return await message.answer(
            "⚠️ No items found. Please check and send again:"
        )
    if len(items) > MAX_ITEMS:
        return await message.answer(
            f"⚠️ Too many items (max {MAX_ITEMS}). Split it and try again:"
        )
    added, duplicates = add_stock_items(pid, items, message.from_user.id)
    await state.clear()
    columns_text = ""
    if items[0][1]:
        columns = ", ".join(
            header for header, _ in json.loads(items[0][1])
        )
        columns_text = "Columns: <b>" + esc(columns) + "</b>\n"
    text = (
        "✅ <b>Stock updated</b>\n\n"
        f"Product: <b>{esc(path_of(pid))}</b>\n"
        f"{columns_text}"
        f"Added: <b>{added}</b>\n"
        f"Duplicates skipped: <b>{duplicates}</b>\n"
        f"Available now: <b>{stock_count(pid)}</b>"
    )
    await message.answer(
        text,
        reply_markup=ikb(
            [[("➕ Add more to this product", f"sm:s:add:{pid}")],
             [("🗂 Stock Management", "sm:home")]]
        ),
    )


@admin_r.message(StockS.file, F.document)
async def sm_file(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    kind = data["kind"]
    document = message.document
    if not (document.file_name or "").lower().endswith("." + kind):
        return await message.answer(f"⚠️ Please send a <b>.{kind}</b> file.")
    if document.file_size and document.file_size > MAX_FILE_BYTES:
        return await message.answer("⚠️ File is too large (max 5 MB).")
    try:
        buffer = await bot.download(document, destination=io.BytesIO())
        raw = buffer.getvalue()
        items = parse_xlsx(raw) if kind == "xlsx" else parse_csv(raw)
    except StockFileError as error:
        return await message.answer(f"⚠️ {esc(error)}")
    except ImportError:
        return await message.answer(
            "⚠️ Excel support needs the openpyxl package.\n"
            "Install it: <code>pip install openpyxl</code>"
        )
    except Exception:
        logging.exception("stock file read failed")
        return await message.answer(
            "⚠️ Could not read this file. Check the format and send again:"
        )
    await finish_add(message, state, data["pid"], items)


@admin_r.message(StockS.file)
async def sm_file_hint(message: Message):
    await message.answer(
        "📎 Please send the file as a document, or press Cancel above."
    )


@admin_r.message(StockS.manual, F.text)
async def sm_manual(message: Message, state: FSMContext):
    data = await state.get_data()
    lines = [line.strip() for line in message.text.splitlines() if line.strip()]
    # Manual entries are plain one-line stock items, so fields must be None.
    items = [(line, None) for line in lines]
    await finish_add(message, state, data["pid"], items)


# ---- View: export ----
@admin_r.callback_query(F.data.startswith("sm:ex:"))
async def sm_export(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    rows = q(
        "SELECT data, fields FROM stock WHERE product_id=? "
        "AND status='available' ORDER BY id",
        (pid,),
    )
    if not rows:
        return await cb.answer("No available stock", show_alert=True)
    structured = [
        item_fields(row) for row in rows if row["fields"]
    ]
    if (
        len(structured) == len(rows)
        and len({tuple(header for header, _ in fields) for fields in structured}) == 1
    ):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([header for header, _ in structured[0]])
        for fields in structured:
            writer.writerow([value for _, value in fields])
        file_data = buffer.getvalue().encode("utf-8-sig")
        filename = f"stock_{pid}.csv"
    else:
        separator = "\n\n" if structured else "\n"
        file_data = separator.join(
            render_item(row) for row in rows
        ).encode("utf-8")
        filename = f"stock_{pid}.txt"
    await cb.message.answer_document(
        BufferedInputFile(file_data, filename=filename)
    )
    await cb.answer()


# ---- Delete stock ----
@admin_r.callback_query(F.data.startswith("sm:da:"))
async def sm_del_all_ask(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    count = stock_count(pid)
    await show(
        cb,
        (
            f"⚠️ Delete all <b>{count}</b> available item(s) of "
            f"<b>{esc(path_of(pid))}</b>?\n"
            "Sold items are kept. This cannot be undone.",
            ikb(
                [[("✅ Yes, delete", f"sm:dd:{pid}")],
                 [("❌ Cancel", f"sm:s:del:{pid}")]]
            ),
        ),
    )
    await cb.answer()


@admin_r.callback_query(F.data.startswith("sm:dd:"))
async def sm_del_all(cb: CallbackQuery):
    pid = int(cb.data.split(":")[2])
    count = stock_count(pid)
    if count:
        run(
            "DELETE FROM stock WHERE product_id=? AND status='available'",
            (pid,),
        )
        log_stock(path_of(pid), "delete", count, cb.from_user.id)
    await show(cb, r_del(pid))
    await cb.answer(f"🗑 Deleted {count} item(s)")


@admin_r.callback_query(F.data.startswith("sm:dn:"))
async def sm_del_n_ask(cb: CallbackQuery, state: FSMContext):
    pid = int(cb.data.split(":")[2])
    available = stock_count(pid)
    if not available:
        return await cb.answer("No available stock", show_alert=True)
    await state.clear()
    await state.set_state(StockS.delnum)
    await state.update_data(pid=pid)
    text = (
        f"🔢 How many of the <b>newest</b> items do you want to delete?\n"
        f"Enter a number (1–{available}):"
    )
    await show(
        cb,
        (text, ikb([[("❌ Cancel", f"sm:s:del:{pid}")]])),
    )
    await cb.answer()


@admin_r.message(StockS.delnum, F.text)
async def sm_del_n(message: Message, state: FSMContext):
    data = await state.get_data()
    pid = data["pid"]
    available = stock_count(pid)
    try:
        number = int(message.text.strip())
    except ValueError:
        number = 0
    if number < 1 or number > available:
        return await message.answer(
            f"⚠️ Enter a number between 1 and {available}."
        )
    run(
        "DELETE FROM stock WHERE id IN ("
        "SELECT id FROM stock WHERE product_id=? AND status='available' "
        "ORDER BY id DESC LIMIT ?)",
        (pid, number),
    )
    log_stock(path_of(pid), "delete", number, message.from_user.id)
    await state.clear()
    text, keyboard = r_del(pid)
    await message.answer(
        f"✅ Deleted {number} item(s).\n\n" + text,
        reply_markup=keyboard,
    )


# ============================== RUN ==============================
async def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is missing. Copy .env.example to .env and fill it in."
        )
    if not OWNER_ID:
        print(
            "WARNING: OWNER_ID is 0. Send /id to the bot, put your ID in "
            ".env as OWNER_ID and restart."
        )
    try:
        from aiogram.client.default import DefaultBotProperties
        bot = Bot(
            BOT_TOKEN,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
    except ImportError:
        bot = Bot(BOT_TOKEN, parse_mode="HTML")
    dispatcher = Dispatcher()
    dispatcher.include_router(main_r)
    dispatcher.include_router(admin_r)
    await bot.delete_webhook(drop_pending_updates=True)
    print("Bot started.")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
