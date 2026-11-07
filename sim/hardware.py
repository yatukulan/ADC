"""Simulated devices around the ADC: the drawers, badge reader, touchscreen and buzzer.

Not part of the ADC software. They stand in for the real cabinet so the ADC can be run and demoed.
"""
from datetime import datetime, timedelta
from typing import Callable, List, Optional


class SimClock:
    """Virtual time in seconds. The ADC reads it through the Hardware Interface."""

    def __init__(self) -> None:
        self.ticks = 0                                  # tenths of a second, so the maths stays exact

    def now(self) -> float:
        return self.ticks / 10

    def step(self) -> None:
        self.ticks += 1

    def iso(self, t: float) -> str:
        return (datetime(2026, 11, 5, 8, 0, 0) + timedelta(seconds=t)).isoformat(sep=" ", timespec="seconds")


class SimDrawerHardware:
    """Four drawers, each with a lock bolt and an open/closed sensor. A bolt moves 0.3 s after a command.
    Faults can be injected: a stuck lock or a drawer forced open."""
    BOLT_DELAY = 0.3

    def __init__(self, clock, ids=(1, 2, 3, 4)) -> None:
        self._clock = clock
        self._moves = []                       # (due time, function) for bolt moves in progress
        self._ids = list(ids)
        self._bolt = {d: "IN" for d in ids}            # IN = locked
        self._open = {d: False for d in ids}
        self._stuck = {d: False for d in ids}
        self._listener: Optional[Callable[[int, str], None]] = None

    # --- used by the Hardware Interface module ---
    def attach(self, listener) -> None:
        self._listener = listener

    def time(self) -> float:
        return self._clock.now()

    def iso(self, t: float) -> str:
        return self._clock.iso(t)

    def step(self) -> None:
        """Finish the bolt moves that are due."""
        due = [m for m in self._moves if m[0] <= self._clock.now() + 1e-9]
        self._moves = [m for m in self._moves if m not in due]
        for _, move in due:
            move()

    def drawer_ids(self) -> List[int]:
        return list(self._ids)

    def send(self, drawer: int, command: str) -> None:
        if self._stuck[drawer]:
            return
        target = "IN" if command == "BOLT_IN" else "OUT"

        def move() -> None:
            self._bolt[drawer] = target
            self._emit(drawer, "BOLT_IN_OK" if target == "IN" else "BOLT_OUT_OK")
        self._moves.append((self._clock.now() + self.BOLT_DELAY, move))

    def read_bolt(self, drawer: int) -> str:
        return self._bolt[drawer]

    def read_sensor(self, drawer: int) -> str:
        return "OPEN" if self._open[drawer] else "CLOSED"

    def _emit(self, drawer: int, code: str) -> None:
        if self._listener:
            self._listener(drawer, code)

    # --- what a person or the environment does ---
    def pull_drawer(self, drawer: int) -> bool:
        """Someone pulls a drawer. It only opens if the bolt is out."""
        if self._open[drawer] or self._bolt[drawer] == "IN":
            return False
        self._open[drawer] = True
        self._emit(drawer, "SENSOR_OPEN")
        return True

    def push_drawer(self, drawer: int) -> bool:
        if not self._open[drawer]:
            return False
        self._open[drawer] = False
        self._emit(drawer, "SENSOR_CLOSED")
        return True

    def force_drawer(self, drawer: int) -> None:
        """Tampering: forced open even though the bolt is in."""
        if not self._open[drawer]:
            self._open[drawer] = True
            self._emit(drawer, "SENSOR_OPEN")

    def set_stuck(self, drawer: int, stuck: bool) -> None:
        self._stuck[drawer] = stuck

    def is_stuck(self, drawer: int) -> bool:
        return self._stuck[drawer]

    def power_cut(self) -> None:
        """Locks fail secure: every bolt goes in, and no signal is sent."""
        self._listener = None
        for d in self._ids:
            self._bolt[d] = "IN"
        self._moves = []


class SimLocalDevices:
    """Badge reader, touchscreen and buzzer. The screen shows whatever UI says.
    The buzzer is just on/off; the browser panel turns it into sound and a banner."""

    def __init__(self) -> None:
        self.buzzer_on = False

    def buzzer(self, on: bool) -> None:
        self.buzzer_on = on
