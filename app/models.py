from flask_login import UserMixin


class User(UserMixin):
    def __init__(self, row):
        self.id        = row['id']
        self.username  = row['username']
        self.full_name = row['full_name']
        self.role      = row['role']

    def get_id(self):
        return str(self.id)
