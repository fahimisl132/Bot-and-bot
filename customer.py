from datetime import timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from sqlalchemy import select, func, desc
from app.db import Session, User, Product, Order, Deposit, Setting, StockItem, now
from app.keyboards import customer_menu, inline_rows
from app.utils import product_path

router = Router()

@router.message(CommandStart())
async def start(m: Message):
    args = (m.text or "").split(maxsplit=1)
    ref = None
    if len(args) > 1 and args[1].isdigit() and int(args[1]) != m.from_user.id:
        ref = int(args[1])
    async with Session() as s:
        user = await s.get(User, m.from_user.id)
        if not user:
            user = User(id=m.from_user.id, username=m.from_user.username or "",
                        full_name=m.from_user.full_name or "", referred_by=ref)
            s.add(user)
        else:
            user.username = m.from_user.username or ""
            user.full_name = m.from_user.full_name or ""
        await s.commit()
    await m.answer("Welcome to My-Sell-Bot! Choose an option:", reply_markup=customer_menu())

@router.message(F.text.in_({"👤 Profile","/profile"}))
async def profile(m: Message):
    async with Session() as s:
        u = await s.get(User, m.from_user.id)
        if not u:
            await m.answer("Please send /start first."); return
        refs = await s.scalar(select(func.count()).select_from(User).where(User.referred_by == u.id))
        text = (f"👤 **Your Profile**\n\nTelegram: {u.full_name}\nTelegram ID: `{u.id}`\n"
                f"Username: @{u.username or 'not set'}\nRegistration: {u.joined_at:%Y-%m-%d}\n"
                f"Wallet Balance: {u.balance} USD\nTotal Deposit: {u.total_deposit} USD\n"
                f"Total Purchase: {u.total_purchase} USD\nReferral Income: {u.referral_income} USD\n"
                f"Total Referrals: {refs}\nReseller Status: {'Approved' if u.reseller else 'No'}")
    await m.answer(text, parse_mode="Markdown")

@router.message(F.text == "🛍 Buy product")
async def buy_menu(m: Message):
    async with Session() as s:
        roots = (await s.scalars(select(Product).where(Product.parent_id.is_(None), Product.active == True))).all()
        kb = inline_rows([(p.id,p.name) for p in roots], "buy")
    await m.answer("🛍 Select a product:", reply_markup=kb)

@router.callback_query(F.data.startswith("buy:"))
async def buy_level(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    async with Session() as s:
        p = await s.get(Product, pid)
        if not p or not p.active:
            await c.answer("Product unavailable", show_alert=True); return
        children = (await s.scalars(select(Product).where(Product.parent_id == pid, Product.active == True))).all()
        if children:
            kb = inline_rows([(x.id,x.name) for x in children], "buy")
            await c.message.edit_text(f"📂 {p.name}\nChoose an option:", reply_markup=kb)
        elif p.price is None:
            await c.answer("This product is not priced yet.", show_alert=True); return
        else:
            await c.message.edit_text(f"{p.name}\nPrice: {p.price} USD", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Buy now", callback_data=f"purchase:{p.id}")],
                [InlineKeyboardButton(text="⬅️ Back", callback_data="home")]
            ]))
    await c.answer()

