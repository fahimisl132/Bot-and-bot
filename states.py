from aiogram.fsm.state import State, StatesGroup

class ProductFlow(StatesGroup):
    root_name = State()
    add_child = State()
    rename = State()
    price = State()

class StockFlow(StatesGroup):
    manual = State()
    file = State()

class DepositFlow(StatesGroup):
    amount = State()
    reference = State()

class AdminFlow(StatesGroup):
    add_admin_id = State()
    add_admin_permissions = State()
    setting_value = State()
    broadcast = State()
    user_adjust = State()
