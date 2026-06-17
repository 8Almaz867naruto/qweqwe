import secrets
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..database import get_db, audit
from ..helpers import role_required, calculate_stay_cost, nights_count, STATUS_BOOKING_LABELS

bp = Blueprint('bookings', __name__, url_prefix='/bookings')


@bp.route('/')
@login_required
def index():
    db = get_db()
    q      = request.args.get('q', '').strip()
    status = request.args.get('status', '')
    sql = '''SELECT b.*, g.full_name AS guest_name, rc.name AS cat_name,
                    r.number AS room_number
             FROM bookings b
             JOIN guests g ON g.id = b.guest_id
             JOIN room_categories rc ON rc.id = b.category_id
             LEFT JOIN rooms r ON r.id = b.room_id
             WHERE 1=1'''
    params = []
    if q:
        sql += ' AND (g.full_name LIKE %s OR b.booking_code LIKE %s)'
        params += [f'%{q}%', f'%{q}%']
    if status:
        sql += ' AND b.status = %s'
        params.append(status)
    sql += ' ORDER BY b.check_in_date DESC LIMIT 200'
    bookings = db.execute(sql, params).fetchall()
    return render_template('bookings/index.html',
        bookings=bookings, q=q, status_filter=status,
        status_labels=STATUS_BOOKING_LABELS)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'receptionist')
