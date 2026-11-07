"""Mode and Fault Manager (MF).

Only this module changes the mode or decides something is a fault.
"""
from .common import ACTIONS, FAULT, MAINTENANCE, NORMAL, SELF_TEST, UNPOWERED, Result

# FR-MOD-09, FR-MOD-10, FR-MOD-11: the modes each request is allowed in
PERMITTED_IN = {"dispense": {NORMAL}, "confirm": {NORMAL}, "cancel": {NORMAL},
                "replenish": {NORMAL, MAINTENANCE}, "count": {NORMAL, MAINTENANCE},
                "view_log": {NORMAL, MAINTENANCE, FAULT},
                "maintenance_start": {NORMAL}, "maintenance_end": {MAINTENANCE}, "resolve_fault": {FAULT}}


class ModeFaultManager:
    CLOSE_LIMIT, LOCK_LIMIT, SELF_TEST_TIME = 30, 2, 2

    def __init__(self, db, hw) -> None:
        self._db, self._hw = db, hw
        self._mode = UNPOWERED
        self._faults = []
        self._test_ends = 0.0

    def connect(self, di, cg) -> None:
        self._di, self._cg = di, cg

    # ---- questions other modules ask ----
    def mode(self) -> str:
        """Implements: FR-MOD-02.
        One variable holds the mode, so the ADC is only ever in one mode."""
        return self._mode

    def faults(self) -> list:
        return list(self._faults)

    def health(self) -> str:
        return "FAULT" if self._faults else "OK"

    def permits(self, action: str) -> Result:
        if self._mode in PERMITTED_IN[action]:
            return Result(True)
        return Result(False, f"The cabinet is in {self._mode} mode, which does not permit this request ({ACTIONS[action]}).")

    # ---- modes ----
    def _enter(self, new: str) -> None:
        """Implements: FR-LOG-05.
        Change mode and log it."""
        old, self._mode = self._mode, new
        self._db.log("MODE", outcome=new, detail=f"{old} to {new}")
        self._di.mode_changed(new)

    def power_on(self) -> None:
        """Implements: FR-MOD-03, FR-FLT-05.
        Run the self-test on every power-up."""
        self._self_test()

    def _self_test(self) -> None:
        self._enter(SELF_TEST)                                       # locks every drawer, which is the lock test
        self._test_ends = self._hw.now() + self.SELF_TEST_TIME

    def maintenance_start(self, session, params) -> Result:
        """Implements: FR-MOD-06.
        Technician only, from Normal mode."""
        self._enter(MAINTENANCE)
        return Result(True, "Maintenance mode started.")

    def maintenance_end(self, session, params) -> Result:
        """Implements: FR-MOD-07.
        Back to Normal."""
        self._enter(NORMAL)
        return Result(True, "Maintenance mode ended.")

    def resolve_fault(self, session, params) -> Result:
        """Implements: FR-FLT-06, FR-LOG-03, FR-MOD-08.
        Faults stay until a Technician resolves them. Each one is logged, then the self-test runs."""
        for f in self._faults:
            self._db.log("FAULT", session.user_id, outcome="Resolved", detail=f"{f['kind']} drawer {f['drawer']}")
        self._faults = []
        self._self_test()
        return Result(True, "Fault resolved. Self-test started.")

    # ---- faults ----
    def raise_fault(self, kind: str, drawer: int, detail: str) -> None:
        """Implements: FR-LOG-03, FR-MOD-01, FR-FLT-02, FR-COM-01.
        Log the fault, put it on the screen right away (limit is 2 s) and alarm the Monitoring System.
        From Normal or Maintenance, go to Fault mode."""
        if any(f["kind"] == kind and f["drawer"] == drawer for f in self._faults):
            return                                                   # already raised
        fault = {"id": self._db.next_id("fault"), "kind": kind, "drawer": drawer, "detail": detail,
                 "raised_at": self._hw.now()}
        self._faults.append(fault)
        self._db.log("FAULT", outcome="Raised", detail=f"{kind} drawer {drawer}: {detail}")
        self._cg.alarm(fault)
        if self._mode in (NORMAL, MAINTENANCE):
            self._enter(FAULT)

    def on_event(self, event) -> None:
        """Implements: FR-FLT-04.
        A drawer that opens without an unlock command is a tamper fault."""
        kind, d = event
        if kind == "open" and self._hw.commanded(d) == "locked":
            self.raise_fault("TAMPER", d, "Drawer opened without an unlock command.")

    def tick(self) -> None:
        """Implements: FR-FLT-01, FR-FLT-03, FR-MOD-04, FR-MOD-05.
        Check the limits: a dispense drawer must close within 30 s, a lock must report back within 2 s.
        When the self-test ends: pass -> Normal, fail -> Fault."""
        hw, now = self._hw, self._hw.now()
        for d in hw.drawers:
            if hw.bolt(d) != hw.commanded(d) and now - hw.commanded_at(d) >= self.LOCK_LIMIT:
                self.raise_fault("LOCK", d, "The lock did not report the commanded state within 2 s.")
            if hw.is_open(d) and hw.commanded(d) == "unlocked" and now - hw.opened_at(d) >= self.CLOSE_LIMIT:
                self.raise_fault("DRAWER", d, "The drawer was not closed within 30 s of opening.")
        if self._mode == SELF_TEST and now >= self._test_ends:
            for d in hw.drawers:
                if hw.is_open(d):
                    self.raise_fault("DRAWER", d, "The drawer was open during the self-test.")
            self._enter(FAULT if self._faults else NORMAL)
