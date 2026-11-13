import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import BOT_TOKEN, OWNER_ID
from app.db import init_db, Session, Admin
from app.handlers.customer import router as customer_router
from app.handlers.admin import router as admin_router

async def main():
    if not BOT_TOKEN or OWNER_ID <= 0:
        raise RuntimeError("Set BOT_TOKEN and OWNER_ID in Railway Variables/.env")
    logging.basicConfig(level=logging.INFO)
    await init_db()
    async with Session() as s:
        if not await s.get(Admin, OWNER_ID):
            s.add(Admin(user_id=OWNER_ID, permissions="products,stock,users,payments,referral,reseller,stats,admins,settings", active=True))
            await s.commit()
    bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(customer_router)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("My-Sell-Bot is running")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__=="__main__":
    asyncio.run(main())
