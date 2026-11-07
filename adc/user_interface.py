"""User Interface (UI).

UI sits on top: it turns a badge or a touch into a request for AC and draws the screen from what the other
modules report. No other module calls UI.
"""
from .common import FAULT, NORMAL, MAINTENANCE, Result

# NFR-USE-03: colours picked for contrast (a test checks the ratios)
PALETTE = {"text": "#111111", "background": "#FFFFFF", "alarm_text": "#FFFFFF", "alarm_background": "#8B0000",
           "link_text": "#003A8C", "banner_background": "#F2F2F2"}
SYMBOL = {"ok": "[OK]", "warn": "[!]", "lost": "[X]"}          # NFR-USE-02: every status has a symbol and a word
FAULT_NAMES = {"DRAWER": "Drawer fault", "LOCK": "Lock fault", "TAMPER": "Tamper fault"}


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


class UserInterface:
    def __init__(self, devices) -> None:
        self._dev = devices                          # .buzzer(on): simulated or real buzzer
        self._notice = self._rejection = ""
        self._log_view = None
        self._had_session = False
        self._seen_last = None

    def connect(self, ac, mf, di, cg) -> None:
        self._ac, self._mf, self._di, self._cg = ac, mf, di, cg

    # ---- input ----
    def on_credential(self, data: str) -> None:
        """Implements: FR-AUT-01.
        The badge goes to AC before anything else."""
        self._notice = self._rejection = ""
        result = self._ac.present_credentials(data)
        if not result.ok:
            self._rejection = result.reason

    def on_touch(self, name: str, params=None) -> Result:
        """Implements: FR-DSP-04, FR-DSP-06, FR-MOD-12.
        Pass the touch (with patient ID, medication and quantity) to AC. Show the reason if it's rejected."""
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
        """Implements: NFR-USE-01, FR-AUT-05.
        Alarms are sounded as well as shown. Patient info is cleared when the session ends."""
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
        """Implements: FR-MOD-13, FR-COM-08, FR-FLT-02, FR-COM-07, FR-DSP-07, FR-AUT-05, NFR-USE-02, NFR-USE-03.
        Everything on screen: mode, link, fault alarms, queue alarm, the request to confirm, inventory and log.
        Patient info only shows while signed in. Every status has a word and a symbol."""
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
