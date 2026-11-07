"""Hardware Interface (HW).

Secret: how the drawer locks, the door sensors and the clock are driven: the command and signal codes of the
drawer driver and the way time is read. Other modules say "lock drawer 2" and ask "is drawer 2 open".
"""


class Hardware:
    def __init__(self, driver) -> None:
        self._drv = driver
        self.drawers = driver.drawer_ids()
        t = driver.time()
        self._cmd = {d: "locked" for d in self.drawers}              # last command sent
        self._cmd_at = {d: t for d in self.drawers}
        self._bolt = {d: "locked" if driver.read_bolt(d) == "IN" else "unlocked" for d in self.drawers}   # last reported
        self._open = {d: driver.read_sensor(d) == "OPEN" for d in self.drawers}
        self._opened_at = {d: t for d in self.drawers}
        self._events = []
        driver.attach(self._signal)

    # ---- clock ----
    def now(self) -> float:
        return self._drv.time()

    def iso(self, t: float) -> str:
        return self._drv.iso(t)

    # ---- commands ----
    def lock(self, d: int) -> None:
        self._command(d, "locked", "BOLT_IN")

    def unlock(self, d: int) -> None:
        self._command(d, "unlocked", "BOLT_OUT")

    def _command(self, d: int, state: str, code: str) -> None:
        self._cmd[d], self._cmd_at[d] = state, self.now()
        self._drv.send(d, code)

    # ---- state ----
    def commanded(self, d: int) -> str:
        return self._cmd[d]

    def commanded_at(self, d: int) -> float:
        return self._cmd_at[d]

    def bolt(self, d: int) -> str:
        return self._bolt[d]

    def is_open(self, d: int) -> bool:
        return self._open[d]

    def opened_at(self, d: int) -> float:
        return self._opened_at[d]

    def events(self) -> list:
        """The ("open", d) and ("closed", d) signals received since the last call."""
        out, self._events = self._events, []
        return out

    def _signal(self, d: int, code: str) -> None:
        if code in ("BOLT_IN_OK", "BOLT_OUT_OK"):
            self._bolt[d] = "locked" if code == "BOLT_IN_OK" else "unlocked"
        else:
            self._open[d] = code == "SENSOR_OPEN"
            if self._open[d]:
                self._opened_at[d] = self.now()
            self._events.append(("open" if self._open[d] else "closed", d))
