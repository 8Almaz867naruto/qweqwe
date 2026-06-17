import os
from flask import Flask
from flask_login import LoginManager
from .database import get_db, close_db, init_db
from .models import User

login_manager = LoginManager()


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'hotel-secret-change-me-2026')
    os.makedirs(app.instance_path, exist_ok=True)

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Войдите, чтобы получить доступ.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        row = get_db().execute(
            'SELECT * FROM users WHERE id = %s', (user_id,)
        ).fetchone()
        return User(row) if row else None

    app.teardown_appcontext(close_db)

    from .blueprints.auth      import bp as auth_bp
    from .blueprints.rooms     import bp as rooms_bp
    from .blueprints.bookings  import bp as bookings_bp
    from .blueprints.checkins  import bp as checkins_bp
    from .blueprints.guests    import bp as guests_bp
    from .blueprints.services  import bp as services_bp
    from .blueprints.payments  import bp as payments_bp
    from .blueprints.reports   import bp as reports_bp
    from .blueprints.admin     import bp as admin_bp

    for bp in [auth_bp, rooms_bp, bookings_bp, checkins_bp,
               guests_bp, services_bp, payments_bp, reports_bp, admin_bp]:
        app.register_blueprint(bp)

    with app.app_context():
        init_db()

    return app