@router.callback_query(F.data.startswith("purchase:"))
async def purchase(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    async with Session() as s:
        async with s.begin():
            p = await s.get(Product, pid)
            u = await s.get(User, c.from_user.id)
            if not p or not p.active or p.price is None:
                await c.answer("Product unavailable", show_alert=True); return
            children = await s.scalar(select(func.count()).select_from(Product).where(Product.parent_id == pid))
            if children:
                await c.answer("Select a final product.", show_alert=True); return
            stock = await s.scalar(select(StockItem).where(StockItem.product_id == pid, StockItem.status == "available").with_for_update())
            if not stock:
                await c.answer("Out of stock.", show_alert=True); return
            price = float(p.price)
            if not u or float(u.balance) < price:
                await c.answer("Insufficient balance. Please deposit.", show_alert=True); return
            u.balance = float(u.balance) - price
            u.total_purchase = float(u.total_purchase) + price
            stock.status = "sold"
            order = Order(user_id=u.id, product_id=p.id, price=price, status="completed", delivery=stock.payload)
            s.add(order)
            await s.flush()
            stock.order_id = order.id
            await c.message.answer(f"✅ Purchase successful!\nOrder: #{order.id}\nProduct: {await product_path(s,p)}\n\nYour item:\n`{stock.payload}`", parse_mode="Markdown")
    await c.answer("Purchased")

@router.message(F.text == "📦 My Order")
async def orders(m: Message):
    async with Session() as s:
        since = now() - timedelta(hours=24)
        recent = (await s.scalars(select(Order).where(Order.user_id == m.from_user.id, Order.created_at >= since).order_by(desc(Order.id)).limit(20))).all()
        old_count = await s.scalar(select(func.count()).select_from(Order).where(Order.user_id == m.from_user.id, Order.created_at < since))
        lines = ["📦 Purchases in the last 24 hours:"]
        for o in recent:
            p = await s.get(Product,o.product_id)
            lines.append(f"#{o.id} | {p.name if p else 'Product'} | {o.price} USD | {o.status}")
        lines.append(f"\nPrevious orders: {old_count}")
    await m.answer("\n".join(lines))

@router.message(F.text == "🤝 Referral")
async def referral(m: Message):
    async with Session() as s:
        u = await s.get(User,m.from_user.id)
        count = await s.scalar(select(func.count()).select_from(User).where(User.referred_by == m.from_user.id))
        pct = await s.get(Setting,"referral_percent")
        botname = (await m.bot.get_me()).username
        await m.answer(f"🤝 Referral\nLink: https://t.me/{botname}?start={m.from_user.id}\nCommission: {pct.value if pct else '0'}%\nTotal referrals: {count}\nReferral income: {u.referral_income} USD")

@router.message(F.text == "💳 Deposit")
async def deposit(m: Message):
    async with Session() as s:
        methods = (await s.scalars(select(Setting).where(Setting.key.like("paymethod:%")))).all()
        active = [x.key.split(":",1)[1] for x in methods if x.value == "on"]
        details = []
        for method in active:
            details.append(f"• {method}: {await _setting(s, 'paydetail:'+method, 'Contact admin')}")
    await m.answer("💳 Available payment methods:\n" + ("\n".join(details) if details else "No payment method enabled.") + "\n\nSend /deposit_amount to create a request. Admin approval is required.")

async def _setting(s,k,d=""):
    row = await s.get(Setting,k); return row.value if row else d

@router.message(F.text.startswith("/deposit_amount"))
async def deposit_amount(m: Message):
    parts=(m.text or "").split()
    if len(parts)<4:
        await m.answer("Use: /deposit_amount METHOD AMOUNT TRANSACTION_ID\nExample: /deposit_amount Binance 10 TX123"); return
    method, amount_s, ref = parts[1], parts[2], parts[3]
    try: amount=float(amount_s)
    except ValueError: await m.answer("Invalid amount."); return
    if amount<=0: await m.answer("Amount must be positive."); return
    async with Session() as s:
        active=await _setting(s,"paymethod:"+method,"off")
        if active!="on": await m.answer("That method is disabled."); return
        d=Deposit(user_id=m.from_user.id,method=method,amount=amount,reference=ref)
        s.add(d)
        try: await s.commit()
        except Exception:
            await s.rollback(); await m.answer("Duplicate transaction reference or invalid request."); return
    await m.answer(f"Deposit request submitted. Request ID: {d.id}. Waiting for admin verification.")

@router.message(F.text == "🆘 Support")
async def support(m: Message):
    async with Session() as s:
        rows=(await s.scalars(select(Setting).where(Setting.key.in_(["support_username","support_group","support_channel"])))).all()
        vals={r.key:r.value for r in rows}
    await m.answer("🆘 Support\n" + "\n".join(f"{k}: {v or 'Not configured'}" for k,v in vals.items()))

@router.message(F.text == "🏪 Apply as a reseller")
async def reseller(m: Message):
    async with Session() as s:
        u=await s.get(User,m.from_user.id)
        if u and u.reseller: await m.answer("You are already a reseller."); return
        minimum=float(await _setting(s,"reseller_min_deposit","0"))
        if not u or float(u.total_deposit)<minimum:
            await m.answer(f"Minimum lifetime deposit required: {minimum} USD"); return
        await m.answer("Your request has been received. Please contact support for membership checks and admin approval.")
