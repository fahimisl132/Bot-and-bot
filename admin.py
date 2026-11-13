from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, desc
from app.config import OWNER_ID
from app.db import Session, User, Product, StockItem, Order, Deposit, Setting, Admin, AuditLog
from app.keyboards import admin_menu
from app.states import ProductFlow, StockFlow, DepositFlow, AdminFlow
from app.utils import buttons_for_products, product_path, descendants

router=Router()
PERMS={"products":"Product Manage","stock":"Stock Manage","users":"User Manage","payments":"Payment Settings","referral":"Referral Settings","stats":"Statistics","admins":"Admin Management","settings":"Bot Settings"}

async def is_admin(uid):
    if uid==OWNER_ID: return True
    async with Session() as s:
        a=await s.get(Admin,uid)
        return bool(a and a.active)

async def permitted(uid, perm):
    if uid==OWNER_ID: return True
    async with Session() as s:
        a=await s.get(Admin,uid)
        return bool(a and a.active and perm in (a.permissions or "").split(","))

async def guard(c, perm):
    if not await permitted(c.from_user.id,perm):
        await c.answer("You do not have permission.",show_alert=True); return False
    return True

@router.message(Command("admin"))
async def admin_cmd(m:Message):
    if await is_admin(m.from_user.id):
        await m.answer("🛡 ADMIN CONTROL PANEL",reply_markup=admin_menu())
    else: await m.answer("Access denied.")

@router.callback_query(F.data.startswith("admin:"))
async def admin_sections(c:CallbackQuery,state:FSMContext):
    section=c.data.split(":")[1]
    if not await guard(c,section): return
    if section=="products":
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Product",callback_data="prod:add")],
            [InlineKeyboardButton(text="✏️ Edit Product",callback_data="prod:edit")],
            [InlineKeyboardButton(text="💵 Set Price",callback_data="prod:price")],
            [InlineKeyboardButton(text="🗑 Remove Product",callback_data="prod:remove")],
            [InlineKeyboardButton(text="👁 Hide / Show",callback_data="prod:toggle")],
            [InlineKeyboardButton(text="⬅ Back",callback_data="home")]])
        await c.message.edit_text("📦 PRODUCT MANAGEMENT",reply_markup=kb)
    elif section=="stock":
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Stock",callback_data="stock:add")],
            [InlineKeyboardButton(text="📋 View Stock",callback_data="stock:view")],
            [InlineKeyboardButton(text="🗑 Delete Stock",callback_data="stock:delete")],
            [InlineKeyboardButton(text="📜 Stock History",callback_data="stock:history")]])
        await c.message.edit_text("📊 STOCK MANAGEMENT",reply_markup=kb)
    elif section=="payments":
        await c.message.edit_text("💳 PAYMENT MANAGEMENT\nConfigure methods using /paymethod METHOD on|off and /paydetail METHOD DETAILS.\nReview deposits: /pendingdeposits")
    elif section=="users":
        await c.message.edit_text("👥 USER MANAGEMENT\nCommands: /user ID, /block ID, /unblock ID, /adjust ID AMOUNT, /broadcast MESSAGE")
    elif section=="referral":
        await c.message.edit_text("Referral commission: /setreferral PERCENT\nCommission limit (successful deposits): /setreferrallimit COUNT")
    elif section=="reseller":
        await c.message.edit_text("Reseller minimum deposit: /setresellermin AMOUNT\nApproval: /approve_reseller USER_ID")
    elif section=="admins":
        await c.message.edit_text("Admin commands: /addadmin ID products,stock,users,payments,referral,stats,admins,settings")
    elif section=="settings":
        await c.message.edit_text("Settings commands: /setsupport USERNAME\n/setwelcome TEXT\n/paymethod METHOD on|off\n/paydetail METHOD DETAILS")
    elif section=="stats":
        async with Session() as s:
            users=await s.scalar(select(func.count()).select_from(User))
            orders=await s.scalar(select(func.count()).select_from(Order))
            deposits=await s.scalar(select(func.count()).select_from(Deposit))
        await c.message.edit_text(f"📈 Statistics\nUsers: {users}\nOrders: {orders}\nDeposit requests: {deposits}")
    await c.answer()

@router.callback_query(F.data=="prod:add")
async def product_add(c:CallbackQuery,state:FSMContext):
    if not await guard(c,"products"): return
    await state.set_state(ProductFlow.root_name)
    await c.message.answer("Enter the main product name (e.g. TextNow):")
    await c.answer()

