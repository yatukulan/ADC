"""Stand-in for the Monitoring System (built by the other team), plus the link between the two systems.

Follows the design doc interface: checks and acks every message, shows alarms, tracks the ADC's status and can
send user-list updates. Uses its own copy of the encryption, not the ADC's code.
"""
import hashlib
import hmac
import json
import secrets
from typing import Callable, Dict, List, Optional

from adc.communication import LinkDown, SECRET


class SimLink:
    """The network between the ADC and the Monitoring System."""

    def __init__(self) -> None:
        self.up = True
        self.silent = False                                # network is up but the Monitoring System doesn't answer
        self._adc_in: Optional[Callable[[Dict], Dict]] = None
        self.monitor = None

    def is_up(self) -> bool:
        return self.up

    def attach(self, on_incoming) -> None:
        self._adc_in = on_incoming

    def detach(self) -> None:
        self._adc_in = None

    def send(self, env: Dict) -> Dict:
        if not self.up:
            raise LinkDown()
        if self.silent:
            return {}
        return self.monitor.receive(env)

    def to_adc(self, env: Dict) -> Dict:
        if not self.up or self._adc_in is None:
            return {"accepted": False, "reason": "ADC not reachable"}
        return self._adc_in(env)

    def set_up(self, up: bool) -> None:
        self.up = up

    def set_silent(self, silent: bool) -> None:
        self.silent = silent


class MonitoringStub:
    SILENT_AFTER = 90        # s without a status message before the ADC shows as not reporting

    def __init__(self, clock, link) -> None:
        self._t, self._link = clock, link
        link.monitor = self
        self.messages: List[Dict] = []
        self.alarms: List[Dict] = []
        self.inventory: Dict[str, Dict] = {}
        self.last_status: Optional[Dict] = None
        self.last_status_at: Optional[float] = None
        self.last_update_result = ""
        self._seen = set()

    # --- keys: same derivation as the interface doc ---
    @staticmethod
    def _keys(secret: bytes, label: bytes):
        return (hmac.new(secret, b"enc|" + label, hashlib.sha256).digest(),
                hmac.new(secret, b"mac|" + label, hashlib.sha256).digest())

    @staticmethod
    def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
        out, i = b"", 0
        while len(out) < n:
            out += hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
            i += 1
        return out[:n]

    def receive(self, env: Dict) -> Dict:
        enc, mac = self._keys(SECRET, b"adc>mon")
        nonce, ct = bytes.fromhex(env["nonce"]), bytes.fromhex(env["ct"])
        good = hmac.new(mac, str(env["id"]).encode() + nonce + ct, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, env["tag"]):
            return {"nack": "bad tag"}
        body = json.loads(bytes(a ^ b for a, b in zip(ct, self._stream(enc, nonce, len(ct)))))
        if env["id"] in self._seen:                 # a repeat (lost ack): ack it again but don't store it twice
            return {"ack": env["id"]}
        self._seen.add(env["id"])
        row = {"id": env["id"], "created": body["created"], "received": self._t.now(), "type": body["type"],
               "payload": body["payload"]}
        self.messages.append(row)
        if body["type"] == "ALARM":
            self.alarms.append(row)
        elif body["type"] == "STATUS":
            self.last_status, self.last_status_at = body["payload"], self._t.now()
        elif body["type"] == "INVENTORY":
            self.inventory[body["payload"]["medication"]] = body["payload"]
        return {"ack": env["id"]}

    def adc_state(self) -> str:
        if self.last_status_at is None:
            return "No status received yet"
        if self._t.now() - self.last_status_at > self.SILENT_AFTER:
            return f"ADC not reporting for {int(self._t.now() - self.last_status_at)} s"
        return "ADC reporting"

    # --- updates from the Monitoring System to the ADC ---
    def send_user_list(self, users: List[Dict], forged: bool = False) -> Dict:
        secret = secrets.token_bytes(16) if forged else SECRET
        enc, mac = self._keys(secret, b"mon>adc")
        nonce = secrets.token_bytes(12)
        hashed = [{"credential_hash": hashlib.sha256(u["credential"].encode()).hexdigest(), "user_id": u["user_id"],
                   "role": u["role"]} for u in users]          # send hashes, not credentials (D1 Figure 10)
        plain = json.dumps({"type": "USER_LIST_UPDATE", "payload": {"users": hashed}}, sort_keys=True).encode()
        ct = bytes(a ^ b for a, b in zip(plain, self._stream(enc, nonce, len(plain))))
        env = {"id": 1, "nonce": nonce.hex(), "ct": ct.hex(),
               "tag": hmac.new(mac, b"1" + nonce + ct, hashlib.sha256).hexdigest()}
        result = self._link.to_adc(env)
        self.last_update_result = ("accepted" if result.get("accepted") else f"rejected ({result.get('reason')})")
        return result
