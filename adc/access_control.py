"""Access Control (AC).

Every user request goes through submit(), so no other module checks a role or sees a credential.
"""
import hashlib
import math

from .common import ACTIONS, NURSE, PHARMACIST, TECHNICIAN, Result, Session

# D1 Table 3: which role can do which action
PERMISSIONS = {
    NURSE: {"dispense", "confirm", "cancel"},
    PHARMACIST: {"replenish", "count", "view_log"},
    TECHNICIAN: {"replenish", "count", "view_log", "maintenance_start", "maintenance_end", "resolve_fault"},
}



def credential_hash(credential: str) -> str:
    """Only the hash is stored, never the credential itself (D1 Figure 10)."""
    return hashlib.sha256(credential.encode()).hexdigest()


DEFAULT_USERS = [{"credential_hash": credential_hash("NURSE-0001"), "user_id": "nurse01", "role": NURSE},
                 {"credential_hash": credential_hash("PHARM-0001"), "user_id": "pharm01", "role": PHARMACIST},
                 {"credential_hash": credential_hash("TECH-0001"), "user_id": "tech01", "role": TECHNICIAN}]


class AccessControl:
    FAILURES_BEFORE_LOCKOUT, LOCKOUT_SECONDS, SESSION_TIMEOUT = 3, 60, 60

    def __init__(self, db, hw) -> None:
        self._db, self._hw = db, hw
        self._session = None
        self._failures = 0
        self._locked_until = -1.0
        if not db.users():
            db.replace_users(DEFAULT_USERS)

    def connect(self, mf, handlers) -> None:
        """mf: checks if the mode allows an action. handlers: action -> the function that does it."""
        self._mf, self._handlers = mf, handlers

    def session(self):
        return self._session

    def present_credentials(self, credential: str) -> Result:
        """Implements: FR-AUT-01, FR-AUT-02, FR-LOG-01.
        Check the badge before anything else. 3 bad tries in a row lock entry for 60 s. Every try is logged."""
        now = self._hw.now()
        if now < self._locked_until:
            self._db.log("AUTHENTICATION", outcome="Refused", detail="credential entry is locked")
            return Result(False, f"Credential entry is locked for {math.ceil(self._locked_until - now)} s.")
        wanted = credential_hash(credential)
        user = next((u for u in self._db.users() if u["credential_hash"] == wanted), None)
        if user is None:
            self._failures += 1
            if self._failures >= self.FAILURES_BEFORE_LOCKOUT:
                self._failures, self._locked_until = 0, now + self.LOCKOUT_SECONDS
            self._db.log("AUTHENTICATION", outcome="Rejected", detail="credentials not recognised")
            return Result(False, "Credentials not recognised.")
        self._failures = 0
        self._session = Session(user["user_id"], user["role"], now)
        self._db.log("AUTHENTICATION", user["user_id"], outcome="Accepted", detail=user["role"])
        return Result(True, f"Signed in as {user['user_id']} ({user['role']}).")

    def submit(self, action: str, params: dict) -> Result:
        """Implements: FR-AUT-03, FR-DSP-03, FR-MOD-09, FR-MOD-10, FR-MOD-11, FR-MOD-12, FR-LOG-02, FR-LOG-07.
        Every request needs a session, a role allowed by Table 3 and a mode that allows it (MF's PERMITTED_IN).
        A rejection returns the reason for the screen and is logged."""
        s = self._session
        if s is None:
            reason = "Present credentials first."
        elif action not in PERMISSIONS[s.role]:
            reason = f"The {s.role} role is not authorized for this request ({ACTIONS[action]})."
        else:
            allowed = self._mf.permits(action)
            if allowed.ok:
                s.last_input = self._hw.now()
                return self._handlers[action](s, params)
            reason = allowed.reason
        who = s.user_id if s else ""
        if action == "dispense":                   # FR-LOG-02: a rejected dispense is logged as a dispensing entry
            qty = params.get("quantity")
            self._db.log("DISPENSING", who, str(params.get("patient_id", "")), params.get("medication") or "",
                         qty if isinstance(qty, int) else None, "Rejected", reason)
        else:
            self._db.log("REJECTION", who, outcome="Rejected", detail=f"{ACTIONS[action]}: {reason}")
        return Result(False, reason)

    def touch(self) -> None:
        if self._session:
            self._session.last_input = self._hw.now()

    def sign_out(self) -> None:
        self._session = None

    def tick(self) -> None:
        """Implements: FR-AUT-04.
        End the session after 60 s with no input."""
        if self._session and self._hw.now() - self._session.last_input >= self.SESSION_TIMEOUT:
            self._session = None

    def apply_user_list_update(self, users: list) -> Result:
        """Implements: FR-COM-09, FR-COM-10.
        Swap in the new user list right away, so it's used from the next badge on."""
        self._db.replace_users(users)
        if self._session and not any(u["user_id"] == self._session.user_id for u in users):
            self._session = None                       # a removed user loses their session
        return Result(True)
