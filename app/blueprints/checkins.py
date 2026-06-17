from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from ..database import get_db, audit
from ..helpers import role_required, calculate_stay_cost, nights_count, STATUS_BOOKING_LABELS

bp = Blueprint('checkins', __name__, url_prefix='/checkins')


@bp.route('/')
@login_required
def index():
    db = get_db()
    today = date.today().isoformat()
    arriving = db.execute(
        '''SELECT b.*, g.full_name AS guest_name, rc.name AS cat_name, r.number AS room_number
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.status='confirmed' AND b.check_in_date=%s ORDER BY b.check_in_date''',
        (today,)
    ).fetchall()
    departing = db.execute(
        '''SELECT b.*, g.full_name AS guest_name, rc.name AS cat_name, r.number AS room_number
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.status='checked_in' AND b.check_out_date=%s ORDER BY b.check_out_date''',
        (today,)
    ).fetchall()
    active = db.execute(
        '''SELECT b.*, g.full_name AS guest_name, rc.name AS cat_name, r.number AS room_number
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.status='checked_in' ORDER BY b.check_out_date''',
    ).fetchall()
    overdue = [x for x in active if x['check_out_date'] < today]
    return render_template('checkins/index.html',
        arriving=arriving, departing=departing, active=active, overdue=overdue,
        today=today, status_labels=STATUS_BOOKING_LABELS)


@bp.route('/checkin/<int:bid>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'receptionist')
def do_checkin(bid):
    db = get_db()
    b = db.execute(
        '''SELECT b.*, g.full_name AS guest_name, g.passport_number, g.phone,
                  rc.name AS cat_name
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           WHERE b.id=%s AND b.status='confirmed' ''', (bid,)
    ).fetchone()
    if not b:
        flash('Бронирование не найдено или статус не позволяет заселение.', 'danger')
        return redirect(url_for('checkins.index'))

    busy = db.execute(
        '''SELECT DISTINCT room_id FROM bookings
           WHERE status IN ('confirmed','checked_in') AND room_id IS NOT NULL AND id != %s
             AND NOT (check_out_date <= %s OR check_in_date >= %s)''',
        (bid, b['check_in_date'], b['check_out_date'])
    ).fetchall()
    busy_ids = {r['room_id'] for r in busy}
    free_rooms = db.execute(
        "SELECT * FROM rooms WHERE category_id=%s AND status IN ('free','booked') ORDER BY floor, number",
        (b['category_id'],)
    ).fetchall()
    free_rooms = [r for r in free_rooms if r['id'] not in busy_ids]

    if request.method == 'POST':
        room_id  = request.form.get('room_id', type=int)
        passport = request.form.get('passport', '').strip()
        if not room_id:
            flash('Выберите номер для заселения.', 'danger')
        else:
            try:
                db.execute('START TRANSACTION')
                conflict = db.execute(
                    '''SELECT id FROM bookings WHERE room_id=%s AND id!=%s
                       AND status IN ('confirmed','checked_in')
                       AND NOT (check_out_date<=%s OR check_in_date>=%s)''',
                    (room_id, bid, b['check_in_date'], b['check_out_date'])
                ).fetchone()
                if conflict:
                    db.execute('ROLLBACK')
                    flash('Номер только что был занят. Выберите другой.', 'danger')
                    return redirect(url_for('checkins.do_checkin', bid=bid))

                db.execute(
                    "UPDATE bookings SET status='checked_in', room_id=%s WHERE id=%s",
                    (room_id, bid)
                )
                db.execute("UPDATE rooms SET status='occupied' WHERE id=%s", (room_id,))
                if passport:
                    db.execute("UPDATE guests SET passport_number=%s WHERE id=%s",
                               (passport, b['guest_id']))
                db.execute('COMMIT')
                audit('checkin', 'booking', bid, f'room {room_id}')
                flash('Гость заселён.', 'success')
                return redirect(url_for('bookings.detail', bid=bid))
            except Exception as e:
                db.execute('ROLLBACK')
                flash(f'Ошибка заселения: {e}', 'danger')

    return render_template('checkins/checkin.html', b=b, free_rooms=free_rooms)


@bp.route('/checkout/<int:bid>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'receptionist')
def do_checkout(bid):
    db = get_db()
    b = db.execute(
        '''SELECT b.*, g.full_name AS guest_name, g.vip_flag, rc.name AS cat_name,
                  r.number AS room_number, r.id AS rid
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.id=%s AND b.status='checked_in' ''', (bid,)
    ).fetchone()
    if not b:
        flash('Заселение не найдено или уже завершено.', 'danger')
        return redirect(url_for('checkins.index'))

    orders   = db.execute(
        '''SELECT so.*, s.name AS svc_name, s.unit FROM service_orders so
           JOIN services s ON s.id=so.service_id WHERE so.booking_id=%s ORDER BY so.ordered_at''',
        (bid,)
    ).fetchall()
    payments = db.execute('SELECT * FROM payments WHERE booking_id=%s ORDER BY paid_at', (bid,)).fetchall()
    svc_total  = round(sum(o['amount'] for o in orders), 2)
    paid_total = round(sum(p['amount'] for p in payments), 2)
    nights     = nights_count(b['check_in_date'], b['check_out_date'])
    due        = round(b['total_amount'] + svc_total - paid_total, 2)

    if request.method == 'POST':
        try:
            db.execute('START TRANSACTION')
            db.execute("UPDATE bookings SET status='completed' WHERE id=%s", (bid,))
            db.execute("UPDATE rooms SET status='cleaning' WHERE id=%s", (b['rid'],))
            db.execute('COMMIT')
            audit('checkout', 'booking', bid)
            flash('Выселение оформлено. Номер отправлен на уборку.', 'success')
            return redirect(url_for('bookings.detail', bid=bid))
        except Exception as e:
            db.execute('ROLLBACK')
            flash(f'Ошибка выселения: {e}', 'danger')

    return render_template('checkins/checkout.html',
        b=b, orders=orders, payments=payments,
        svc_total=svc_total, paid_total=paid_total, due=due, nights=nights)
