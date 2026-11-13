from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def customer_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Profile"), KeyboardButton(text="🛍 Buy product")],
        [KeyboardButton(text="🤝 Referral"), KeyboardButton(text="💳 Deposit")],
        [KeyboardButton(text="📦 My Order"), KeyboardButton(text="🆘 Support")],
        [KeyboardButton(text="🏪 Apply as a reseller"), KeyboardButton(text="🌐 Language")]
    ], resize_keyboard=True)

def admin_menu():
    rows = [
        [("📦 Product Management","admin:products"),("📊 Stock Management","admin:stock")],
        [("💰 Order Management","admin:orders"),("💳 Payment Management","admin:payments")],
        [("👥 User Management","admin:users"),("🤝 Referral Settings","admin:referral")],
        [("🏪 Reseller Management","admin:reseller"),("🛡 Admin Management","admin:admins")],
        [("⚙️ Bot Settings","admin:settings"),("📈 Statistics","admin:stats")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t,d in row] for row in rows
    ])

def inline_rows(items, prefix, include_back=True):
    rows = []
    for item in items:
        rows.append([InlineKeyboardButton(text=item[1], callback_data=f"{prefix}:{item[0]}")])
    if include_back:
        rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
