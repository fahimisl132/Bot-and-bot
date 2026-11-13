# Telegram Shop Bot

A Telegram bot for selling digital products, fully managed from an in-bot admin panel.
Built with [aiogram 3](https://docs.aiogram.dev/) and SQLite.

## Status

| Part | State |
|---|---|
| Customer main menu (Profile, Buy Product) | Done |
| Admin panel | Done |
| Product Management (multi-level product tree, price, hide/show, remove) | Done |
| Stock Management (Excel / CSV / manual, view, delete, history) | Done |
| Deposit & payments, orders, referral, reseller, user management | Planned |

## Setup

```bash
git clone <your-repo-url>
cd <repo-folder>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env      # then edit .env
python shop_bot.py
```

1. Create a bot with [@BotFather](https://t.me/BotFather) and put the token in `.env` as `BOT_TOKEN`.
2. Start the bot, send `/id`, put the number in `.env` as `OWNER_ID`, restart.
3. Send `/start`; the **🛠 Admin Panel** button appears for the owner.

## How products work

Products form a tree. When adding a product, press **Add another** for more options at the same level,
tap an option to create sub-options inside it, and press **Done** to mark the remaining options as sellable.
Only sellable items (the leaves) have a price and stock.

## Stock import format

- **Excel (.xlsx)** or **CSV (.csv)**:
  - **Row 1 = column headers** (never stored as stock).
  - **Every next row = one stock item.**
  - Column order, empty cells and values are kept exactly as in the file.
  - A row with more columns than the header is rejected (nothing is added).
  - Fully empty rows are skipped. CSV delimiter `,` `;` or tab is detected from the header row.
- **Manual**: one item per line (plain text, no headers).
- Duplicates (including already sold items) are skipped automatically.
- **Export** returns the available stock as a CSV (same columns) when all items share the same headers.

## Security notes

- Keep `.env` and `shop.db` out of Git (already in `.gitignore`). `shop.db` contains customer and stock data.
- Run the bot on an always-on server (VPS) for real use, and back up `shop.db` regularly.

## Project layout

```
shop_bot.py        # the bot (menus, admin panel, product + stock management)
requirements.txt
.env.example
```
