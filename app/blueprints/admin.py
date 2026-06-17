from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from werkzeug.security import generate_password_hash
from ..database import get_db, audit
from ..helpers import role_required, ROLE_LABELS, STATUS_ROOM_LABELS

bp = Blueprint('admin', __name__, url_prefix='/admin')


def _has_digits(value: str) -> bool:
    return any(ch.isdigit() for ch in value)


def _create_user(username: str, full_name: str, password: str, role: str):
    if role not in ROLE_LABELS:
        return False, 'Недопустимая роль пользователя.'
    if not (username and full_name and password):
        return False, 'Заполните логин, ФИО и пароль.'
    if len(username) < 3:
        return False, 'Логин должен быть не короче 3 символов.'
    if len(password) < 6:
        return False, 'Пароль должен быть не менее 6 символов.'
    if _has_digits(full_name):
        return False, 'В ФИО сотрудника нельзя использовать цифры.'

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users(username,password_hash,full_name,role) VALUES(%s,%s,%s,%s)",
            (username, generate_password_hash(password), full_name, role)
        )
        audit('user_create', 'user', None, f'{username}, {full_name}, {role}')
        return True, f'Пользователь {username} создан.'
    except Exception as e:
        text = str(e)
        if 'Duplicate' in text or '1062' in text:
            return False, 'Пользователь с таким логином уже существует.'
        return False, f'Ошибка: {e}'



@bp.route('/')
@login_required
@role_required('admin')
def index():
    db = get_db()
    users     = db.execute("SELECT * FROM users ORDER BY role, full_name").fetchall()
    rooms     = db.execute("SELECT r.*, rc.name AS cat_name FROM rooms r JOIN room_categories rc ON rc.id=r.category_id ORDER BY r.floor, r.number").fetchall()
    cats      = db.execute("SELECT * FROM room_categories ORDER BY name").fetchall()
    seasons   = db.execute("SELECT * FROM seasons ORDER BY start_date").fetchall()
    services  = db.execute("SELECT * FROM services ORDER BY is_active DESC, name").fetchall()
    return render_template('admin/index.html',
        users=users, rooms=rooms, cats=cats, seasons=seasons, services=services,
        role_labels=ROLE_LABELS, status_labels=STATUS_ROOM_LABELS)



@bp.route('/users/new', methods=['POST'])
@login_required
@role_required('admin')
def user_new():
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'receptionist')
    ok, message = _create_user(username, full_name, password, role)
    flash(message, 'success' if ok else 'danger')
    return redirect(url_for('admin.index'))


@bp.route('/receptionists/new', methods=['POST'])
@login_required
@role_required('admin')
def receptionist_new():
    username = request.form.get('username', '').strip()
    full_name = request.form.get('full_name', '').strip()
    password = request.form.get('password', '')
    ok, message = _create_user(username, full_name, password, 'receptionist')
    flash(message, 'success' if ok else 'danger')
    return redirect(url_for('admin.index'))


@bp.route('/users/<int:uid>/update', methods=['POST'])
@login_required
@role_required('admin')
def user_update(uid):
    db = get_db()
    fn = request.form.get('full_name', '').strip()
    role = request.form.get('role', 'receptionist')
    if role not in ROLE_LABELS:
        flash('Недопустимая роль пользователя.', 'danger')
    elif not fn:
        flash('Укажите ФИО пользователя.', 'danger')
    elif _has_digits(fn):
        flash('В ФИО пользователя нельзя использовать цифры.', 'danger')
    else:
        db.execute('UPDATE users SET full_name=%s, role=%s WHERE id=%s', (fn, role, uid))
        audit('user_update', 'user', uid, f'{fn}, {role}')
        flash('Пользователь обновлён.', 'success')
    return redirect(url_for('admin.index'))


@bp.route('/users/<int:uid>/reset', methods=['POST'])
@login_required
@role_required('admin')
def user_reset(uid):
    db = get_db()
    pw = request.form.get('password', '')
    if len(pw) < 6:
        flash('Пароль должен быть не менее 6 символов.', 'danger')
    else:
        db.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                   (generate_password_hash(pw), uid))
        flash('Пароль сброшен.', 'success')
    return redirect(url_for('admin.index'))


@bp.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def user_delete(uid):
    from flask_login import current_user
    if uid == current_user.id:
        flash('Нельзя удалить собственную учётную запись.', 'danger')
    else:
        get_db().execute("DELETE FROM users WHERE id=%s", (uid,))
        flash('Пользователь удалён.', 'success')
    return redirect(url_for('admin.index'))



@bp.route('/cats/new', methods=['POST'])
@login_required
@role_required('admin')
def cat_new():
    db   = get_db()
    name = request.form.get('name', '').strip()
    cap  = request.form.get('capacity', 1, type=int)
    rate = request.form.get('base_rate', 0, type=float)
    desc = request.form.get('description', '').strip()
    if not name or rate <= 0:
        flash('Укажите название и ставку > 0.', 'danger')
    else:
        try:
            db.execute("INSERT INTO room_categories(name,capacity,base_rate,description) VALUES(%s,%s,%s,%s)",
                       (name, cap, rate, desc))
            flash('Категория добавлена.', 'success')
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('admin.index'))



@bp.route('/cats/<int:cid>/update', methods=['POST'])
@login_required
@role_required('admin')
def cat_update(cid):
    db = get_db()
    name = request.form.get('name', '').strip()
    cap = request.form.get('capacity', 1, type=int)
    rate = request.form.get('base_rate', 0, type=float)
    desc = request.form.get('description', '').strip()
    if not name or cap < 1 or rate <= 0:
        flash('Проверьте название, вместимость и ставку категории.', 'danger')
    else:
        try:
            db.execute('UPDATE room_categories SET name=%s, capacity=%s, base_rate=%s, description=%s WHERE id=%s',
                       (name, cap, rate, desc, cid))
            audit('category_update', 'room_category', cid, name)
            flash('Категория обновлена.', 'success')
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('admin.index'))


