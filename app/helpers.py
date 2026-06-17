from functools import wraps
from datetime import date, timedelta
from flask import abort
from flask_login import current_user
from .database import get_db


def role_required(*roles):
    """Декоратор: проверяет роль текущего пользователя."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def calculate_stay_cost(category_id, check_in_str, check_out_str, vip=False):
    """Поночное суммирование с учётом активных сезонных тарифов."""
    db = get_db()
    tariffs = db.execute(
        '''SELECT t.daily_rate, s.start_date, s.end_date
           FROM tariffs t JOIN seasons s ON s.id = t.season_id
           WHERE t.category_id = %s''',
        (category_id,)
    ).fetchall()
    base_row = db.execute(
        'SELECT base_rate FROM room_categories WHERE id = %s', (category_id,)
    ).fetchone()
    base_rate = base_row['base_rate'] if base_row else 0

    d1 = date.fromisoformat(check_in_str)
    d2 = date.fromisoformat(check_out_str)
    if d2 <= d1:
        return 0.0

    total = 0.0
    cur = d1
    while cur < d2:
        cur_str = cur.isoformat()
        rate = base_rate
        for t in tariffs:
            if t['start_date'] <= cur_str <= t['end_date']:
                rate = t['daily_rate']
                break
        total += rate
        cur += timedelta(days=1)

    if vip:
        total *= 0.90 
    return round(total, 2)


def nights_count(check_in_str, check_out_str):
    d1 = date.fromisoformat(check_in_str)
    d2 = date.fromisoformat(check_out_str)
    return max((d2 - d1).days, 0)


STATUS_ROOM_LABELS = {
    'free':        ('Свободен',         'success'),
    'booked':      ('Забронирован',      'warning'),
    'occupied':    ('Занят',             'danger'),
    'cleaning':    ('Уборка',            'info'),
    'maintenance': ('Ремонт',            'secondary'),
}
STATUS_BOOKING_LABELS = {
    'confirmed':  ('Подтверждено',  'warning'),
    'checked_in': ('Заселён',       'success'),
    'completed':  ('Завершено',     'secondary'),
    'cancelled':  ('Отменено',      'dark'),
}
METHOD_LABELS = {
    'cash':     'Наличными',
    'card':     'Банковская карта',
    'transfer': 'Перевод',
}
ROLE_LABELS = {
    'admin':       'Администратор',
    'receptionist':'Сотрудник стойки',
    'housekeeper': 'Горничная',
    'manager':     'Управляющий',
}
