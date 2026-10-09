"""Local viewer server (M6).

Stdlib HTTP only — no web framework, no build step. The browser side is a single
static file; everything below is JSON in, binary out.

    GET  /                    index.html
    GET  /api/bootstrap       schema + species list + scene list
    GET  /api/species/<name>  parameters of one species file
    POST /api/generate        {params, seed, age, scene, fast} -> packed binary
    POST /api/export          {…, output} -> writes USD, returns the file list
    POST /api/save            {params, path} -> writes a species TOML
    GET  /api/variants        ?seeds=1-9&… -> contact sheet PNG

Fast mode simulates once at meta.max_age and truncates by birth year, which makes
the age slider interactive; exact mode re-runs the simulation for that age.

Generate requests are serialised by one lock, and the browser aborts its previous
request whenever it sends a new one. So a request that finds a newer one has
arrived answers 409 instead of running, and a simulation still in progress is
cancelled when a newer request needs a *different* simulation (one that only
needs the same growth result is left to finish, since it will reuse it). The
max-age simulation behind the fast scrub is never cancelled.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter

from .. import __version__
from ..core.growth import truncate
from ..pipeline import Pipeline, assemble_skeleton
from ..schema import GROUPS, SCHEMA, STAGE_GROUPS, Scene, group_hash, load_scene, load_species, scene_key, validate
from . import jobs
from .payload import debug_arrays, pack

HERE = Path(__file__).parent

# The browser hung up (aborted fetch, closed tab). Not an error worth a traceback.
DISCONNECTS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class Superseded(Exception):
    """A newer generate request arrived; this one's result would be thrown away."""


# 1 = geometry rebuild only, 2 = skeleton + geometry, 3 = full simulation.
# Tiers 1 and 2 are a few tens of milliseconds, so the viewer updates them live while dragging.
TIERS = {g: (1 if g in STAGE_GROUPS["geometry"] else 2 if g in STAGE_GROUPS["radii"] else 3) for g in GROUPS}


def _schema_json():
    groups = {g: [] for g in GROUPS}
    for p in SCHEMA:
        groups[p.group].append({"name": p.name, "kind": p.kind, "default": p.default, "lo": p.lo, "hi": p.hi,
                                "unit": p.unit, "doc": p.doc, "choices": list(p.choices)})
    return groups


class Session:
    """Holds the pipeline plus the full-age simulation used by the fast scrub."""

    def __init__(self, root: Path, cache_dir):
        self.root = root
        self.pipeline = Pipeline(cache_dir=cache_dir)
        self.lock = threading.Lock()
        self._full = None            # (growth key, GrowthResult at max_age)
        self._seq_lock = threading.Lock()
        self._latest = (0, None)     # (sequence number, growth key) of the newest generate
        self.running = None          # sequence number of the generate holding the lock

    def _arrive(self, gkey):
        with self._seq_lock:
            seq = self._latest[0] + 1
            self._latest = (seq, gkey)
            return seq

    def _check(self, seq, gkey):
        """Progress hook: abort a simulation a newer request has no use for."""
        latest, latest_key = self._latest
        if latest > seq and latest_key != gkey:
            raise Superseded()

    def species_files(self):
        return sorted(p.name for p in (self.root / "species").glob("*.toml"))

    def scene_files(self):
        return sorted(p.name for p in (self.root / "scenes").glob("*.toml"))

    def _scene(self, name):
        return load_scene(self.root / "scenes" / name) if name else Scene()

    def generate(self, body):
        params = validate(body["params"], "viewer")
        seed = int(body.get("seed", 42))
        age = float(body.get("age", params["meta"]["reference_age"]))
        lod = float(body.get("lod", 1.0))
        scene = self._scene(body.get("scene"))
        fast = bool(body.get("fast", False))
        t0 = perf_counter()
        # only the simulation inputs: tier 1-2 edits must not throw away the max-age run
        sim_age = params["meta"]["max_age"] if fast else age
        gkey = group_hash(params, STAGE_GROUPS["skeleton"], [seed, sim_age, scene_key(scene)])
        seq = self._arrive(gkey)
        check = lambda *_: self._check(seq, gkey)            # noqa: E731

        with self.lock:
            if self._latest[0] > seq:
                raise Superseded()
            self.running = seq
            try:
                if fast:
                    if self._full is None or self._full[0] != gkey:
                        # never cancelled: the max-age run is what makes every later scrub fast
                        self._full = (gkey, self.pipeline.growth(params, scene, seed, sim_age))
                    g = truncate(self._full[1], age)
                    sk = assemble_skeleton(g, params, seed)
                    from ..geo.sweep import build_geometry
                    geo = build_geometry(sk, params, lod)
                else:
                    g, sk, geo = self.pipeline.geometry(params, scene, seed, age, lod, progress=check)
            finally:
                self.running = None

        extras = debug_arrays(g, params, age) if body.get("debug") else None
        meta = {"version": __version__, "age": age, "seed": seed, "fast": fast,
                "scene": scene.name, "curves": int(sk.curve_count), "height": float(sk.height),
                "trunk_radius": float(sk.radius[0]), "dbh": sk.dbh, "elapsed": perf_counter() - t0,
                "stats": {k: v for k, v in g.stats.items() if isinstance(v, (int, float))},
                "geo_stats": geo.stats}
        return pack(sk, geo, g, meta, extras)

    def export(self, body):
        from ..usd.stage import write_tree
        params = validate(body["params"], "viewer")
        seed, age = int(body.get("seed", 42)), float(body.get("age", 80))
        scene = self._scene(body.get("scene"))
        out = Path(body.get("output") or (self.root / "out" /
                   f"{params['meta']['name']}_{scene.name}_a{age:g}_s{seed}.usda"))
        with self.lock:
            g, sk, geo = self.pipeline.geometry(params, scene, seed, age, float(body.get("lod", 1.0)))
            info = {"version": __version__, "species": params["meta"]["name"], "seed": seed,
                    "age": age, "scene": scene.name, "lod": float(body.get("lod", 1.0))}
            files = write_tree(out, sk, geo, params, info)
        return {"files": [str(f) for f in files]}

    def save_species(self, body):
        params = validate(body["params"], "viewer")
        path = Path(body.get("path") or (self.root / "species" / f"{params['meta']['name']}.toml"))
        path.write_text(to_toml(params), encoding="utf-8")
        return {"path": str(path)}

    def variants(self, body):
        params = validate(body["params"], "viewer")
        seeds = body.get("seeds") or list(range(1, 10))
        job = jobs.start(self.root, params, seeds, float(body.get("age", 80)), body.get("scene") or None)
        return job.status()


