"""Cabinet: builds the modules, connects them and runs one step of time. It holds no decisions, so it is not a
module. Power loss is modelled by dropping every module object and keeping only the database file, which is
what a real cabinet keeps."""
from .access_control import AccessControl
from .communication import CommunicationGateway
from .database import Database
from .dispensing import DispensingInventory
from .hardware import Hardware
from .mode_fault import ModeFaultManager
from .user_interface import UserInterface


class Cabinet:
    def __init__(self, world, db_path: str = ":memory:", queue_capacity: int = 50) -> None:
        self._world, self._db_path, self._capacity = world, db_path, queue_capacity
        self.powered = False

    def power_on(self) -> None:
        w = self._world
        self.hw = Hardware(w.drawers)
        self.db = Database(self._db_path, self.hw)
        self.ac, self.mf, self.di = AccessControl(self.db, self.hw), ModeFaultManager(self.db, self.hw), DispensingInventory(self.db, self.hw)
        self.cg = CommunicationGateway(self.db, self.hw, w.link, self._capacity)
        self.ui = UserInterface(w.devices)
        self.ac.connect(self.mf, {"dispense": self.di.request, "confirm": self.di.confirm, "cancel": self.di.cancel,
                                  "replenish": self.di.replenish, "count": self.di.count, "view_log": self.db.review,
                                  "maintenance_start": self.mf.maintenance_start, "maintenance_end": self.mf.maintenance_end,
                                  "resolve_fault": self.mf.resolve_fault})
        self.mf.connect(self.di, self.cg)
        self.di.connect(self.ac, self.mf, self.cg)
        self.cg.connect(self.ac, self.mf)
        self.ui.connect(self.ac, self.mf, self.di, self.cg)
        self.powered = True
        self.di.recover()
        self.mf.power_on()

    def tick(self) -> None:
        """One step of time: pass on the drawer signals, then let each module check its deadlines."""
        if not self.powered:
            return
        for event in self.hw.events():
            self.mf.on_event(event)
            self.di.on_event(event)
        for module in (self.ac, self.mf, self.di, self.cg, self.ui):
            module.tick()

    def power_loss(self) -> None:
        """Everything in memory is lost; only the database file survives."""
        self.powered = False
        self._world.drawers.power_cut()
        self._world.link.detach()
        self.db.close()
        self._world.devices.buzzer(False)
