"""Dispensing and Inventory (DI).

Secret: the steps of a dispensing transaction, the relock timing, and how inventory records are kept and changed.
It is the only module that sends an unlock command, and the only one that changes a quantity on hand.
"""
from .common import (CANCELLED, COMPLETED, FAULT, FAULTED, INTERRUPTED, NORMAL, OPEN, REJECTED, REQUESTED, SELF_TEST,
                     TIMED_OUT, UNLOCKED, Result)

SEED = [  # medication, drawer, quantity, expiry, low-stock threshold
    ("Acetaminophen 500 mg", 1, 20, "2027-08-31", 5), ("Ibuprofen 200 mg", 2, 12, "2027-05-31", 4),
    ("Morphine 10 mg", 3, 8, "2027-03-31", 3), ("Amoxicillin 500 mg", 4, 6, "2027-01-31", 3)]


class DispensingInventory:
    RELOCK_AFTER = 15

    def __init__(self, db, hw) -> None:
        self._db, self._hw = db, hw
        self._txn = None                    # the one transaction in progress, or None
        self._last = None                   # how the latest transaction ended, for the screen
        if not db.inventory():
            for m, d, q, e, t in SEED:
                db.save_inventory({"medication": m, "drawer": d, "quantity": q, "expiry": e, "threshold": t,
                                   "discrepant": False})

    def connect(self, ac, mf, cg) -> None:
        self._ac, self._mf, self._cg = ac, mf, cg

    # ---- inventory ----
    def records(self) -> list:
        """Implements: FR-INV-04 (low stock if and only if the quantity on hand is at or below the threshold)."""
        return [dict(r, low_stock=r["quantity"] <= r["threshold"]) for r in self._db.inventory()]

    def _record(self, medication):
        return next((r for r in self.records() if r["medication"] == medication), None)

    def _save(self, rec: dict, user_id: str, outcome: str, detail: str) -> None:
        """Implements: FR-LOG-04 (each change to a record and each count is logged), FR-COM-03 (each change goes to the
        Monitoring System)."""
        self._db.save_inventory(rec)
        rec["low_stock"] = rec["quantity"] <= rec["threshold"]
        self._db.log("INVENTORY", user_id, medication=rec["medication"], quantity=rec["quantity"], outcome=outcome,
                     detail=detail)
        self._cg.inventory(rec)

    def replenish(self, session, params) -> Result:
        """Implements: FR-INV-03 (the quantity on hand rises by the replenished quantity)."""
        rec, qty = self._record(params.get("medication")), params.get("quantity")
        if rec is None or not isinstance(qty, int) or qty <= 0:
            return Result(False, "Choose a medication and a quantity above zero.")
        rec["quantity"] += qty
        self._save(rec, session.user_id, "Replenished", f"added {qty}")
        return Result(True, f"{rec['medication']}: {rec['quantity']} on hand.")

    def count(self, session, params) -> Result:
        """Implements: FR-INV-05 (a counted quantity that differs from the quantity on hand marks the record as
        discrepant; a matching count clears the mark)."""
        rec, qty = self._record(params.get("medication")), params.get("quantity")
        if rec is None or not isinstance(qty, int) or qty < 0:
            return Result(False, "Choose a medication and a counted quantity.")
        rec["discrepant"] = qty != rec["quantity"]
        self._save(rec, session.user_id, "Counted", f"counted {qty}, on hand {rec['quantity']}")
        return Result(True, f"Counted {qty}, on hand {rec['quantity']}. " +
                      ("The record is marked discrepant." if rec["discrepant"] else "The count matches."))

    # ---- dispensing ----
    def request(self, session, params) -> Result:
        """Implements: FR-DSP-04 (a patient identifier, a medication and a quantity are required), FR-DSP-05 (a quantity
        above the quantity on hand is rejected), FR-DSP-06 (the reason is returned for display), FR-DSP-07 (the request
        is held for the nurse to confirm on the screen), FR-LOG-02 (a rejected request is logged)."""
        patient, med, qty = str(params.get("patient_id", "")).strip(), params.get("medication"), params.get("quantity")
        rec = self._record(med)
        if self._txn:
            reason = "Another request is already in progress."
        elif not patient or rec is None or not isinstance(qty, int) or qty <= 0:
            reason = "A patient identifier, a medication and a quantity above zero are required."
        elif qty > rec["quantity"]:
            reason = f"Only {rec['quantity']} of {med} on hand."
        else:
            self._txn = {"id": self._db.next_id("txn"), "user": session.user_id, "patient": patient, "medication": med,
                         "quantity": qty, "drawer": rec["drawer"], "state": REQUESTED, "unlocked_at": 0.0}
            return Result(True, "Check the request and confirm.")
        self._db.log("DISPENSING", session.user_id, patient, med or "", qty if isinstance(qty, int) else None,
                     REJECTED, reason)
        return Result(False, reason)

    def confirm(self, session, params) -> Result:
        """Implements: FR-DSP-01 (only the drawer holding the requested medication is unlocked, and only after the nurse
        confirms), NFR-SAF-01 (the mode is checked again immediately before the unlock command), NFR-PRF-01 (the unlock
        command is sent in the same step as the confirmation), NFR-SAF-02 (this is the only operation that sends an
        unlock command, so a drawer stays locked unless it ran for that drawer)."""
        t = self._txn
        if t is None or t["state"] != REQUESTED:
            return Result(False, "There is no request waiting for confirmation.")
        if self._mf.mode() != NORMAL:
            self._finish(CANCELLED, "mode is no longer Normal")
            return Result(False, "The request was cancelled because the ADC is no longer in Normal mode.")
        self._hw.unlock(t["drawer"])
        t["state"], t["unlocked_at"] = UNLOCKED, self._hw.now()
        self._db.save_txn(t["id"], t)
        return Result(True, f"Drawer {t['drawer']} is unlocked. Take {t['quantity']} x {t['medication']}.")

    def cancel(self, session, params) -> Result:
        """Implements: FR-DSP-10 (a request can be cancelled until the nurse confirms it)."""
        if self._txn is None:
            return Result(False, "There is no request to cancel.")
        if self._txn["state"] != REQUESTED:
            return Result(False, "The drawer is already unlocked, so the request cannot be cancelled.")
        self._finish(CANCELLED, "cancelled by the nurse")
        return Result(True, "Request cancelled.")

    def on_event(self, event) -> None:
        """Implements: FR-DSP-08 (a drawer reported closed is locked), FR-DSP-02 (a transaction is completed only after
        the drawer sensor reports the drawer closed), FR-INV-01 (the dispensed quantity is taken from the quantity on
        hand when the transaction completes)."""
        kind, d = event
        t = self._txn
        if kind == "open" and t and t["state"] == UNLOCKED and t["drawer"] == d:
            t["state"] = OPEN
            self._db.save_txn(t["id"], t)
        elif kind == "closed":
            self._hw.lock(d)
            if t and t["state"] == OPEN and t["drawer"] == d:
                rec = self._record(t["medication"])
                rec["quantity"] -= t["quantity"]
                self._save(rec, t["user"], "Dispensed", f"{t['quantity']} for patient {t['patient']}")
                self._finish(COMPLETED, "drawer closed")

    def tick(self) -> None:
        """Implements: FR-DSP-09 (a drawer not opened within 15 s of unlocking is locked again), D-11 (a request is
        cancelled when the session ends)."""
        t = self._txn
        if t and t["state"] == UNLOCKED and self._hw.now() - t["unlocked_at"] >= self.RELOCK_AFTER:
            self._hw.lock(t["drawer"])
            self._finish(TIMED_OUT, "the drawer was not opened within 15 s")
        elif t and t["state"] == REQUESTED and self._ac.session() is None:
            self._finish(CANCELLED, "the session ended")

    def mode_changed(self, mode: str) -> None:
        """Implements: NFR-SAF-01 (every drawer is locked on entering Self-Test or Fault mode)."""
        t = self._txn
        if mode in (SELF_TEST, FAULT):
            for d in self._hw.drawers:
                self._hw.lock(d)
            if t:
                self._finish(CANCELLED if t["state"] == REQUESTED else FAULTED, f"the ADC entered {mode} mode")
        elif t and t["state"] == REQUESTED:
            self._finish(CANCELLED, f"the ADC entered {mode} mode")

    def recover(self) -> None:
        """Implements: FR-DSP-11 (a transaction in progress at a power loss is recorded as interrupted)."""
        for t in self._db.open_txns():
            self._db.log("DISPENSING", t["user"], t["patient"], t["medication"], t["quantity"], INTERRUPTED,
                         f"power lost while {t['state']}; quantity on hand unchanged")
            self._db.close_txn(t["id"])

    def pending(self):
        """The request waiting for the nurse to confirm, for the screen."""
        t = self._txn
        if t and t["state"] == REQUESTED:
            return {k: t[k] for k in ("patient", "medication", "quantity", "drawer")}

    def last(self):
        """How the latest transaction ended, for the screen."""
        return self._last

    def _finish(self, outcome: str, detail: str) -> None:
        """Implements: FR-LOG-02 (each request is logged with its outcome), NFR-AUD-01 (the entry has user, patient
        identifier, medication, quantity, time and outcome)."""
        t, self._txn = self._txn, None
        self._db.close_txn(t["id"])
        self._db.log("DISPENSING", t["user"], t["patient"], t["medication"], t["quantity"], outcome, detail)
        self._last = {"outcome": outcome, "patient": t["patient"], "medication": t["medication"],
                      "quantity": t["quantity"], "detail": detail}