@bp.route('/rooms/new', methods=['POST'])
@login_required
@role_required('admin')
def room_new():
    db     = get_db()
    number = request.form.get('number', '').strip()
    cat_id = request.form.get('category_id', type=int)
    floor  = request.form.get('floor', 1, type=int)
    notes  = request.form.get('notes', '').strip()
    if not number or not cat_id:
        flash('Укажите номер и категорию.', 'danger')
    else:
        try:
            db.execute("INSERT INTO rooms(number,category_id,floor,notes) VALUES(%s,%s,%s,%s)",
                       (number, cat_id, floor, notes))
            flash(f'Номер {number} добавлен.', 'success')
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('admin.index'))



@bp.route('/rooms/<int:rid>/update', methods=['POST'])
@login_required
@role_required('admin')
def room_update(rid):
    db = get_db()
    number = request.form.get('number', '').strip()
    cat_id = request.form.get('category_id', type=int)
    floor = request.form.get('floor', 1, type=int)
    status = request.form.get('status', 'free')
    notes = request.form.get('notes', '').strip()
    if status not in STATUS_ROOM_LABELS:
        flash('Недопустимый статус номера.', 'danger')
    elif not number or not cat_id:
        flash('Укажите номер и категорию.', 'danger')
    else:
        try:
            db.execute('UPDATE rooms SET number=%s, category_id=%s, floor=%s, status=%s, notes=%s WHERE id=%s',
                       (number, cat_id, floor, status, notes, rid))
            audit('room_update', 'room', rid, f'№{number}, {status}')
            flash('Номер обновлён.', 'success')
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('admin.index'))


@bp.route('/services/new', methods=['POST'])
@login_required
@role_required('admin')
def service_new():
    db = get_db()
    name = request.form.get('name', '').strip()
    unit = request.form.get('unit', 'шт.').strip() or 'шт.'
    price = request.form.get('price', 0, type=float)
    is_active = 1 if request.form.get('is_active') == 'on' else 0
    if not name:
        flash('Укажите название услуги.', 'danger')
    elif price < 0:
        flash('Цена услуги не может быть отрицательной.', 'danger')
    else:
        try:
            db.execute(
                "INSERT INTO services(name, unit, price, is_active) VALUES(%s,%s,%s,%s)",
                (name, unit, price, is_active)
            )
            audit('service_create', 'service', None, f'{name}, {price} руб.')
            flash('Услуга добавлена.', 'success')
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('admin.index'))


@bp.route('/services/<int:sid>/update', methods=['POST'])
@login_required
@role_required('admin')
def service_update(sid):
    db = get_db()
    name = request.form.get('name', '').strip()
    unit = request.form.get('unit', 'шт.').strip() or 'шт.'
    price = request.form.get('price', 0, type=float)
    is_active = 1 if request.form.get('is_active') == 'on' else 0
    if not name:
        flash('Укажите название услуги.', 'danger')
    elif price < 0:
        flash('Цена услуги не может быть отрицательной.', 'danger')
    else:
        try:
            db.execute(
                "UPDATE services SET name=%s, unit=%s, price=%s, is_active=%s WHERE id=%s",
                (name, unit, price, is_active, sid)
            )
            audit('service_update', 'service', sid, f'{name}, {price} руб.')
            flash('Услуга обновлена.', 'success')
        except Exception as e:
            flash(f'Ошибка: {e}', 'danger')
    return redirect(url_for('admin.index'))


@bp.route('/services/<int:sid>/toggle', methods=['POST'])
@login_required
@role_required('admin')
def service_toggle(sid):
    db = get_db()
    svc = db.execute('SELECT is_active, name FROM services WHERE id=%s', (sid,)).fetchone()
    if svc:
        new_status = 0 if svc['is_active'] else 1
        db.execute('UPDATE services SET is_active=%s WHERE id=%s', (new_status, sid))
        audit('service_toggle', 'service', sid, f'{svc["name"]}: {new_status}')
        flash('Статус услуги изменён.', 'success')
    return redirect(url_for('admin.index'))


@bp.route('/services/<int:sid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def service_delete(sid):
    db = get_db()
    orders_count = db.execute('SELECT COUNT(*) FROM service_orders WHERE service_id=%s', (sid,)).fetchone()[0]
    svc = db.execute('SELECT name FROM services WHERE id=%s', (sid,)).fetchone()
    if not svc:
        flash('Услуга не найдена.', 'danger')
    elif orders_count:
        db.execute('UPDATE services SET is_active=0 WHERE id=%s', (sid,))
        flash('Услуга уже использовалась в заказах, поэтому она отключена, а не удалена.', 'warning')
    else:
        db.execute('DELETE FROM services WHERE id=%s', (sid,))
        audit('service_delete', 'service', sid, svc['name'])
        flash('Услуга удалена.', 'success')
    return redirect(url_for('admin.index'))


@bp.route('/audit')
@login_required
@role_required('admin')
def audit_log():
    db = get_db()
    rows = db.execute(
        '''SELECT al.*, u.username FROM audit_log al
           LEFT JOIN users u ON u.id=al.user_id
           ORDER BY al.created_at DESC LIMIT 200'''
    ).fetchall()
    return render_template('admin/audit.html', rows=rows)
