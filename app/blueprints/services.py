from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..database import get_db, audit
from ..helpers import role_required

bp = Blueprint('services', __name__, url_prefix='/services')


@bp.route('/')
@login_required
def index():
    db = get_db()
    rows = db.execute("SELECT * FROM services ORDER BY is_active DESC, name").fetchall()
    return render_template('services/index.html', services=rows)


@bp.route('/order/<int:bid>', methods=['POST'])
@login_required
@role_required('admin', 'receptionist')
def order(bid):
    """Добавить заказ услуги к активному бронированию."""
    db = get_db()
    b = db.execute("SELECT * FROM bookings WHERE id=%s AND status='checked_in'", (bid,)).fetchone()
    if not b:
        flash('Активное заселение не найдено.', 'danger')
        return redirect(url_for('bookings.detail', bid=bid))

    svc_id   = request.form.get('service_id', type=int)
    quantity = request.form.get('quantity', 1, type=int)
    if not svc_id or quantity < 1:
        flash('Укажите услугу и количество.', 'danger')
        return redirect(url_for('bookings.detail', bid=bid))

    svc = db.execute("SELECT * FROM services WHERE id=%s AND is_active=1", (svc_id,)).fetchone()
    if not svc:
        flash('Услуга не найдена.', 'danger')
        return redirect(url_for('bookings.detail', bid=bid))

    amount = round(svc['price'] * quantity, 2)
    db.execute(
        "INSERT INTO service_orders(booking_id,service_id,quantity,amount,ordered_by) VALUES(%s,%s,%s,%s,%s)",
        (bid, svc_id, quantity, amount, current_user.id)
    )
    audit('service_order', 'booking', bid, f'{svc["name"]} x{quantity} = {amount}')
    flash(f'Услуга «{svc["name"]}» добавлена ({amount:.2f} руб.).', 'success')
    return redirect(url_for('bookings.detail', bid=bid))


@bp.route('/manage', methods=['GET'])
@login_required
@role_required('admin')
def manage():
    db = get_db()
    rows = db.execute("SELECT * FROM services ORDER BY name").fetchall()
    return render_template('services/manage.html', services=rows)


@bp.route('/manage/new', methods=['POST'])
@login_required
@role_required('admin')
def manage_new():
    db = get_db()
    name  = request.form.get('name', '').strip()
    unit  = request.form.get('unit', 'шт.').strip()
    price = request.form.get('price', 0, type=float)
    if not name:
        flash('Укажите название услуги.', 'danger')
    else:
        db.execute("INSERT INTO services(name,unit,price) VALUES(%s,%s,%s)", (name, unit, price))
        flash('Услуга добавлена.', 'success')
    return redirect(url_for('services.manage'))


@bp.route('/manage/<int:sid>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def toggle(sid):
    db = get_db()
    svc = db.execute("SELECT is_active FROM services WHERE id=%s", (sid,)).fetchone()
    if svc:
        db.execute("UPDATE services SET is_active=%s WHERE id=%s", (0 if svc['is_active'] else 1, sid))
    return redirect(url_for('services.manage'))
