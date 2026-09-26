"""Local demonstration API. Run: python -m model.api --port 8765.

Bound to loopback, with one shared in-memory engine. Not a production server.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock

from .common import ROOT
from .enginefault import DEFAULT_MODEL
from .fusion import FusionEngine
from .telemetry import UNITS


def make_server(engine, port=8765):
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send_json(self, status, payload):
            body = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send_json(200, {"status": "ready", "fuel_model": engine.fuel_predictor.kind,
                                     "fault_model_loaded": engine.fault_predictor is not None,
                                     "hardware_connected": False, "scope": "local research demo"})
            elif self.path == "/v1/schema":
                self.send_json(200, {"schema_version": 1, "units": UNITS,
                                     "example": {"vehicle_id": 1, "trip_id": 1, "source_time_s": 0,
                                                 "mode": "live", "signals": {"rpm": {"value": 1500, "unit": "rpm",
                                                 "measured_at_s": 0, "provenance": "measured"}}}})
            elif self.path == "/":
                path = ROOT / "reports/replays/fusion/index.html"
                if not path.exists():
                    self.send_json(404, {"error": "Run python -m model.demo_fusion first"})
                    return
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(404, {"error": "Unknown route"})

        def do_POST(self):
            if self.path not in {"/v1/telemetry", "/v1/reset"}:
                self.send_json(404, {"error": "Unknown route"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_json(415, {"error": "Content-Type application/json required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("Body must be between 1 byte and 1 MiB")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                with lock:
                    if self.path == "/v1/reset":
                        engine.reset()
                        result = {"status": "reset"}
                    else:
                        if len(engine.states) >= 256 and (payload.get("vehicle_id"), payload.get("trip_id"), payload.get("mode")) not in engine.states:
                            raise ValueError("Demo stream limit reached; reset before another session")
                        result = engine.step(payload)
                self.send_json(200, result)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                self.send_json(400, {"error": str(exc)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--fuel-model", type=Path, default=ROOT / "artifacts/models/gpu_candidate")
    p.add_argument("--fault-model", type=Path, default=DEFAULT_MODEL)
    args = p.parse_args()
    engine = FusionEngine(args.fuel_model, args.fault_model)
    server = make_server(engine, args.port)
    print(f"DriftBeacon API and demo: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
