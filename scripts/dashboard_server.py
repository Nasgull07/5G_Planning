#!/usr/bin/env python3
"""Local web dashboard for the existing 5G planning workflows."""

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
UI_FILE = ROOT / "ui" / "index.html"
SCRIPTS = ROOT / "scripts"
DATA_DIR = ROOT / "data" / "processed"
OUTPUT_DIR = ROOT / "output"
SCENARIO_FILE = DATA_DIR / "scenario_sites.csv"

JOB_LOCK = threading.Lock()
JOB = {
    "id": None,
    "status": "idle",
    "label": "",
    "step": "",
    "log": [],
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bounded_int(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an integer.") from None
    if str(value).strip() != str(parsed):
        raise ValueError(f"{label} must be an integer.")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return parsed


def positive_float(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return parsed


def count_csv_rows(path):
    if not path.is_file():
        return 0
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        return sum(1 for _ in csv.DictReader(source))


def build_commands(action, payload):
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    if action == "download":
        workers = bounded_int(payload.get("workers", 2), "Workers", 1, 16)
        return "Downloading cells", [
            [sys.executable, str(SCRIPTS / "download_cells.py"), "--workers", str(workers)]
        ]

    if action == "scenario":
        real_count = bounded_int(payload.get("real_count", 350), "Real RRHs", 1, 5000)
        synthetic_count = bounded_int(
            payload.get("synthetic_count", 50), "Synthetic RRHs", 0, 5000
        )
        min_distance = positive_float(
            payload.get("min_distance", 200), "Minimum distance (m)", 1, 100000
        )
        seed = bounded_int(payload.get("seed", 42), "Seed", -2147483648, 2147483647)
        scenario_command = [
            sys.executable,
            str(SCRIPTS / "create_scenario_sites.py"),
            "--real-count",
            str(real_count),
            "--count",
            str(synthetic_count),
            "--min-distance",
            str(min_distance),
            "--seed",
            str(seed),
        ]
        map_command = [
            sys.executable,
            str(SCRIPTS / "create_cell_sites_map.py"),
        ]
        return "Generating scenario and map", [scenario_command, map_command]

    if action == "assignment":
        if not SCENARIO_FILE.is_file():
            raise ValueError("Generate a scenario before running an assignment.")
        scenario_size = count_csv_rows(SCENARIO_FILE)
        if scenario_size < 1:
            raise ValueError("The generated scenario contains no RRHs.")
        pool_count = bounded_int(payload.get("pool_count", 3), "BBU Pools", 1, scenario_size)
        return "Running K-Medoids assignment", [
            [
                sys.executable,
                str(SCRIPTS / "bbu_initial_assignment_kmedian.py"),
                "--k_pools",
                str(pool_count),
            ]
        ]

    raise ValueError("Unknown dashboard action.")


def add_log(job_id, line):
    with JOB_LOCK:
        if JOB["id"] != job_id:
            return
        JOB["log"].append(line.rstrip())
        JOB["log"] = JOB["log"][-250:]


def run_commands(job_id, label, commands):
    error = None
    for index, command in enumerate(commands, start=1):
        display_command = " ".join(
            Path(part).name if index == 1 and part == sys.executable else part
            for part in command
        )
        with JOB_LOCK:
            if JOB["id"] == job_id:
                JOB["step"] = f"Step {index} of {len(commands)}"
        add_log(job_id, f"> {display_command}")
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
            )
            if process.stdout is not None:
                for line in process.stdout:
                    add_log(job_id, line)
            return_code = process.wait()
        except OSError as exception:
            error = f"Could not start workflow: {exception}"
            add_log(job_id, error)
            break
        if return_code != 0:
            error = f"Workflow stopped with exit code {return_code}."
            add_log(job_id, error)
            break

    with JOB_LOCK:
        if JOB["id"] != job_id:
            return
        JOB["status"] = "failed" if error else "completed"
        JOB["step"] = "Finished"
        JOB["error"] = error
        JOB["finished_at"] = utc_now()


def start_job(action, payload):
    label, commands = build_commands(action, payload)
    with JOB_LOCK:
        if JOB["status"] == "running":
            raise RuntimeError("Another workflow is already running.")
        job_id = uuid.uuid4().hex
        JOB.update({
            "id": job_id,
            "status": "running",
            "label": label,
            "step": "Starting",
            "log": [],
            "error": None,
            "started_at": utc_now(),
            "finished_at": None,
        })
    worker = threading.Thread(
        target=run_commands,
        args=(job_id, label, commands),
        daemon=True,
    )
    worker.start()
    return get_job()


def get_job():
    with JOB_LOCK:
        return dict(JOB, log=list(JOB["log"]))


def read_scenario_counts():
    if not SCENARIO_FILE.is_file():
        return {"available": False, "total": 0, "real": 0, "synthetic": 0}
    total = real = synthetic = 0
    with SCENARIO_FILE.open("r", newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            total += 1
            if (row.get("is_synthetic") or "").strip().casefold() == "true":
                synthetic += 1
            else:
                real += 1
    return {"available": True, "total": total, "real": real, "synthetic": synthetic}


def dashboard_state():
    cells_file = DATA_DIR / "cellular_sites.csv"
    return {
        "cells_available": cells_file.is_file(),
        "cell_count": count_csv_rows(cells_file),
        "scenario": read_scenario_counts(),
        "job": get_job(),
    }


def parse_run_parameters(filename):
    match = re.search(r"_k(\d+)(?:_s(\d+))?", filename)
    if not match:
        return {"k": None, "synthetic_rrhs": None}
    return {
        "k": int(match.group(1)),
        "synthetic_rrhs": int(match.group(2)) if match.group(2) else None,
    }


def assignment_metadata(path, fallback_k=None):
    if not path.is_file():
        return {
            "k": fallback_k,
            "rrhs": None,
            "real_rrhs": None,
            "synthetic_rrhs": None,
        }

    total = synthetic = 0
    pools = set()
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            total += 1
            if (row.get("is_synthetic") or "").strip().casefold() in {
                "true", "1", "t", "yes",
            }:
                synthetic += 1
            pool = (row.get("bbu_pool") or "").strip()
            if pool:
                pools.add(pool)
    return {
        "k": fallback_k or len(pools) or None,
        "rrhs": total,
        "real_rrhs": total - synthetic,
        "synthetic_rrhs": synthetic,
    }


def assignment_file_for(path):
    if path.name.startswith("rrh_bbu_") and path.name.endswith("_assignments.csv"):
        return path
    if path.name.startswith("bbu_pool_") and path.name.endswith("_locations.csv"):
        run_name = path.stem[len("bbu_pool_"):-len("_locations")]
        return DATA_DIR / f"rrh_bbu_{run_name}_assignments.csv"
    return None


def artifact_catalog():
    artifacts = []
    for path in sorted(OUTPUT_DIR.glob("*.png"), key=lambda item: item.name.casefold()):
        artifacts.append({
            "name": path.name,
            "title": path.stem.replace("_", " ").replace("-", " ").title(),
            "kind": "chart",
            "k": None,
            "rrhs": None,
            "real_rrhs": None,
            "synthetic_rrhs": None,
            "size": path.stat().st_size,
            "url": f"/assets/chart/{path.name}",
        })

    for path in sorted(DATA_DIR.glob("*.csv"), key=lambda item: item.name.casefold()):
        if path.name in {"cellular_sites.csv", "scenario_sites.csv"}:
            continue
        if not path.name.startswith(("rrh_bbu_", "bbu_pool_", "bbu_kmedian_")):
            continue
        parameters = parse_run_parameters(path.stem)
        assignment_path = assignment_file_for(path)
        details = assignment_metadata(assignment_path, parameters["k"]) if assignment_path else {}
        synthetic_count = details.get("synthetic_rrhs")
        if synthetic_count is None:
            synthetic_count = parameters["synthetic_rrhs"]
        artifacts.append({
            "name": path.name,
            "title": path.stem.replace("_", " ").replace("-", " ").title(),
            "kind": "table",
            "k": details.get("k", parameters["k"]),
            "rrhs": details.get("rrhs"),
            "real_rrhs": details.get("real_rrhs"),
            "synthetic_rrhs": synthetic_count,
            "size": path.stat().st_size,
            "url": f"/assets/table/{path.name}",
        })
    return artifacts


def map_catalog():
    maps = []
    for path in OUTPUT_DIR.glob("*.html"):
        parameters = parse_run_parameters(path.stem)
        if path.name == "cellular_sites_map.html":
            scenario = read_scenario_counts()
            details = {
                "k": None,
                "rrhs": scenario["total"] if scenario["available"] else None,
                "real_rrhs": scenario["real"] if scenario["available"] else None,
                "synthetic_rrhs": (
                    scenario["synthetic"] if scenario["available"] else None
                ),
                "map_type": "scenario",
            }
        elif path.name.startswith("rrh_bbu_") and path.name.endswith(
            "_assignments_map.html"
        ):
            assignment_name = path.name[:-len("_map.html")] + ".csv"
            details = assignment_metadata(
                DATA_DIR / assignment_name,
                parameters["k"],
            )
            if details["synthetic_rrhs"] is None:
                details["synthetic_rrhs"] = parameters["synthetic_rrhs"]
            details["map_type"] = "assignment"
        else:
            details = {
                "k": parameters["k"],
                "rrhs": None,
                "real_rrhs": None,
                "synthetic_rrhs": parameters["synthetic_rrhs"],
                "map_type": "map",
            }
        maps.append({
            "name": path.name,
            "title": path.stem.replace("_", " ").replace("-", " ").title(),
            "modified": path.stat().st_mtime,
            "url": f"/maps/{path.name}",
            **details,
        })
    return sorted(maps, key=lambda item: item["modified"], reverse=True)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "5GPlanningDashboard/1.0"

    def send_bytes(self, content, content_type, status=200, download_name=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{download_name}"',
            )
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload, status=200):
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(content, "application/json; charset=utf-8", status)

    def send_error_json(self, message, status):
        self.send_json({"error": message}, status)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request body length.") from None
        if length < 0 or length > 65536:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Request body must contain valid JSON.") from None
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/":
                if not UI_FILE.is_file():
                    self.send_error(404, "Dashboard UI not found.")
                    return
                self.send_bytes(UI_FILE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self.send_json(dashboard_state())
            elif path == "/api/catalog":
                self.send_json({"maps": map_catalog(), "artifacts": artifact_catalog()})
            elif path == "/api/job":
                self.send_json(get_job())
            elif path.startswith("/maps/"):
                self.serve_map(path[len("/maps/"):])
            elif path.startswith("/assets/"):
                self.serve_asset(path[len("/assets/"):])
            else:
                self.send_error(404, "Not found.")
        except (OSError, csv.Error, UnicodeError) as exception:
            self.send_error_json(str(exception), 500)

    def serve_map(self, raw_name):
        name = unquote(raw_name)
        if not name or Path(name).name != name or not name.endswith(".html"):
            self.send_error(404, "Map not found.")
            return
        path = OUTPUT_DIR / name
        if not path.is_file():
            self.send_error(404, "Map not found.")
            return
        self.send_bytes(path.read_bytes(), "text/html; charset=utf-8")

    def serve_asset(self, raw_path):
        parts = [unquote(part) for part in raw_path.split("/")]
        if len(parts) != 2 or Path(parts[1]).name != parts[1]:
            self.send_error(404, "Artifact not found.")
            return
        kind, name = parts
        if kind == "chart" and name.endswith(".png"):
            path = OUTPUT_DIR / name
            content_type = "image/png"
            download_name = None
        elif kind == "table" and name.endswith(".csv"):
            path = DATA_DIR / name
            content_type = "text/csv; charset=utf-8"
            download_name = name
        else:
            self.send_error(404, "Artifact not found.")
            return
        if not path.is_file():
            self.send_error(404, "Artifact not found.")
            return
        self.send_bytes(path.read_bytes(), content_type, download_name=download_name)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not path.startswith("/api/jobs/"):
            self.send_error_json("Not found.", 404)
            return
        action = path.removeprefix("/api/jobs/")
        try:
            payload = self.read_json()
            job = start_job(action, payload)
            self.send_json(job, 202)
        except ValueError as exception:
            self.send_error_json(str(exception), 400)
        except RuntimeError as exception:
            self.send_error_json(str(exception), 409)

    def log_message(self, format_string, *args):
        print(f"[dashboard] {self.address_string()} - {format_string % args}")


def main():
    parser = argparse.ArgumentParser(description="Run the local 5G Planning dashboard.")
    parser.add_argument("--port", type=int, default=8765, help="Local web server port.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"5G Planning dashboard running at http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