@router.message(ProductFlow.root_name)
async def product_root(m:Message,state:FSMContext):
    name=(m.text or "").strip()
    if not name or len(name)>120: await m.answer("Name must be 1-120 characters."); return
    async with Session() as s:
        p=Product(name=name,parent_id=None,is_leaf=True); s.add(p); await s.commit()
        await state.update_data(current_id=p.id,root_id=p.id,stack=[])
    await state.set_state(ProductFlow.add_child)
    await m.answer(f"Created {name}. Select it to add subcategories, or Done to finish.",reply_markup=buttons_for_products([p],"tree",True))

@router.callback_query(F.data.startswith("tree:"))
async def tree_select(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(":")[1])
    data=await state.get_data()
    async with Session() as s:
        p=await s.get(Product,pid)
        if not p: await c.answer("Not found",show_alert=True); return
        stack = data.get("stack", [])
        if pid != data.get("current_id"):
            stack = stack + [data.get("current_id")]
        await state.update_data(current_id=pid,stack=stack)
    await state.set_state(ProductFlow.add_child)
    await c.message.answer(f"Enter the name of the subcategory of {p.name}:")
    await c.answer()

@router.callback_query(F.data=="product:add_child")
async def add_child_prompt(c:CallbackQuery,state:FSMContext):
    await c.message.answer("Enter the name of the another subcategory:")
    await c.answer()

@router.message(ProductFlow.add_child)
async def add_child_name(m:Message,state:FSMContext):
    data=await state.get_data(); parent_id=data.get("current_id")
    name=(m.text or "").strip()
    if not name or len(name)>120: await m.answer("Name must be 1-120 characters."); return
    async with Session() as s:
        p=Product(name=name,parent_id=parent_id,is_leaf=True)
        parent=await s.get(Product,parent_id)
        if parent: parent.is_leaf=False
        s.add(p); await s.commit()
        children=(await s.scalars(select(Product).where(Product.parent_id==parent_id))).all()
    await m.answer("Select an option and proceed:",reply_markup=buttons_for_products(children,"tree",True))

@router.callback_query(F.data=="product:done")
async def product_done(c:CallbackQuery,state:FSMContext):
    data=await state.get_data()
    stack=data.get("stack",[])
    if stack:
        parent_id=stack.pop()
        async with Session() as s:
            parent=await s.get(Product,parent_id)
            kids=(await s.scalars(select(Product).where(Product.parent_id==parent_id))).all()
            for k in kids:
                grand=await s.scalar(select(func.count()).select_from(Product).where(Product.parent_id==k.id))
                k.is_leaf=(grand==0)
            await s.commit()
        await state.update_data(current_id=parent_id,stack=stack)
        async with Session() as s:
            kids=(await s.scalars(select(Product).where(Product.parent_id==parent_id))).all()
        await c.message.answer("Continue adding subcategories:",reply_markup=buttons_for_products(kids,"tree",True))
    else:
        await state.clear()
        await c.message.answer("✅ All products successfully added. Use Edit Product to add more levels.")
    await c.answer()

@router.callback_query(F.data=="prod:price")
async def price_menu(c:CallbackQuery,state:FSMContext):
    if not await guard(c,"products"): return
    async with Session() as s:
        rows=(await s.scalars(select(Product).where(Product.is_leaf==True))).all()
        kb=buttons_for_products(rows,"setprice",False)
    await c.message.edit_text("💵 Select a sellable product to set price:",reply_markup=kb)
    await c.answer()

@router.callback_query(F.data.startswith("setprice:"))
async def set_price(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(":")[1])
    await state.update_data(price_product=pid)
    await state.set_state(ProductFlow.price)
    await c.message.answer("Enter price in USD:")
    await c.answer()

@router.message(ProductFlow.price)
async def save_price(m:Message,state:FSMContext):
    try: price=float(m.text)
    except: await m.answer("Enter a valid number."); return
    if price<=0: await m.answer("Price must be greater than zero."); return
    data=await state.get_data()
    async with Session() as s:
        p=await s.get(Product,data["price_product"])
        if not p or not p.is_leaf: await m.answer("This is not a sellable product."); return
        p.price=price; await s.commit()
    await state.clear(); await m.answer(f"Price saved: {price:.2f} USD")