def to_toml(params: dict) -> str:
    """Minimal TOML writer — enough for species files (no nested tables beyond one level)."""
    def fmt(v):
        if isinstance(v, str):
            return json.dumps(v)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, dict):
            return "{ " + ", ".join(f"{k} = {fmt(x)}" for k, x in v.items()) + " }"
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(fmt(x) for x in v) + "]"
        if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
            return str(int(v)) if v.is_integer() and abs(v) >= 1 else repr(v)
        return repr(v)

    lines = [f"# written by treegen {__version__} viewer"]
    for group, table in params.items():
        lines.append(f"\n[{group}]")
        for k, v in table.items():
            lines.append(f"{k} = {fmt(v)}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    session: Session = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except DISCONNECTS:
            self.close_connection = True

    def _send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except DISCONNECTS:
            self.close_connection = True

    def do_GET(self):
        s = self.session
        if self.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/bootstrap":
            return self._send(200, {"version": __version__, "schema": _schema_json(), "tiers": TIERS,
                                    "species": s.species_files(), "scenes": s.scene_files(),
                                    "root": str(s.root)})
        if self.path.startswith("/api/variants/"):
            parts = self.path.strip("/").split("/")[2:]
            job = jobs.JOBS.get(parts[0])
            if job is None:
                return self._send(404, {"error": "unknown job"})
            if len(parts) == 1:
                return self._send(200, job.status())
            png = job.images.get(int(parts[1]))
            return self._send(200, png, "image/png") if png else self._send(404, {"error": "not ready"})
        if self.path.startswith("/api/species/"):
            name = self.path.rsplit("/", 1)[-1]
            try:
                return self._send(200, load_species(s.root / "species" / name))
            except Exception as e:                        # noqa: BLE001
                return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        s = self.session
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError as e:
            return self._send(400, {"error": f"bad json: {e}"})
        try:
            if self.path == "/api/generate":
                return self._send(200, s.generate(body), "application/octet-stream")
            if self.path == "/api/export":
                return self._send(200, s.export(body))
            if self.path == "/api/save":
                return self._send(200, s.save_species(body))
            if self.path == "/api/variants":
                return self._send(200, s.variants(body))
        except Superseded:
            return self._send(409, {"error": "superseded by a newer request", "superseded": True})
        except Exception as e:                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        return self._send(404, {"error": "not found"})


def serve(root: Path, port: int = 8765, cache_dir=None, open_browser: bool = True):
    Handler.session = Session(Path(root), cache_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"treegen viewer on {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
