"""Browser panel for the simulated ADC: a local web page with the touchscreen, the cabinet hardware and a
Monitoring System view. Standard library only. Run:  python run_panel.py
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sim.world import World

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.RLock()


class Runner:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.speed = 1
        self.paused = False
        self.world = None
        self.reset()

    def reset(self) -> None:
        if self.world is not None:
            try:
                self.world.cabinet.db.close()
            except Exception:
                pass
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass
        self.world = World(self.db_path)
        self.world.cabinet.power_on()

    def tick(self) -> None:
        with LOCK:
            if not self.paused:
                self.world.advance(0.1 * self.speed)

    # ---- what the page shows -----------------------------------------------------------
    def state(self) -> dict:
        w, c = self.world, self.world.cabinet
        mon = w.monitor
        base = {"time": w.clock.now(), "iso": w.clock.iso(w.clock.now()), "speed": self.speed, "paused": self.paused,
                "link_up": w.link.up, "silent": w.link.silent, "powered": c.powered}
        drawers = [{"id": d, "bolt": "Locked" if w.drawers.read_bolt(d) == "IN" else "Unlocked",
                    "open": w.drawers.read_sensor(d) == "OPEN", "stuck": w.drawers.is_stuck(d)}
                   for d in w.drawers.drawer_ids()]
        monitor = {"adc_state": mon.adc_state(), "last_status": mon.last_status,
                   "alarms": [{"created": a["created"], "received": a["received"], "payload": a["payload"]} for a in mon.alarms[-5:]],
                   "messages": [{"id": m["id"], "type": m["type"], "created": round(m["created"], 1),
                                 "received": round(m["received"], 1),
                                 "summary": json.dumps(m["payload"])[:110]} for m in mon.messages[-25:]],
                   "inventory": list(mon.inventory.values()), "update": mon.last_update_result,
                   "count": len(mon.messages)}
        if not c.powered:
            return {**base, "drawers": drawers, "screen": None, "buzzer": False, "queue": 0, "monitor": monitor,
                    "catalog": [], "txn": None}
        return {**base, "drawers": drawers, "screen": c.ui.screen(), "buzzer": w.devices.buzzer_on,
                "queue": c.cg.queue_length(), "monitor": monitor,
                "catalog": [r["medication"] for r in c.di.records()]}

    # ---- what the page can do ----------------------------------------------------------
    def do(self, action: str, a: dict) -> dict:
        w, c = self.world, self.world.cabinet
        if action == "badge" and c.powered:
            c.ui.on_credential(a["token"])
        elif action == "touch" and c.powered:
            c.ui.on_touch(a["name"], a.get("params"))
        elif action == "pull":
            w.drawers.pull_drawer(int(a["d"]))
        elif action == "push":
            w.drawers.push_drawer(int(a["d"]))
        elif action == "force":
            w.drawers.force_drawer(int(a["d"]))
        elif action == "stick":
            w.drawers.set_stuck(int(a["d"]), not w.drawers.is_stuck(int(a["d"])))
        elif action == "link":
            w.link.set_up(bool(a["up"]))
        elif action == "silent":
            w.link.set_silent(bool(a["on"]))
        elif action == "update_users":
            w.monitor.send_user_list(
                [{"credential": "NURSE-0001", "user_id": "nurse01", "role": "Nurse"},
                 {"credential": "PHARM-0001", "user_id": "pharm01", "role": "Pharmacist"},
                 {"credential": "TECH-0001", "user_id": "tech01", "role": "Technician"},
                 {"credential": "NURSE-0002", "user_id": "nurse02", "role": "Nurse"}], forged=bool(a.get("forged")))
        elif action == "power_loss" and c.powered:
            c.power_loss()
        elif action == "power_on" and not c.powered:
            c.power_on()
        elif action == "speed":
            self.speed = max(1, min(60, int(a["x"])))
        elif action == "pause":
            self.paused = not self.paused
        elif action == "advance":
            w.advance(float(a["s"]))
        elif action == "reset":
            self.reset()
        return {"ok": True}


def make_handler(runner: Runner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                with LOCK:
                    self._send(200, json.dumps(runner.state()).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/do":
                return self._send(404, b"not found", "text/plain")
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            try:
                with LOCK:
                    out = runner.do(req["action"], req.get("args", {}))
            except Exception as e:                                 # a bad click must never stop the demo
                out = {"ok": False, "error": str(e)}
            self._send(200, json.dumps(out).encode(), "application/json")
    return Handler


def serve(port: int = 8765, db_path: str = "adc_demo.db") -> None:
    runner = Runner(db_path)

    def loop():
        while True:
            time.sleep(0.1)
            runner.tick()
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", port), make_handler(runner)).serve_forever()