@router.callback_query(F.data.startswith("prod:"))
async def product_actions(c:CallbackQuery,state:FSMContext):
    action=c.data.split(":")[1]
    if action=="edit":
        await c.message.answer("Use /products to list product IDs, then /rename PRODUCT_ID NEW_NAME.")
    elif action in ("remove","toggle"):
        await c.message.answer(f"Use /{action} PRODUCT_ID.")
    await c.answer()

@router.callback_query(F.data.startswith("stock:"))
async def stock_actions(c:CallbackQuery,state:FSMContext):
    if not await guard(c,"stock"): return
    action=c.data.split(":")[1]
    if action=="add":
        async with Session() as s:
            rows=(await s.scalars(select(Product).where(Product.is_leaf==True))).all()
        await c.message.edit_text("Choose sellable product:",reply_markup=buttons_for_products(rows,"stockproduct",False))
    elif action in ("view","delete","history"):
        await c.message.answer(f"Stock {action}: use /stocklist")
    await c.answer()

@router.callback_query(F.data.startswith("stockproduct:"))
async def stock_product(c:CallbackQuery,state:FSMContext):
    pid=int(c.data.split(":")[1]); await state.update_data(stock_product=pid)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Excel (.xlsx)",callback_data="stocktype:excel")],
        [InlineKeyboardButton(text="📄 CSV (.csv)",callback_data="stocktype:csv")],
        [InlineKeyboardButton(text="✍️ Manual Entry",callback_data="stocktype:manual")]])
    await c.message.edit_text("SELECT STOCK TYPE",reply_markup=kb); await c.answer()

@router.callback_query(F.data.startswith("stocktype:"))
async def stock_type(c:CallbackQuery,state:FSMContext):
    typ=c.data.split(":")[1]
    if typ=="manual":
        await state.set_state(StockFlow.manual); await c.message.answer("Send one stock item per message/line. Send /done when finished.")
    else:
        await state.update_data(stock_file_type=typ); await state.set_state(StockFlow.file)
        await c.message.answer("Upload the .xlsx or .csv file. First column will be imported as stock.")
    await c.answer()

@router.message(StockFlow.manual)
async def stock_manual(m:Message,state:FSMContext):
    if m.text=="/done": await state.clear(); await m.answer("Stock entry finished."); return
    data=await state.get_data(); lines=[x.strip() for x in (m.text or "").splitlines() if x.strip()]
    if not lines: await m.answer("Send stock text."); return
    async with Session() as s:
        for line in lines: s.add(StockItem(product_id=data["stock_product"],payload=line))
        await s.commit()
    await m.answer(f"Added {len(lines)} stock item(s). Send more or /done.")

@router.message(StockFlow.file)
async def stock_file(m:Message,state:FSMContext):
    import csv, io
    from openpyxl import load_workbook
    data=await state.get_data()
    if not m.document: await m.answer("Please upload a file."); return
    raw=await m.bot.download(m.document)
    content=raw.read()
    vals=[]
    try:
        if data["stock_file_type"]=="csv":
            vals=[r[0].strip() for r in csv.reader(io.StringIO(content.decode("utf-8-sig"))) if r and r[0].strip()]
        else:
            wb=load_workbook(io.BytesIO(content),read_only=True,data_only=True)
            vals=[str(row[0]).strip() for row in wb.active.iter_rows(values_only=True) if row and row[0] is not None and str(row[0]).strip()]
    except Exception:
        await m.answer("Could not read file. Check format and try again."); return
    async with Session() as s:
        for v in vals: s.add(StockItem(product_id=data["stock_product"],payload=v))
        await s.commit()
    await state.clear(); await m.answer(f"Imported {len(vals)} stock item(s).")

@router.message(Command("setreferral"))
async def setref(m:Message):
    if not await permitted(m.from_user.id,"referral"): return
    try: pct=float(m.text.split()[1])
    except: await m.answer("Use /setreferral PERCENT"); return
    if pct<0 or pct>100: await m.answer("Percent must be 0-100"); return
    async with Session() as s:
        from app.db import set_setting
        await set_setting(s,"referral_percent",pct); await s.commit()
    await m.answer("Referral commission saved.")

@router.message(Command("setreferrallimit"))
async def setref_limit(m:Message):
    if not await permitted(m.from_user.id,"referral"): return
    try: n=int(m.text.split()[1])
    except: await m.answer("Use /setreferrallimit COUNT"); return
    async with Session() as s:
        from app.db import set_setting
        await set_setting(s,"referral_limit",n); await s.commit()
    await m.answer("Referral commission limit saved.")

