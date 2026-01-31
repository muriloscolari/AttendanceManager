from flask import abort
from flask_login import current_user

def check_collaboration_permission(party):
    if party.user_id != current_user.id and current_user not in party.collaborators:
        abort(403)
