from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash
from ..database import get_db
from ..models import User

bp = Blueprint('auth', __name__, url_prefix='/auth')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        row = db.execute('SELECT * FROM users WHERE username = %s', (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            user = User(row)
            login_user(user, remember=True)
            return redirect(url_for('rooms.chart'))
        flash('Неверный логин или пароль.', 'danger')
    return render_template('auth/login.html')


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@bp.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    from werkzeug.security import generate_password_hash
    from flask_login import current_user
    if request.method == 'POST':
        old = request.form.get('old_password', '')
        new = request.form.get('new_password', '')
        db = get_db()
        row = db.execute('SELECT password_hash FROM users WHERE id=%s', (current_user.id,)).fetchone()
        if not check_password_hash(row['password_hash'], old):
            flash('Старый пароль указан неверно.', 'danger')
        elif len(new) < 6:
            flash('Новый пароль должен быть не менее 6 символов.', 'danger')
        else:
            db.execute('UPDATE users SET password_hash=%s WHERE id=%s',
                       (generate_password_hash(new), current_user.id))
            flash('Пароль успешно изменён.', 'success')
            return redirect(url_for('rooms.chart'))
    return render_template('auth/change_password.html')