def new():
    db = get_db()
    if request.method == 'POST':
        guest_id     = request.form.get('guest_id', type=int)
        category_id  = request.form.get('category_id', type=int)
        room_id      = request.form.get('room_id') or None
        if room_id:
            room_id = int(room_id)
        check_in     = request.form.get('check_in_date', '').strip()
        check_out    = request.form.get('check_out_date', '').strip()
        guests_count = request.form.get('guests_count', 1, type=int)
        notes        = request.form.get('notes', '').strip()

        if not (guest_id and category_id and check_in and check_out):
            flash('Заполните все обязательные поля.', 'danger')
        elif check_in >= check_out:
            flash('Дата выезда должна быть позже даты заезда.', 'danger')
        else:
            try:
                db.execute('START TRANSACTION')
                if room_id:
                    conflict = db.execute(
                        '''SELECT id FROM bookings
                           WHERE room_id = %s AND status IN ('confirmed','checked_in')
                             AND NOT (check_out_date <= %s OR check_in_date >= %s)''',
                        (room_id, check_in, check_out)
                    ).fetchone()
                    if conflict:
                        db.execute('ROLLBACK')
                        flash('Выбранный номер уже занят на эти даты.', 'danger')
                        return redirect(url_for('bookings.new'))

                guest = db.execute('SELECT vip_flag FROM guests WHERE id=%s', (guest_id,)).fetchone()
                vip = bool(guest['vip_flag']) if guest else False
                total = calculate_stay_cost(category_id, check_in, check_out, vip)
                code  = secrets.token_hex(4).upper()

                db.execute(
                    '''INSERT INTO bookings
                       (guest_id,category_id,room_id,check_in_date,check_out_date,
                        booking_code,guests_count,total_amount,notes,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (guest_id, category_id, room_id, check_in, check_out,
                     code, guests_count, total, notes, current_user.id)
                )
                bid = db.execute('SELECT LAST_INSERT_ID()').fetchone()[0]
                if room_id:
                    db.execute("UPDATE rooms SET status='booked' WHERE id=%s", (room_id,))
                db.execute('COMMIT')
                audit('booking_create', 'booking', bid, code)
                flash(f'Бронирование создано. Код: {code}', 'success')
                return redirect(url_for('bookings.detail', bid=bid))
            except Exception as e:
                db.execute('ROLLBACK')
                flash(f'Ошибка: {e}', 'danger')

    categories = db.execute('SELECT * FROM room_categories ORDER BY name').fetchall()
    guests     = db.execute("SELECT id, full_name, phone FROM guests ORDER BY full_name").fetchall()
    selected_room = None
    selected_category_id = None
    room_arg = request.args.get('room_id', type=int)
    if room_arg:
        selected_room = db.execute('SELECT id, category_id FROM rooms WHERE id=%s', (room_arg,)).fetchone()
        if selected_room:
            selected_category_id = selected_room['category_id']
    return render_template('bookings/new.html', categories=categories, guests=guests,
        selected_room_id=room_arg if selected_room else None,
        selected_category_id=selected_category_id)


@bp.route('/<int:bid>')
@login_required
def detail(bid):
    db = get_db()
    b = db.execute(
        '''SELECT b.*, g.full_name AS guest_name, g.phone AS guest_phone,
                  g.vip_flag, rc.name AS cat_name, r.number AS room_number
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.id=%s''', (bid,)
    ).fetchone()
    if not b:
        flash('Бронирование не найдено.', 'danger')
        return redirect(url_for('bookings.index'))

    orders = db.execute(
        '''SELECT so.*, s.name AS svc_name, s.unit FROM service_orders so
           JOIN services s ON s.id=so.service_id WHERE so.booking_id=%s
           ORDER BY so.ordered_at''', (bid,)
    ).fetchall()
    payments = db.execute(
        'SELECT * FROM payments WHERE booking_id=%s ORDER BY paid_at', (bid,)
    ).fetchall()
    paid_total = sum(p['amount'] for p in payments)
    svc_total  = sum(o['amount'] for o in orders)
    due        = round(b['total_amount'] + svc_total - paid_total, 2)
    nights     = nights_count(b['check_in_date'], b['check_out_date'])
    services   = db.execute("SELECT * FROM services WHERE is_active=1 ORDER BY name").fetchall()
    return render_template('bookings/detail.html',
        b=b, orders=orders, payments=payments,
        paid_total=paid_total, svc_total=svc_total, due=due,
        nights=nights, services=services,
        status_labels=STATUS_BOOKING_LABELS)


@bp.route('/<int:bid>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'receptionist')
def edit(bid):
    db = get_db()
    b = db.execute('SELECT * FROM bookings WHERE id=%s', (bid,)).fetchone()
    if not b:
        flash('Бронирование не найдено.', 'danger')
        return redirect(url_for('bookings.index'))
    if b['status'] != 'confirmed':
        flash('Редактировать можно только подтверждённые бронирования до заселения.', 'danger')
        return redirect(url_for('bookings.detail', bid=bid))

    if request.method == 'POST':
        guest_id = request.form.get('guest_id', type=int)
        category_id = request.form.get('category_id', type=int)
        room_id = request.form.get('room_id') or None
        if room_id:
            room_id = int(room_id)
        check_in = request.form.get('check_in_date', '').strip()
        check_out = request.form.get('check_out_date', '').strip()
        guests_count = request.form.get('guests_count', 1, type=int)
        notes = request.form.get('notes', '').strip()

        if not (guest_id and category_id and check_in and check_out):
            flash('Заполните все обязательные поля.', 'danger')
        elif check_in >= check_out:
            flash('Дата выезда должна быть позже даты заезда.', 'danger')
        else:
            try:
                db.execute('START TRANSACTION')
                if room_id:
                    conflict = db.execute(
                        """SELECT id FROM bookings
                           WHERE room_id=%s AND id!=%s AND status IN ('confirmed','checked_in')
                             AND NOT (check_out_date <= %s OR check_in_date >= %s)""",
                        (room_id, bid, check_in, check_out)
                    ).fetchone()
                    if conflict:
                        db.execute('ROLLBACK')
                        flash('Выбранный номер уже занят на эти даты.', 'danger')
                        return redirect(url_for('bookings.edit', bid=bid))
                guest = db.execute('SELECT vip_flag FROM guests WHERE id=%s', (guest_id,)).fetchone()
                vip = bool(guest['vip_flag']) if guest else False
                total = calculate_stay_cost(category_id, check_in, check_out, vip)
                old_room = b['room_id']
                db.execute(
                    """UPDATE bookings SET guest_id=%s, category_id=%s, room_id=%s,
                       check_in_date=%s, check_out_date=%s, guests_count=%s,
                       total_amount=%s, notes=%s WHERE id=%s""",
                    (guest_id, category_id, room_id, check_in, check_out, guests_count, total, notes, bid)
                )
                if old_room and old_room != room_id:
                    db.execute("UPDATE rooms SET status='free' WHERE id=%s", (old_room,))
                if room_id:
                    db.execute("UPDATE rooms SET status='booked' WHERE id=%s", (room_id,))
                db.execute('COMMIT')
                audit('booking_update', 'booking', bid, b['booking_code'])
                flash('Бронирование обновлено.', 'success')
                return redirect(url_for('bookings.detail', bid=bid))
            except Exception as e:
                db.execute('ROLLBACK')
                flash(f'Ошибка: {e}', 'danger')

    categories = db.execute('SELECT * FROM room_categories ORDER BY name').fetchall()
    guests = db.execute('SELECT id, full_name, phone FROM guests ORDER BY full_name').fetchall()
    return render_template('bookings/edit.html', b=b, categories=categories, guests=guests)

@bp.route('/<int:bid>/cancel', methods=['POST'])
@login_required
@role_required('admin', 'receptionist')
def cancel(bid):
    db = get_db()
    b = db.execute("SELECT * FROM bookings WHERE id=%s", (bid,)).fetchone()
    if b and b['status'] in ('confirmed',):
        db.execute("UPDATE bookings SET status='cancelled' WHERE id=%s", (bid,))
        if b['room_id']:
            db.execute("UPDATE rooms SET status='free' WHERE id=%s", (b['room_id'],))
        audit('booking_cancel', 'booking', bid)
        flash('Бронирование отменено.', 'success')
    else:
        flash('Нельзя отменить бронирование в текущем статусе.', 'danger')
    return redirect(url_for('bookings.detail', bid=bid))


@bp.route('/api/cost')
@login_required
def api_cost():
    cat_id    = request.args.get('category_id', type=int)
    check_in  = request.args.get('check_in', '')
    check_out = request.args.get('check_out', '')
    guest_id  = request.args.get('guest_id', type=int)
    if not (cat_id and check_in and check_out):
        return jsonify({'cost': 0, 'nights': 0})
    vip = False
    if guest_id:
        db = get_db()
        g = db.execute('SELECT vip_flag FROM guests WHERE id=%s', (guest_id,)).fetchone()
        vip = bool(g['vip_flag']) if g else False
    n = nights_count(check_in, check_out)
    c = calculate_stay_cost(cat_id, check_in, check_out, vip)
    return jsonify({'cost': c, 'nights': n, 'vip': vip})
