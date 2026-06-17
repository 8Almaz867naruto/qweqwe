from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..database import get_db, audit
from ..helpers import role_required, METHOD_LABELS

bp = Blueprint('payments', __name__, url_prefix='/payments')


@bp.route('/add/<int:bid>', methods=['POST'])
@login_required
@role_required('admin', 'receptionist')
def add(bid):
    db = get_db()
    b = db.execute("SELECT status FROM bookings WHERE id=%s", (bid,)).fetchone()
    if not b or b['status'] not in ('confirmed', 'checked_in'):
        flash('Нельзя принять оплату для данного бронирования.', 'danger')
        return redirect(url_for('bookings.detail', bid=bid))

    amount = request.form.get('amount', 0, type=float)
    method = request.form.get('method', 'cash')
    notes  = request.form.get('notes', '').strip()
    if amount <= 0:
        flash('Сумма должна быть больше нуля.', 'danger')
    elif method not in ('cash', 'card', 'transfer'):
        flash('Недопустимый способ оплаты.', 'danger')
    else:
        db.execute(
            "INSERT INTO payments(booking_id,amount,method,received_by,notes) VALUES(%s,%s,%s,%s,%s)",
            (bid, amount, method, current_user.id, notes)
        )
        audit('payment', 'booking', bid, f'{amount:.2f} {method}')
        flash(f'Оплата {amount:.2f} руб. принята.', 'success')
    return redirect(url_for('bookings.detail', bid=bid))


@bp.route('/')
@login_required
@role_required('admin', 'manager')
def index():
    db = get_db()
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    sql = '''SELECT p.*, b.booking_code, g.full_name AS guest_name, u.full_name AS staff_name
             FROM payments p
             LEFT JOIN bookings b ON b.id=p.booking_id
             LEFT JOIN guests g ON g.id=b.guest_id
             JOIN users u ON u.id=p.received_by
             WHERE 1=1'''
    params = []
    if date_from:
        sql += ' AND p.paid_at >= %s'
        params.append(date_from)
    if date_to:
        sql += ' AND p.paid_at <= %s'
        params.append(date_to + ' 23:59:59')
    sql += ' ORDER BY p.paid_at DESC LIMIT 500'
    rows = db.execute(sql, params).fetchall()
    total = sum(r['amount'] for r in rows)
    return render_template('payments/index.html',
        payments=rows, total=total,
        date_from=date_from, date_to=date_to,
        method_labels=METHOD_LABELS)
