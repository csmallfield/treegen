"""Background variant rendering.

Nine seeds are nine independent simulations, so they run in worker processes and
stream back to the browser one at a time instead of blocking the server for
minutes. Falls back to threads if processes are unavailable (they still help a
little, since numpy releases the GIL in places).
"""
from __future__ import annotations

import io
import os
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

_POOL = None
_POOL_LOCK = threading.Lock()


def _pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            workers = max(1, min((os.cpu_count() or 2) - 1, 6))
            try:
                _POOL = ProcessPoolExecutor(max_workers=workers)
                _POOL.submit(int, 0).result(timeout=30)     # verify workers actually start
            except Exception:                               # noqa: BLE001
                _POOL = ThreadPoolExecutor(max_workers=workers)
        return _POOL


def render_variant(root: str, params: dict, seed: int, age: float, scene_name: str | None, size: float = 4.0):
    """Run one simulation and return (seed, PNG bytes): one side-view silhouette.

    The frame comes from the envelope at this age, not from the tree, so every seed
    is drawn at the same scale and height differences between variants stay visible.
    """
    import matplotlib
    matplotlib.use("Agg")
    from ..core.age import age_state
    from ..core.envelope import Envelope
    from ..core.growth import envelope_stretch
    from ..pipeline import Pipeline
    from ..preview import render_png
    from ..schema import Scene, load_scene

    scene = load_scene(Path(root) / "scenes" / scene_name) if scene_name else Scene()
    _, sk = Pipeline().skeleton(params, scene, int(seed), float(age))
    buf = io.BytesIO()
    env = Envelope.at_age(params, age_state(params, age), envelope_stretch(params, scene)(age))
    render_png(sk, buf, mode="skeleton", size=size, views=("side",),
               extent=max(env.height * 1.1, sk.height * 1.05), half_width=env.radius * 1.15)
    return int(seed), buf.getvalue()


class VariantJob:
    def __init__(self, root, params, seeds, age, scene_name):
        self.id = uuid.uuid4().hex[:12]
        self.seeds = [int(s) for s in seeds]
        self.images: dict[int, bytes] = {}
        self.errors: dict[int, str] = {}
        self.lock = threading.Lock()
        pool = _pool()
        self.futures = [pool.submit(render_variant, str(root), params, s, age, scene_name) for s in self.seeds]
        for f in self.futures:
            f.add_done_callback(self._done)

    def _done(self, fut):
        try:
            seed, png = fut.result()
            with self.lock:
                self.images[seed] = png
        except Exception as e:                              # noqa: BLE001
            with self.lock:
                self.errors[-len(self.errors) - 1] = f"{type(e).__name__}: {e}"

    def status(self):
        with self.lock:
            return {"id": self.id, "seeds": self.seeds, "ready": sorted(self.images),
                    "errors": list(self.errors.values()),
                    "done": len(self.images) + len(self.errors) >= len(self.seeds)}


JOBS: dict[str, VariantJob] = {}


def start(root, params, seeds, age, scene_name) -> VariantJob:
    job = VariantJob(root, params, seeds, age, scene_name)
    JOBS[job.id] = job
    for old in list(JOBS)[:-8]:                             # keep the last few jobs only
        JOBS.pop(old, None)
    return job
