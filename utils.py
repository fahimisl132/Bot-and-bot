from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from app.db import Product

async def product_path(session, product):
    names = [product.name]
    current = product
    while current.parent_id:
        current = await session.get(Product, current.parent_id)
        if not current:
            break
        names.append(current.name)
    return " → ".join(reversed(names))

async def descendants(session, product_id):
    rows = (await session.scalars(select(Product))).all()
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)
    out = []
    def walk(pid):
        for child in by_parent.get(pid, []):
            out.append(child)
            walk(child.id)
    walk(product_id)
    return out

def buttons_for_products(products, prefix, add_controls=False):
    rows, row = [], []
    for p in products:
        row.append(InlineKeyboardButton(text=p.name[:30], callback_data=f"{prefix}:{p.id}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    if add_controls:
        rows.append([InlineKeyboardButton(text="➕ Add another", callback_data="product:add_child")])
        rows.append([InlineKeyboardButton(text="✅ Done", callback_data="product:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