@router.message(Command("paymethod"))
async def paymethod(m:Message):
    if not await permitted(m.from_user.id,"payments"): return
    p=(m.text or "").split(maxsplit=2)
    if len(p)!=3 or p[2] not in ("on","off"): await m.answer("Use /paymethod METHOD on|off"); return
    async with Session() as s:
        from app.db import set_setting
        await set_setting(s,"paymethod:"+p[1],p[2]); await s.commit()
    await m.answer("Payment method updated.")

@router.message(Command("paydetail"))
async def paydetail(m:Message):
    if not await permitted(m.from_user.id,"payments"): return
    p=(m.text or "").split(maxsplit=2)
    if len(p)!=3: await m.answer("Use /paydetail METHOD DETAILS"); return
    async with Session() as s:
        from app.db import set_setting
        await set_setting(s,"paydetail:"+p[1],p[2]); await s.commit()
    await m.answer("Payment details saved.")

@router.message(Command("pendingdeposits"))
async def pending_deposits(m:Message):
    if not await permitted(m.from_user.id,"payments"): return
    async with Session() as s:
        rows=(await s.scalars(select(Deposit).where(Deposit.status=="pending").order_by(Deposit.id).limit(30))).all()
        if not rows: await m.answer("No pending deposits."); return
        text="\n".join(f"#{d.id} user={d.user_id} {d.method} {d.amount} ref={d.reference}" for d in rows)
    await m.answer(text+"\nApprove: /approvedeposit ID | Reject: /rejectdeposit ID")

@router.message(Command("approvedeposit"))
async def approve_deposit(m:Message):
    if not await permitted(m.from_user.id,"payments"): return
    try: did=int(m.text.split()[1])
    except: await m.answer("Use /approvedeposit ID"); return
    async with Session() as s:
        async with s.begin():
            d=await s.get(Deposit,did,with_for_update=True)
            if not d or d.status!="pending": await m.answer("Deposit not pending."); return
            u=await s.get(User,d.user_id,with_for_update=True)
            d.status="approved"; u.balance=float(u.balance)+float(d.amount); u.total_deposit=float(u.total_deposit)+float(d.amount)
    await m.answer("Deposit approved and balance credited.")

@router.message(Command("rejectdeposit"))
async def reject_deposit(m:Message):
    if not await permitted(m.from_user.id,"payments"): return
    try: did=int(m.text.split()[1])
    except: await m.answer("Use /rejectdeposit ID"); return
    async with Session() as s:
        d=await s.get(Deposit,did)
        if not d or d.status!="pending": await m.answer("Deposit not pending."); return
        d.status="rejected"; await s.commit()
    await m.answer("Deposit rejected.")

@router.message(Command("setresellermin"))
async def reseller_min(m:Message):
    if not await permitted(m.from_user.id,"reseller"): return
    try: value=float(m.text.split()[1])
    except: await m.answer("Use /setresellermin AMOUNT"); return
    async with Session() as s:
        from app.db import set_setting
        await set_setting(s,"reseller_min_deposit",value); await s.commit()
    await m.answer("Reseller minimum deposit saved.")

@router.message(Command("approve_reseller"))
async def approve_reseller(m:Message):
    if not await permitted(m.from_user.id,"reseller"): return
    try: uid=int(m.text.split()[1])
    except: await m.answer("Use /approve_reseller USER_ID"); return
    async with Session() as s:
        u=await s.get(User,uid)
        if not u: await m.answer("User not found."); return
        u.reseller=True; await s.commit()
    await m.answer("Reseller approved.")

@router.message(Command("addadmin"))
async def add_admin(m:Message):
    if m.from_user.id!=OWNER_ID: await m.answer("Owner only."); return
    p=(m.text or "").split(maxsplit=2)
    if len(p)!=3: await m.answer("Use /addadmin ID products,stock,users,payments,referral,stats,admins,settings"); return
    try: uid=int(p[1])
    except: await m.answer("Invalid ID."); return
    async with Session() as s:
        a=await s.get(Admin,uid)
        if a: a.permissions=p[2]
        else: s.add(Admin(user_id=uid,permissions=p[2]))
        await s.commit()
    await m.answer("Admin permissions saved.")

