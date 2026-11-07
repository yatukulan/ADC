"""Communication Gateway (CG)."""
import hashlib
import hmac
import json
import secrets

SECRET = b"adc-and-monitoring-system-shared-key"      # A-09: installed with the cabinet (a real system would use TLS)
FORBIDDEN = ("patient_name", "name")                  # NFR-SEC-01: no patient names in messages


class LinkDown(Exception):
    """Raised by the link when a message can't be sent."""


def _keys(label: bytes):
    """One encryption key and one MAC key per direction (label)."""
    return (hmac.new(SECRET, b"enc|" + label, hashlib.sha256).digest(),
            hmac.new(SECRET, b"mac|" + label, hashlib.sha256).digest())


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    """n bytes of keystream. XOR with it to encrypt or decrypt."""
    out = b"".join(hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range(n // 32 + 1))
    return out[:n]


class CommunicationGateway:
    # status every 30 s, alarm at 90% full, link lost after 10 s with no ack, retry every 1 s
    STATUS_PERIOD, ALARM_FRACTION, ACK_TIMEOUT, PROBE_PERIOD = 30, 0.9, 10, 1
    LOG_FIELDS = ("seq", "iso", "type", "user_id", "patient_id", "medication", "quantity", "outcome", "detail")

    def __init__(self, db, hw, link, capacity: int = 50) -> None:
        self._db, self._hw, self._link, self._capacity = db, hw, link, capacity
        self._up = link.is_up()
        self._unacked_since = None                    # when the oldest unacked message was sent
        self._next_status, self._next_probe = hw.now() + self.STATUS_PERIOD, 0.0
        self._log_seq = db.last_seq()                 # log entries up to here are already queued
        link.attach(self.on_incoming)

    def connect(self, ac, mf) -> None:
        self._ac, self._mf = ac, mf

    # ---- questions the screen asks ----
    def is_connected(self) -> bool:
        """Implements: FR-COM-08.
        Link status for the screen."""
        return self._up

    def queue_length(self) -> int:
        return self._db.queue_length()

    def queue_nearly_full(self) -> bool:
        """Implements: FR-COM-07.
        True at 90% full, which turns on the local alarm."""
        return self._db.queue_length() >= self.ALARM_FRACTION * self._capacity

    # ---- sending ----
    def alarm(self, fault: dict) -> None:
        """Implements: FR-COM-01.
        Send a fault alarm as soon as it's raised (limit is 5 s)."""
        self._deliver("ALARM", {"fault_id": fault["id"], "kind": fault["kind"], "drawer": fault["drawer"],
                                "detail": fault["detail"]})

    def inventory(self, rec: dict) -> None:
        """Implements: FR-COM-03.
        Send each record change as soon as it's made (limit is 10 s)."""
        self._deliver("INVENTORY", {"medication": rec["medication"], "quantity": rec["quantity"],
                                    "low_stock": rec["low_stock"], "discrepant": rec["discrepant"]})

    def _deliver(self, kind: str, payload: dict) -> None:
        """Implements: FR-COM-05, NFR-SEC-01, NFR-AVL-01.
        Queue the message, then try to send. The queue is on disk, so it survives a power loss.
        No patient names allowed. A dead link never blocks local work."""
        assert not any(k in payload for k in FORBIDDEN)
        if self._db.queue_length() >= self._capacity:
            self._db.log("COMMUNICATION", outcome="Dropped", detail=f"queue full, {kind} message dropped")
            return
        self._db.queue_push({"id": self._db.next_id("message"), "created": self._hw.now(), "type": kind,
                             "payload": payload})
        self._flush()

    def _flush(self) -> None:
        """Implements: FR-COM-06.
        Send in order. A message is only removed once it's acked."""
        while self._up and (msg := self._db.queue_first()):
            if not self._transmit(msg):
                return
            self._db.queue_pop()

    def _transmit(self, msg: dict) -> bool:
        """Implements: NFR-SEC-02, NFR-SEC-03.
        Encrypt the body and add an auth tag. Returns True if the Monitoring System acked."""
        enc, mac = _keys(b"adc>mon")
        nonce = secrets.token_bytes(12)
        plain = json.dumps({k: msg[k] for k in ("created", "type", "payload")}, sort_keys=True).encode()
        ct = bytes(a ^ b for a, b in zip(plain, _stream(enc, nonce, len(plain))))
        tag = hmac.new(mac, str(msg["id"]).encode() + nonce + ct, hashlib.sha256).hexdigest()
        try:
            reply = self._link.send({"id": msg["id"], "nonce": nonce.hex(), "ct": ct.hex(), "tag": tag})
        except LinkDown:
            if self._up:
                self._set_link(False)
            return False
        if reply.get("ack") == msg["id"]:
            self._unacked_since = None
            return True
        if self._unacked_since is None:
            self._unacked_since = self._hw.now()
        return False

    def _set_link(self, up: bool) -> None:
        """Implements: FR-LOG-06, FR-COM-04.
        Log each loss and restore. Nothing else depends on the link, so local work carries on."""
        self._up, self._unacked_since = up, None
        self._db.log("COMMUNICATION", outcome="Restored" if up else "Lost", detail="link to the Monitoring System")
        self._flush()

    def _status_message(self, now: float) -> dict:
        return {"id": self._db.next_id("message"), "created": now, "type": "STATUS",
                "payload": {"mode": self._mf.mode(), "health": self._mf.health(), "queue": self._db.queue_length()}}

    def tick(self) -> None:
        """Implements: FR-COM-02, FR-COM-04, FR-COM-08 (D1 Figure 8, US-COM-03 AC1).
        Send mode and health every 30 s. The link is lost if the network is down or there's no ack in 10 s,
        and restored when an ack comes back."""
        now = self._hw.now()
        if self._up and (not self._link.is_up() or
                         (self._unacked_since is not None and now - self._unacked_since >= self.ACK_TIMEOUT)):
            self._set_link(False)
        elif not self._up and self._link.is_up() and now >= self._next_probe:
            self._next_probe = now + self.PROBE_PERIOD
            head = self._db.queue_first()                  # probe with the oldest queued message, or a status message
            if self._transmit(head or self._status_message(now)):
                if head:
                    self._db.queue_pop()
                self._set_link(True)
        if self._up:
            self._flush()
        self._forward_log()                                # also while down: the queue holds them
        if now >= self._next_status:
            self._next_status = now + self.STATUS_PERIOD
            if self._up:                                   # a late status is out of date, so never queue it (A-14)
                self._transmit(self._status_message(now))

    def _forward_log(self) -> None:
        """Copy new log entries to the Monitoring System (D1 Figure 1).
        Link entries are skipped, or we'd be sending messages about messages."""
        for e in self._db.log_since(self._log_seq):
            self._log_seq = e["seq"]
            if e["type"] != "COMMUNICATION":
                self._deliver("LOG", {k: e[k] for k in self.LOG_FIELDS})

    # ---- receiving ----
    def on_incoming(self, env: dict) -> dict:
        """Implements: NFR-SEC-04, FR-COM-09, FR-COM-10.
        Only accept an update whose tag checks out. A new user list is applied before this returns,
        so before the next badge."""
        enc, mac = _keys(b"mon>adc")
        nonce, ct = bytes.fromhex(env["nonce"]), bytes.fromhex(env["ct"])
        good = hmac.new(mac, str(env["id"]).encode() + nonce + ct, hashlib.sha256).hexdigest()
        if hmac.compare_digest(good, env["tag"]):
            body = json.loads(bytes(a ^ b for a, b in zip(ct, _stream(enc, nonce, len(ct)))))
            if body.get("type") == "USER_LIST_UPDATE":
                self._ac.apply_user_list_update(body["payload"]["users"])
                return {"accepted": True}
        self._db.log("COMMUNICATION", outcome="Rejected", detail="update from an origin that could not be verified")
        return {"accepted": False, "reason": "origin could not be verified"}
