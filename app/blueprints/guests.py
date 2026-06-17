# ── guests ────────────────────────────────────────────────────────────────────
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
import re
from ..database import get_db, audit
from ..helpers import role_required

bp = Blueprint('guests', __name__, url_prefix='/guests')


@bp.route('/')
@login_required
def index():
    q = request.args.get('q', '').strip()
    db = get_db()
    if q:
        rows = db.execute(
            "SELECT * FROM guests WHERE full_name LIKE %s OR phone LIKE %s OR passport_number LIKE %s ORDER BY full_name",
            (f'%{q}%', f'%{q}%', f'%{q}%')
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM guests ORDER BY full_name").fetchall()
    return render_template('guests/index.html', guests=rows, q=q)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'receptionist')
def new():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        if not full_name:
            flash('Укажите ФИО гостя.', 'danger')
        elif re.search(r'\d', full_name):
            flash('ФИО не должно содержать цифры.', 'danger')
        else:
            db = get_db()
            db.execute(
                "INSERT INTO guests(full_name,passport_number,phone,email,address,notes,vip_flag) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (full_name, request.form.get('passport_number','').strip(),
                 request.form.get('phone','').strip(), request.form.get('email','').strip(),
                 request.form.get('address','').strip(), request.form.get('notes','').strip(),
                 1 if request.form.get('vip_flag') else 0)
            )
            gid = db.execute('SELECT LAST_INSERT_ID()').fetchone()[0]
            audit('guest_create', 'guest', gid, full_name)
            flash('Гость добавлен.', 'success')
            return redirect(url_for('guests.detail', gid=gid))
    return render_template('guests/new.html')


@bp.route('/<int:gid>')
@login_required
def detail(gid):
    db = get_db()
    g = db.execute("SELECT * FROM guests WHERE id=%s", (gid,)).fetchone()
    if not g:
        flash('Гость не найден.', 'danger')
        return redirect(url_for('guests.index'))
    history = db.execute(
        '''SELECT b.*, rc.name AS cat_name, r.number AS room_number
           FROM bookings b JOIN room_categories rc ON rc.id=b.category_id
           LEFT JOIN rooms r ON r.id=b.room_id
           WHERE b.guest_id=%s ORDER BY b.check_in_date DESC''', (gid,)
    ).fetchall()
    return render_template('guests/detail.html', g=g, history=history)


@bp.route('/<int:gid>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'receptionist')
def edit(gid):
    db = get_db()
    g = db.execute("SELECT * FROM guests WHERE id=%s", (gid,)).fetchone()
    if not g:
        flash('Гость не найден.', 'danger')
        return redirect(url_for('guests.index'))
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        if not full_name:
            flash('Укажите ФИО.', 'danger')
        elif re.search(r'\d', full_name):
            flash('ФИО не должно содержать цифры.', 'danger')
        else:
            db.execute(
                "UPDATE guests SET full_name=%s,passport_number=%s,phone=%s,email=%s,address=%s,notes=%s,vip_flag=%s WHERE id=%s",
                (full_name, request.form.get('passport_number','').strip(),
                 request.form.get('phone','').strip(), request.form.get('email','').strip(),
                 request.form.get('address','').strip(), request.form.get('notes','').strip(),
                 1 if request.form.get('vip_flag') else 0, gid)
            )
            audit('guest_update', 'guest', gid, full_name)
            flash('Данные гостя обновлены.', 'success')
            return redirect(url_for('guests.detail', gid=gid))
    return render_template('guests/edit.html', g=g)
