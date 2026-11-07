"""Communication Gateway (CG).

Secret: everything about talking to the Monitoring System: the message formats, how messages are protected, how a
lost link is noticed (D1 Figure 8), and the queue that holds messages until they are acknowledged.
"""
import hashlib
import hmac
import json
import secrets

SECRET = b"adc-and-monitoring-system-shared-key"      # A-09: installed with the cabinet; the real system would use TLS
FORBIDDEN = ("patient_name", "name")                  # NFR-SEC-01


class LinkDown(Exception):
    """Raised by the link when a message cannot be sent."""


def _keys(label: bytes):
    return (hmac.new(SECRET, b"enc|" + label, hashlib.sha256).digest(),
            hmac.new(SECRET, b"mac|" + label, hashlib.sha256).digest())


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = b"".join(hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range(n // 32 + 1))
    return out[:n]


class CommunicationGateway:
    STATUS_PERIOD, ALARM_FRACTION, ACK_TIMEOUT, PROBE_PERIOD = 30, 0.9, 10, 1
    LOG_FIELDS = ("seq", "iso", "type", "user_id", "patient_id", "medication", "quantity", "outcome", "detail")

    def __init__(self, db, hw, link, capacity: int = 50) -> None:
        self._db, self._hw, self._link, self._capacity = db, hw, link, capacity
        self._up = link.is_up()
        self._unacked_since = None                    # when the oldest message still waiting for an acknowledgement was sent
        self._next_status, self._next_probe = hw.now() + self.STATUS_PERIOD, 0.0
        self._log_seq = db.last_seq()                 # log entries up to here have been handed to the queue
        link.attach(self.on_incoming)

    def connect(self, ac, mf) -> None:
        self._ac, self._mf = ac, mf

    # ---- questions the screen asks ----
    def is_connected(self) -> bool:
        """Implements: FR-COM-08 (the communication status is available to be shown on the local display)."""
        return self._up

    def queue_length(self) -> int:
        return self._db.queue_length()

    def queue_nearly_full(self) -> bool:
        """Implements: FR-COM-07 (a local alarm is shown when the transmission queue is 90% full)."""
        return self._db.queue_length() >= self.ALARM_FRACTION * self._capacity

    # ---- sending ----
    def alarm(self, fault: dict) -> None:
        """Implements: FR-COM-01 (each fault alarm is sent as soon as it is raised, far inside 5 s)."""
        self._deliver("ALARM", {"fault_id": fault["id"], "kind": fault["kind"], "drawer": fault["drawer"],
                                "detail": fault["detail"]})

    def inventory(self, rec: dict) -> None:
        """Implements: FR-COM-03 (each change to a record is sent as soon as it is made, far inside 10 s)."""
        self._deliver("INVENTORY", {"medication": rec["medication"], "quantity": rec["quantity"],
                                    "low_stock": rec["low_stock"], "discrepant": rec["discrepant"]})

    def _deliver(self, kind: str, payload: dict) -> None:
        """Implements: FR-COM-05 (a message that cannot be sent stays in the transmission queue, which survives a power
        loss), NFR-SEC-01 (a message has no field for a patient name), NFR-AVL-01 (a lost link never blocks local
        work: the message is queued and the caller carries on)."""
        assert not any(k in payload for k in FORBIDDEN)
        if self._db.queue_length() >= self._capacity:
            self._db.log("COMMUNICATION", outcome="Dropped", detail=f"queue full, {kind} message dropped")
            return
        self._db.queue_push({"id": self._db.next_id("message"), "created": self._hw.now(), "type": kind,
                             "payload": payload})
        self._flush()

    def _flush(self) -> None:
        """Implements: FR-COM-06 (queued messages are sent in order of creation, each removed only once acknowledged)."""
        while self._up and (msg := self._db.queue_first()):
            if not self._transmit(msg):
                return
            self._db.queue_pop()

    def _transmit(self, msg: dict) -> bool:
        """Implements: NFR-SEC-02 (the body is encrypted), NFR-SEC-03 (the envelope carries an authentication tag).
        Returns True when the Monitoring System acknowledged the message."""
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
        """Implements: FR-LOG-06 (each loss and each restoration of communication is logged), FR-COM-04 (nothing else
        in the ADC depends on the link, so local requests carry on)."""
        self._up, self._unacked_since = up, None
        self._db.log("COMMUNICATION", outcome="Restored" if up else "Lost", detail="link to the Monitoring System")
        self._flush()

    def _status_message(self, now: float) -> dict:
        return {"id": self._db.next_id("message"), "created": now, "type": "STATUS",
                "payload": {"mode": self._mf.mode(), "health": self._mf.health(), "queue": self._db.queue_length()}}

    def tick(self) -> None:
        """Implements: FR-COM-02 (mode and health are sent every 30 s while communication is available), FR-COM-04 and
        FR-COM-08 (communication is lost when the network reports no link or a message is not acknowledged within 10 s,
        and restored when the Monitoring System acknowledges a message again: D1 Figure 8, US-COM-03 AC1)."""
        now = self._hw.now()
        if self._up and (not self._link.is_up() or
                         (self._unacked_since is not None and now - self._unacked_since >= self.ACK_TIMEOUT)):
            self._set_link(False)
        elif not self._up and self._link.is_up() and now >= self._next_probe:
            self._next_probe = now + self.PROBE_PERIOD
            head = self._db.queue_first()                  # the oldest waiting message, or a status message, as a probe
            if self._transmit(head or self._status_message(now)):
                if head:
                    self._db.queue_pop()
                self._set_link(True)
        if self._up:
            self._flush()
        self._forward_log()                                # also while the link is down: the queue holds them
        if now >= self._next_status:
            self._next_status = now + self.STATUS_PERIOD
            if self._up:                                   # a late status is out of date, so it is never queued (A-14)
                self._transmit(self._status_message(now))

    def _forward_log(self) -> None:
        """Log entries go to the Monitoring System too (D1 Figure 1). Entries about the link are not sent: they
        would create messages about messages."""
        for e in self._db.log_since(self._log_seq):
            self._log_seq = e["seq"]
            if e["type"] != "COMMUNICATION":
                self._deliver("LOG", {k: e[k] for k in self.LOG_FIELDS})

    # ---- receiving ----
    def on_incoming(self, env: dict) -> dict:
        """Implements: NFR-SEC-04 (an update is accepted only after its origin is verified), FR-COM-09 (an authorized-
        user-list update is accepted), FR-COM-10 (it is applied before the call returns, so before the next credential
        presentation)."""
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
