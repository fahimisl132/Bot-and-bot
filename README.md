# My-Sell-Bot

A new Telegram digital-product sales bot starter built with Python, aiogram 3, SQLAlchemy async, and PostgreSQL (SQLite is supported for local testing).

## Features in this starter
- Customer menu: Profile, Buy Product, Referral, Deposit, My Order, Support, Reseller, Language.
- Admin dashboard with product, stock, order, payment, user, referral, reseller, admin, settings and statistics sections.
- Unlimited nested product categories; leaf categories are sellable.
- Per-leaf pricing.
- Stock upload from manual entry, CSV and XLSX.
- Stock delivery and wallet debit in one database transaction.
- Deposit request records, duplicate reference protection, admin approval/rejection.
- User block/unblock and balance adjustment.
- Referral links and configurable commission percentage/limit setting.
- Reseller minimum deposit and manual approval.
- Admin permissions and owner account.

## Important limitations to configure before real sales
1. Binance Pay / crypto live payment verification is NOT enabled by this starter. It currently records deposit requests for admin review. Add the official provider API/webhook credentials and verify signatures before automatic crediting.
2. bKash and Nagad are manual transaction-reference workflows until you obtain merchant API credentials and complete provider integration.
3. Referral commission accrual, channel/group membership checks, reseller discount tiers, deposit bonuses, expiry enforcement, language translations, admin activity logging, and a full graphical edit wizard are extension points; do not advertise them as automated until implemented and tested.
4. Product and stock admin commands are provided; the nested add-product wizard and set-price interface are interactive.
5. Before launch, add migrations (Alembic), backups, rate limits, payment reconciliation, monitoring, and a staging bot.

## Local setup
Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `BOT_TOKEN` from BotFather and your numeric Telegram `OWNER_ID`.
For local testing, set:
`DATABASE_URL=sqlite+aiosqlite:///./mysellbot.db`

Run:
```bash
python -m app
```

Open Telegram and send `/start`. Send `/admin` from the owner account.

## Railway deployment
1. Create a PostgreSQL database in the Railway project.
2. Add a service from this GitHub repository.
3. Set Railway Variables:
   - `BOT_TOKEN`
   - `OWNER_ID`
   - `DATABASE_URL` = Railway PostgreSQL connection URL. The app normalizes `postgres://` and `postgresql://`.
   - `DEFAULT_CURRENCY=USD`
4. Deploy using the included Dockerfile and `railway.toml`.
5. Ensure only one replica is running because this project uses Telegram long polling.
6. Keep `.env` out of GitHub. Rotate the bot token if it has ever been shared publicly.

## Admin commands
- `/admin` — dashboard
- `/products` — product IDs and tree status
- `/rename PRODUCT_ID NEW_NAME`
- `/remove PRODUCT_ID` (leaf only)
- `/hide PRODUCT_ID`, `/show PRODUCT_ID`
- `/setreferral PERCENT`
- `/setreferrallimit COUNT`
- `/paymethod METHOD on|off`
- `/paydetail METHOD DETAILS`
- `/pendingdeposits`
- `/approvedeposit DEPOSIT_ID`
- `/rejectdeposit DEPOSIT_ID`
- `/setresellermin AMOUNT`
- `/approve_reseller USER_ID`
- `/addadmin USER_ID products,stock,users,payments,referral,stats,admins,settings`
- `/user USER_ID`, `/block USER_ID`, `/unblock USER_ID`
- `/adjust USER_ID +/-AMOUNT`
- `/broadcast MESSAGE`

## Deposit customer command
`/deposit_amount METHOD AMOUNT TRANSACTION_ID`

A unique transaction reference is required. Deposit remains pending until an authorized admin approves it.

## Stock format
- Manual: one stock item per line; `/done` finishes.
- CSV: first column contains one deliverable per row.
- XLSX: first column of the active sheet contains one deliverable per row.

## Security notes
- Do not put provider secrets in source code.
- Owner ID is the only initial super-admin.
- Admin commands must be used only by trusted accounts.
- Never automatically credit payment based only on a user-submitted transaction ID.
- Test purchases, refunds, concurrency and duplicate references with a staging bot before production.
