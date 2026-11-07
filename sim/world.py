"""The simulated world around one cabinet: clock, drawers, devices, link and Monitoring System."""
from adc.cabinet import Cabinet
from .hardware import SimClock, SimDrawerHardware, SimLocalDevices
from .monitoring_stub import MonitoringStub, SimLink


class World:
    def __init__(self, db_path: str = ":memory:", queue_capacity: int = 50) -> None:
        self.clock = SimClock()
        self.drawers = SimDrawerHardware(self.clock)
        self.devices = SimLocalDevices()
        self.link = SimLink()
        self.monitor = MonitoringStub(self.clock, self.link)
        self.cabinet = Cabinet(self, db_path, queue_capacity)

    def advance(self, seconds: float) -> None:
        """Move time forward in 0.1 s steps: drawers move first, then the cabinet checks its timers."""
        for _ in range(int(round(seconds * 10))):
            self.clock.step()
            self.drawers.step()
            self.cabinet.tick()
