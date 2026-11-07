"""Names shared by every module. No behaviour here."""
from dataclasses import dataclass
from typing import Any

# operational modes (D1 Figure 11)
UNPOWERED, SELF_TEST, NORMAL, MAINTENANCE, FAULT = "Unpowered", "Self-Test", "Normal", "Maintenance", "Fault"

# roles
NURSE, PHARMACIST, TECHNICIAN = "Nurse", "Pharmacist", "Technician"

# states and outcomes of a dispensing transaction (D1 Figure 12)
REQUESTED, UNLOCKED, OPEN = "Requested", "Unlocked", "Open"
COMPLETED, REJECTED, CANCELLED = "Completed", "Rejected", "Cancelled"
TIMED_OUT, FAULTED, INTERRUPTED = "TimedOut", "Faulted", "Interrupted"

# what a user can ask the ADC to do, and how a rejection names it
ACTIONS = {"dispense": "dispensing request", "confirm": "dispensing confirmation", "cancel": "dispensing cancellation",
           "replenish": "replenishment", "count": "inventory count", "view_log": "log review",
           "maintenance_start": "maintenance request", "maintenance_end": "end of maintenance",
           "resolve_fault": "fault resolution"}


@dataclass
class Result:
    ok: bool
    reason: str = ""
    data: Any = None


@dataclass
class Session:
    user_id: str
    role: str
    last_input: float
