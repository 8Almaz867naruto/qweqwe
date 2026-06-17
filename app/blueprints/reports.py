import csv
import io
from datetime import date, timedelta
from flask import Blueprint, render_template, request, Response
from flask_login import login_required
from ..database import get_db
from ..helpers import role_required

bp = Blueprint('reports', __name__, url_prefix='/reports')


def _get_period():
    today = date.today()
    d_from = request.args.get('date_from') or (today.replace(day=1)).isoformat()
    d_to   = request.args.get('date_to')   or today.isoformat()
    return d_from, d_to


@bp.route('/')
@login_required
@role_required('admin', 'manager')
def dashboard():
    db = get_db()
    d_from, d_to = _get_period()

    total_rooms = db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    by_status = db.execute(
        "SELECT status, COUNT(*) AS cnt FROM rooms GROUP BY status"
    ).fetchall()
    status_map = {r['status']: r['cnt'] for r in by_status}

    days_in_period = max((date.fromisoformat(d_to) - date.fromisoformat(d_from)).days, 1)
    occ_nights = db.execute(
        '''SELECT COUNT(*) FROM bookings
           WHERE status IN ('checked_in','completed')
             AND check_in_date <= %s AND check_out_date > %s''',
        (d_to, d_from)
    ).fetchone()[0]

    available_nights = total_rooms * days_in_period
    occupancy = round(occ_nights / available_nights * 100, 1) if available_nights else 0

    revenue_rooms = db.execute(
        '''SELECT COALESCE(SUM(total_amount),0) FROM bookings
           WHERE status IN ('checked_in','completed')
             AND check_in_date >= %s AND check_in_date <= %s''',
        (d_from, d_to)
    ).fetchone()[0]
    revenue_svc = db.execute(
        '''SELECT COALESCE(SUM(so.amount),0) FROM service_orders so
           JOIN bookings b ON b.id=so.booking_id
           WHERE b.status IN ('checked_in','completed')
             AND so.ordered_at >= %s AND so.ordered_at <= %s''',
        (d_from, d_to + ' 23:59:59')
    ).fetchone()[0]
    total_revenue = round(revenue_rooms + revenue_svc, 2)

    adr    = round(revenue_rooms / occ_nights, 2) if occ_nights else 0
    revpar = round(total_revenue / available_nights, 2) if available_nights else 0

    by_cat = db.execute(
        '''SELECT rc.name, COUNT(b.id) AS cnt, COALESCE(SUM(b.total_amount),0) AS rev
           FROM bookings b JOIN room_categories rc ON rc.id=b.category_id
           WHERE b.status IN ('checked_in','completed')
             AND b.check_in_date >= %s AND b.check_in_date <= %s
           GROUP BY rc.id ORDER BY rev DESC''',
        (d_from, d_to)
    ).fetchall()

    daily = db.execute(
        '''SELECT check_in_date AS dt, COUNT(*) AS cnt FROM bookings
           WHERE status IN ('confirmed','checked_in','completed')
             AND check_in_date >= %s AND check_in_date <= %s
           GROUP BY check_in_date ORDER BY dt''',
        (d_from, d_to)
    ).fetchall()

    return render_template('reports/dashboard.html',
        d_from=d_from, d_to=d_to,
        total_rooms=total_rooms, status_map=status_map,
        occupancy=occupancy, adr=adr, revpar=revpar,
        revenue_rooms=round(revenue_rooms, 2), revenue_svc=round(revenue_svc, 2),
        total_revenue=total_revenue, occ_nights=occ_nights,
        by_cat=by_cat, daily=daily)


@bp.route('/export')
@login_required
@role_required('admin', 'manager')
def export_csv():
    d_from, d_to = _get_period()
    db = get_db()
    rows = db.execute(
        '''SELECT b.booking_code, g.full_name AS guest, g.phone,
                  rc.name AS category, r.number AS room,
                  b.check_in_date, b.check_out_date, b.guests_count,
                  b.total_amount, b.prepaid_amount, b.status
           FROM bookings b JOIN guests g ON g.id=b.guest_id
           JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.check_in_date >= %s AND b.check_in_date <= %s
           ORDER BY b.check_in_date''',
        (d_from, d_to)
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Код брони','Гость','Телефон','Категория','Номер',
                     'Дата заезда','Дата выезда','Гостей','Стоимость',
                     'Предоплата','Статус'])
    for r in rows:
        writer.writerow(list(r))

    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': f'attachment; filename=report_{d_from}_{d_to}.csv'}
    )
