from datetime import date, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from ..database import get_db, audit
from ..helpers import role_required, STATUS_ROOM_LABELS

bp = Blueprint('rooms', __name__, url_prefix='/rooms')


@bp.route('/')
@bp.route('/chart')
@login_required
def chart():
    db = get_db()
    try:
        start = date.fromisoformat(request.args.get('start', ''))
    except ValueError:
        start = date.today() - timedelta(days=date.today().weekday())
    days = int(request.args.get('days', 14))
    days = max(7, min(days, 31))
    category_filter = request.args.get('category_id', type=int)
    dates = [start + timedelta(d) for d in range(days)]
    end   = dates[-1]

    room_sql = 'SELECT r.*, rc.name AS cat_name FROM rooms r JOIN room_categories rc ON rc.id = r.category_id'
    room_params = []
    if category_filter:
        room_sql += ' WHERE r.category_id=%s'
        room_params.append(category_filter)
    room_sql += ' ORDER BY r.floor, r.number'
    rooms = db.execute(room_sql, room_params).fetchall()

    bookings = db.execute(
        '''SELECT b.room_id, b.check_in_date, b.check_out_date,
                  b.booking_code, b.status, b.id,
                  g.full_name AS guest_name
           FROM bookings b JOIN guests g ON g.id = b.guest_id
           WHERE b.status IN ('confirmed','checked_in')
             AND b.room_id IS NOT NULL
             AND b.check_in_date <= %s AND b.check_out_date > %s''',
        (end.isoformat(), start.isoformat())
    ).fetchall()

    matrix = {}
    for r in rooms:
        matrix[r['id']] = {}
    for b in bookings:
        d = date.fromisoformat(b['check_in_date'])
        dend = date.fromisoformat(b['check_out_date'])
        while d < dend and d <= end:
            if d >= start:
                matrix[b['room_id']][d.isoformat()] = b
            d += timedelta(days=1)

    prev_start = (start - timedelta(days=days)).isoformat()
    next_start = (start + timedelta(days=days)).isoformat()
    categories = db.execute('SELECT * FROM room_categories ORDER BY name').fetchall()
    status_map = {k: 0 for k in STATUS_ROOM_LABELS}
    for r in rooms:
        status_map[r['status']] = status_map.get(r['status'], 0) + 1
    today = date.today().isoformat()
    today_arrivals = db.execute("SELECT COUNT(*) FROM bookings WHERE status='confirmed' AND check_in_date=%s", (today,)).fetchone()[0]
    today_departures = db.execute("SELECT COUNT(*) FROM bookings WHERE status='checked_in' AND check_out_date=%s", (today,)).fetchone()[0]
    active_count = db.execute("SELECT COUNT(*) FROM bookings WHERE status='checked_in'").fetchone()[0]

    return render_template('rooms/chart.html',
        rooms=rooms, dates=dates, matrix=matrix,
        start=start, days=days, prev_start=prev_start, next_start=next_start,
        status_labels=STATUS_ROOM_LABELS, categories=categories,
        category_filter=category_filter, status_map=status_map,
        today_arrivals=today_arrivals, today_departures=today_departures,
        active_count=active_count)


@bp.route('/status/<int:room_id>', methods=['POST'])
@login_required
@role_required('admin', 'receptionist', 'housekeeper')
def set_status(room_id):
    new_status = request.form.get('status')
    allowed = ['free', 'booked', 'occupied', 'cleaning', 'maintenance']
    if new_status not in allowed:
        flash('Недопустимый статус.', 'danger')
        return redirect(url_for('rooms.chart'))
    db = get_db()
    db.execute('UPDATE rooms SET status=%s WHERE id=%s', (new_status, room_id))
    audit('status_change', 'room', room_id, f'→ {new_status}')
    flash(f'Статус номера обновлён.', 'success')
    return redirect(request.referrer or url_for('rooms.chart'))


@bp.route('/api/free')
@login_required
def api_free():
    """API: свободные номера категории на период."""
    cat_id    = request.args.get('category_id', type=int)
    check_in  = request.args.get('check_in', '')
    check_out = request.args.get('check_out', '')
    excl_id   = request.args.get('exclude', type=int)  
    if not (cat_id and check_in and check_out):
        return jsonify([])
    db = get_db()
    # Получаем номера, которые уже заняты на выбранный период.
    if excl_id:
        busy_rows = db.execute(
            '''SELECT DISTINCT b.room_id FROM bookings b
               WHERE b.status IN ('confirmed','checked_in')
                 AND b.room_id IS NOT NULL AND b.id != %s
                 AND NOT (b.check_out_date <= %s OR b.check_in_date >= %s)''',
            (excl_id, check_in, check_out)
        ).fetchall()
    else:
        busy_rows = db.execute(
            '''SELECT DISTINCT b.room_id FROM bookings b
               WHERE b.status IN ('confirmed','checked_in')
                 AND b.room_id IS NOT NULL
                 AND NOT (b.check_out_date <= %s OR b.check_in_date >= %s)''',
            (check_in, check_out)
        ).fetchall()
    busy_ids = {r['room_id'] for r in busy_rows}

    rooms = db.execute(
        "SELECT id, number, floor FROM rooms WHERE category_id=%s AND status IN ('free','booked') ORDER BY floor, number",
        (cat_id,)
    ).fetchall()
    result = [{'id': r['id'], 'number': r['number'], 'floor': r['floor']}
              for r in rooms if r['id'] not in busy_ids]
    return jsonify(result)
