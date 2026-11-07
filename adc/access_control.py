"""Access Control (AC).

Secret: how credentials are checked, how the authorized-user list is held, the role table (D1 Table 3) and the
sign-in policy (lockout and session timeout). Every request from a user passes through submit, so no other
module checks a role or sees a credential.
"""
import hashlib
import math

from .common import ACTIONS, NURSE, PHARMACIST, TECHNICIAN, Result, Session

# D1 Table 3: which role may ask for which action
PERMISSIONS = {
    NURSE: {"dispense", "confirm", "cancel"},
    PHARMACIST: {"replenish", "count", "view_log"},
    TECHNICIAN: {"replenish", "count", "view_log", "maintenance_start", "maintenance_end", "resolve_fault"},
}



def credential_hash(credential: str) -> str:
    """The authorized-user list holds a hash of each credential, never the credential itself (D1 Figure 10)."""
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
        """mf answers whether the mode permits an action. handlers maps each action to the operation that does it."""
        self._mf, self._handlers = mf, handlers

    def session(self):
        return self._session

    def present_credentials(self, credential: str) -> Result:
        """Implements: FR-AUT-01 (credentials are checked before any protected action), FR-AUT-02 (three rejections in
        a row lock credential entry for 60 s), FR-LOG-01 (every attempt is logged)."""
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
        """Implements: FR-AUT-03 (an action is permitted only when the role of the user is authorized for it in
        Table 3), FR-DSP-03 (a dispensing request only from a Nurse with an active session), FR-MOD-11 (an action the
        mode does not permit is rejected), FR-MOD-12 (the reason is returned for display), FR-LOG-07 (each rejection
        is logged), FR-LOG-02 (a rejected dispensing request is logged as a dispensing entry), FR-MOD-09 and FR-MOD-10
        (through the mode table in Mode and Fault Manager)."""
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
        if action == "dispense":                   # FR-LOG-02: a rejected dispensing request is a dispensing entry
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
        """Implements: FR-AUT-04 (a session ends after 60 s without user input)."""
        if self._session and self._hw.now() - self._session.last_input >= self.SESSION_TIMEOUT:
            self._session = None

    def apply_user_list_update(self, users: list) -> Result:
        """Implements: FR-COM-09 (an authorized-user-list update is accepted), FR-COM-10 (it is applied at once, so
        before the next credential presentation)."""
        self._db.replace_users(users)
        if self._session and not any(u["user_id"] == self._session.user_id for u in users):
            self._session = None                       # a removed user cannot keep a session
        return Result(True)
