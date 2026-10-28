"""One-time local-owner sign-in links; no permanent credentials reach the browser."""

from hashlib import sha256
import secrets

from app.config import Settings
from app.database import connect


def token_hash(token):
    return sha256(token.encode()).hexdigest()


class DashboardSessions:
    def __init__(self, connection_factory=None):
        self.connect = connection_factory or (lambda: connect(Settings()))

    def issue(self):
        ticket = "login_" + secrets.token_urlsafe(32)
        with self.connect() as conn:
            conn.execute("""DELETE FROM soloestate.dashboard_sessions WHERE token_hash IN (
                SELECT token_hash FROM soloestate.dashboard_sessions WHERE expires_at<clock_timestamp()
                ORDER BY expires_at LIMIT 100 FOR UPDATE SKIP LOCKED)""")
            row = conn.execute("""INSERT INTO soloestate.dashboard_sessions (token_hash,kind,expires_at)
                VALUES (%s,'ticket',clock_timestamp()+interval '2 minutes') RETURNING expires_at""", (token_hash(ticket),)).fetchone()
        return {"ticket": ticket, "expires_at": row[0].isoformat()}

    def exchange(self, ticket):
        if not isinstance(ticket, str) or not ticket.startswith("login_") or len(ticket)>100:
            raise PermissionError("Sign-in link is invalid or expired.")
        session = "sess_" + secrets.token_urlsafe(32)
        with self.connect() as conn:
            row = conn.execute("""UPDATE soloestate.dashboard_sessions SET consumed_at=clock_timestamp()
                WHERE token_hash=%s AND kind='ticket' AND consumed_at IS NULL AND expires_at>clock_timestamp()
                RETURNING token_hash""", (token_hash(ticket),)).fetchone()
            if row is None:
                raise PermissionError("Sign-in link is invalid or expired.")
            expiry = conn.execute("""INSERT INTO soloestate.dashboard_sessions (token_hash,kind,expires_at)
                VALUES (%s,'session',clock_timestamp()+interval '8 hours') RETURNING expires_at""", (token_hash(session),)).fetchone()[0]
        return {"session_token":session,"expires_at":expiry.isoformat()}

    def verify(self, session):
        if not isinstance(session,str) or not session.startswith("sess_") or len(session)>100:
            raise PermissionError("Owner session is invalid or expired.")
        with self.connect() as conn:
            row=conn.execute("""SELECT expires_at FROM soloestate.dashboard_sessions WHERE token_hash=%s
                AND kind='session' AND revoked_at IS NULL AND expires_at>clock_timestamp()""",(token_hash(session),)).fetchone()
        if row is None:
            raise PermissionError("Owner session is invalid or expired.")
        return {"authenticated":True,"expires_at":row[0].isoformat()}

    def revoke(self, session):
        with self.connect() as conn:
            conn.execute("UPDATE soloestate.dashboard_sessions SET revoked_at=clock_timestamp() WHERE token_hash=%s AND kind='session'",(token_hash(session),))
