"""User Interface (UI).

Secret: the local devices (credential reader, touchscreen, alarm buzzer), the screen layout and the wording of
what is shown. UI sits on top: it turns a badge swipe or a touch into a request for Access Control, and it draws
the screen from what the other modules report. No other module calls UI.
"""
from .common import FAULT, NORMAL, MAINTENANCE, Result

# Colours chosen for NFR-USE-03 (the ratios are checked by a test)
PALETTE = {"text": "#111111", "background": "#FFFFFF", "alarm_text": "#FFFFFF", "alarm_background": "#8B0000",
           "link_text": "#003A8C", "banner_background": "#F2F2F2"}
SYMBOL = {"ok": "[OK]", "warn": "[!]", "lost": "[X]"}          # NFR-USE-02: a symbol and a word go with every status
FAULT_NAMES = {"DRAWER": "Drawer fault", "LOCK": "Lock fault", "TAMPER": "Tamper fault"}


def contrast_ratio(fg: str, bg: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


class UserInterface:
    def __init__(self, devices) -> None:
        self._dev = devices                          # .buzzer(on): the simulated or the real alarm buzzer
        self._notice = self._rejection = ""
        self._log_view = None
        self._had_session = False
        self._seen_last = None

    def connect(self, ac, mf, di, cg) -> None:
        self._ac, self._mf, self._di, self._cg = ac, mf, di, cg

    # ---- input ----
    def on_credential(self, data: str) -> None:
        """Implements: FR-AUT-01 (the credential read at the cabinet goes to Access Control before anything else)."""
        self._notice = self._rejection = ""
        result = self._ac.present_credentials(data)
        if not result.ok:
            self._rejection = result.reason

    def on_touch(self, name: str, params=None) -> Result:
        """Implements: FR-DSP-04 (the patient identifier, medication and quantity entered are passed on with the
        request), FR-DSP-06 (the reason for a rejected request is displayed), FR-MOD-12 (the reason for a request the
        mode does not permit is displayed)."""
        self._notice = self._rejection = ""
        self._ac.touch()
        if name == "sign_out":
            self._ac.sign_out()
            return Result(True)
        result = self._ac.submit(name, params or {})
        if not result.ok:
            self._rejection = result.reason
        elif name == "view_log":
            self._log_view = result.data
        else:
            self._notice = result.reason
        return result

    # ---- output ----
    def tick(self) -> None:
        """Implements: NFR-USE-01 (every alarm is sounded as well as shown), FR-AUT-05 (nothing about a patient stays
        on the screen after the session ends)."""
        session = self._ac.session()
        if self._had_session and session is None:
            self._notice, self._log_view = "", None
        self._had_session = session is not None
        last = self._di.last()
        if last is not self._seen_last:
            self._seen_last = last
            who = f" for patient {last['patient']}" if session else ""
            self._notice = (f"Dispensing {last['outcome'].lower()}: {last['quantity']} x {last['medication']}{who}. "
                            f"({last['detail']})")
        self._dev.buzzer(bool(self._mf.faults()) or self._cg.queue_nearly_full())

    def screen(self) -> dict:
        """Implements: FR-MOD-13 (the current mode is displayed), FR-COM-08 (the communication status is displayed),
        FR-FLT-02 (a fault alarm is displayed as soon as the fault is raised), FR-COM-07 (a local alarm is displayed
        when the queue is 90% full), FR-DSP-07 (the patient identifier, medication and quantity are displayed for the
        nurse to confirm), FR-AUT-05 (a patient identifier is displayed only while a session is active), NFR-USE-02
        (each status carries a word and a symbol), NFR-USE-03 (the palette meets the contrast ratio)."""
        s, mode, up = self._ac.session(), self._mf.mode(), self._cg.is_connected()
        pending = self._di.pending()
        return {
            "mode": {"label": "Mode", "value": mode,
                     "symbol": SYMBOL["ok"] if mode in (NORMAL, MAINTENANCE) else SYMBOL["warn"]},
            "link": {"label": "Monitoring System link", "value": "Connected" if up else "Lost",
                     "symbol": SYMBOL["ok"] if up else SYMBOL["lost"]},
            "session": {"user": s.user_id, "role": s.role} if s else None,
            "alarms": [{"text": f"{FAULT_NAMES[f['kind']]}: drawer {f['drawer']}. {f['detail']}",
                        "symbol": SYMBOL["warn"], "when": f["raised_at"]} for f in self._mf.faults()],
            "local_alarm": {"text": "The transmission queue is almost full.", "symbol": SYMBOL["warn"]}
                           if self._cg.queue_nearly_full() else None,
            "notice": self._notice,
            "rejection": self._rejection,
            "confirmation": {"patient_id": pending["patient"], "medication": pending["medication"],
                             "quantity": pending["quantity"], "drawer": pending["drawer"]} if pending and s else None,
            "inventory": self._di.records() if s else [],
            "log": self._log_view if s else None,
            "palette": PALETTE,
        }