@router.message(Command("block"))
async def block_user(m:Message):
    if not await permitted(m.from_user.id,"users"): return
    try: uid=int(m.text.split()[1])
    except: await m.answer("Use /block USER_ID"); return
    async with Session() as s:
        u=await s.get(User,uid)
        if not u: await m.answer("User not found."); return
        u.blocked=True; await s.commit()
    await m.answer("User blocked.")

@router.message(Command("unblock"))
async def unblock_user(m:Message):
    if not await permitted(m.from_user.id,"users"): return
    try: uid=int(m.text.split()[1])
    except: await m.answer("Use /unblock USER_ID"); return
    async with Session() as s:
        u=await s.get(User,uid)
        if not u: await m.answer("User not found."); return
        u.blocked=False; await s.commit()
    await m.answer("User unblocked.")

@router.message(Command("adjust"))
async def adjust_balance(m:Message):
    if not await permitted(m.from_user.id,"users"): return
    p=(m.text or "").split()
    if len(p)!=3: await m.answer("Use /adjust USER_ID +/-AMOUNT"); return
    try: uid=int(p[1]); delta=float(p[2])
    except: await m.answer("Invalid values."); return
    async with Session() as s:
        u=await s.get(User,uid)
        if not u: await m.answer("User not found."); return
        if float(u.balance)+delta<0: await m.answer("Balance cannot become negative."); return
        u.balance=float(u.balance)+delta; await s.commit()
    await m.answer("Balance adjusted.")

@router.message(Command("user"))
async def user_detail(m:Message):
    if not await permitted(m.from_user.id,"users"): return
    try: uid=int(m.text.split()[1])
    except: await m.answer("Use /user USER_ID"); return
    async with Session() as s:
        u=await s.get(User,uid)
        if not u: await m.answer("Not found."); return
        await m.answer(f"User {uid}\n@{u.username}\nBalance: {u.balance}\nDeposits: {u.total_deposit}\nPurchases: {u.total_purchase}\nBlocked: {u.blocked}")

@router.message(Command("products"))
async def list_products(m:Message):
    if not await permitted(m.from_user.id,"products"): return
    async with Session() as s:
        rows=(await s.scalars(select(Product).order_by(Product.id))).all()
        text="\n".join(f"{p.id}: {p.name} parent={p.parent_id} leaf={p.is_leaf} price={p.price} active={p.active}" for p in rows) or "No products."
    await m.answer(text)

@router.message(Command("rename"))
async def rename_product(m:Message):
    if not await permitted(m.from_user.id,"products"): return
    p=(m.text or "").split(maxsplit=2)
    if len(p)!=3: await m.answer("Use /rename PRODUCT_ID NEW_NAME"); return
    async with Session() as s:
        row=await s.get(Product,int(p[1]))
        if not row: await m.answer("Not found."); return
        row.name=p[2]; await s.commit()
    await m.answer("Renamed.")

@router.message(Command("remove"))
async def remove_product(m:Message):
    if not await permitted(m.from_user.id,"products"): return
    try: pid=int(m.text.split()[1])
    except: await m.answer("Use /remove PRODUCT_ID"); return
    async with Session() as s:
        row=await s.get(Product,pid)
        if not row: await m.answer("Not found."); return
        kids=await s.scalar(select(func.count()).select_from(Product).where(Product.parent_id==pid))
        if kids: await m.answer("Cannot remove a category with child categories. Remove children first."); return
        await s.delete(row); await s.commit()
    await m.answer("Product removed.")

@router.message(Command("hide"))
async def hide_product(m:Message):
    await _toggle_product(m,False)

@router.message(Command("show"))
async def show_product(m:Message):
    await _toggle_product(m,True)

async def _toggle_product(m,active):
    if not await permitted(m.from_user.id,"products"): return
    try: pid=int(m.text.split()[1])
    except: await m.answer("Use /hide ID or /show ID"); return
    async with Session() as s:
        p=await s.get(Product,pid)
        if not p: await m.answer("Not found."); return
        p.active=active; await s.commit()
    await m.answer("Updated.")

@router.message(Command("broadcast"))
async def broadcast(m:Message):
    if not await permitted(m.from_user.id,"users"): return
    text=(m.text or "").partition(" ")[2].strip()
    if not text: await m.answer("Use /broadcast MESSAGE"); return
    async with Session() as s:
        ids=(await s.scalars(select(User.id).where(User.blocked==False))).all()
    sent=0
    for uid in ids:
        try: await m.bot.send_message(uid,text); sent+=1
        except Exception: pass
    await m.answer(f"Broadcast sent: {sent}/{len(ids)}")
