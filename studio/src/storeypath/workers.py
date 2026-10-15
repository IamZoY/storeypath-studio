"""Floors read in processes of their own (``storeypath serve``: STOREYPATH_READ_WORKERS),
so that reads use the machine's cores: Python runs one thread of Studio's code at a time,
and a floor's read is mostly that. Two people drawing on two floors are read side by
side, and so are a building's floors read together. A drawing drawn whole for the
drawing viewer (drawview.py) is drawn in one too: it takes as long as a read; and so are
the floors' parts of their drawings a package carries.

Each floor is read by the same worker every time (by its ID): what a read keeps of the
floor's drawing and of its last read (convert.py) is that worker's, ready for the next.
A worker reads one floor at a time; what it reads it sends back (convert.FloorRead, with
what the models answered), and the read is applied to the project here, as a read made
here is. Its models are the ones Studio uses: the language model's server Studio started
(asked over HTTP), the vision model's GPU helpers as Studio has them, the symbol spotter
(loaded in the worker the first time a floor needs it). What it says as it reads comes
back as it says it, and a read asked to stop stops where it says how far it has got.
"""

from __future__ import annotations

import multiprocessing
import os
import threading
import traceback
import zlib
from typing import Callable

POLL_S = 0.2  # how often a read waited on is asked whether to stop


def default_count() -> int:
    """STOREYPATH_READ_WORKERS, else as many as the machine has cores to spare, at most 4."""
    said = os.environ.get("STOREYPATH_READ_WORKERS")
    if said is not None and said.strip().isdigit():
        return int(said)
    return max(1, min(4, (os.cpu_count() or 2) - 1))


class ReadWorkers:
    """``count`` worker processes reading floors (see the module)."""

    def __init__(self, count: int):
        self._context = multiprocessing.get_context("spawn")  # (a fresh interpreter: no thread or lock of ours)
        self._workers = [_Worker(self._context, n) for n in range(count)]

    def __len__(self) -> int:
        return len(self._workers)

    def _of(self, floor_id: str) -> "_Worker":
        return self._workers[zlib.crc32(floor_id.encode()) % len(self._workers)]

    def read(self, floor_id: str, task: dict, say: Callable[[str], None] | None = None,
             stopping: Callable[[], bool] = lambda: False):
        """convert.read_floor in the floor's worker (``task``: its arguments, and the
        models' settings, models_settings): (the read, the readings and vision answers
        the worker's copy of the project has after it). ``say`` gets what it says; a
        read is asked to stop when ``say`` raises, or ``stopping()`` is true."""
        return self._of(floor_id).run({"do": "read", "floor_id": floor_id, **task}, say, stopping)

    def keep(self, floor_id: str, task: dict) -> bool:
        """convert.keep_a_read in the floor's worker: whether a read was kept."""
        return self._of(floor_id).run({"do": "keep", "floor_id": floor_id, **task}, None, lambda: False)

    def view(self, key: str, task: dict, say: Callable[[str], None] | None = None) -> dict:
        """drawview.write_view in a worker (``task``: the drawing's file and the view's,
        ``drawing`` and ``out``): what the view holds, in numbers. A worker reading no floor
        when there is one, so that a person waiting to see a drawing does not wait for a read."""
        free = [w for w in self._workers if not w._lock.locked()]
        worker = free[zlib.crc32(key.encode()) % len(free)] if free else self._of(key)
        return worker.run({"do": "view", **task}, say, lambda: False)

    def floor_views(self, key: str, task: dict, say: Callable[[str], None] | None = None) -> list[dict]:
        """drawview.write_floor_views in a worker (``task``: the drawing's file, ``drawing``,
        and its floors' parts, ``floors``), as view: what each holds, in numbers."""
        free = [w for w in self._workers if not w._lock.locked()]
        worker = free[zlib.crc32(key.encode()) % len(free)] if free else self._of(key)
        return worker.run({"do": "floor views", **task}, say, lambda: False)

    def close(self) -> None:
        for w in self._workers:
            w.close()


class _Worker:
    def __init__(self, context, n: int):
        self._context, self.n = context, n
        self._lock = threading.Lock()  # one read at a time
        self._start()

    def _start(self) -> None:
        self._conn, child = self._context.Pipe()
        self._stop = self._context.Event()
        self._proc = self._context.Process(target=_serve, args=(child, self._stop), name=f"storeypath reader {self.n}",
                                           daemon=True)
        self._proc.start()
        child.close()

    def run(self, task: dict, say, stopping):
        with self._lock:
            if not self._proc.is_alive():
                self._start()
            self._stop.clear()
            self._conn.send(task)
            asked_to_stop: BaseException | None = None
            while True:
                if not self._conn.poll(POLL_S):
                    if not self._proc.is_alive():
                        self._start()
                        raise RuntimeError("the process reading the floor ended before it finished "
                                           "(it is started again)")
                    if stopping():
                        self._stop.set()
                    continue
                kind, value = self._conn.recv()
                if kind == "say":
                    if say is not None and asked_to_stop is None:
                        try:
                            say(value)
                        except BaseException as e:  # (Stopped: the read stops where it says how far it got)
                            asked_to_stop = e
                            self._stop.set()
                    continue
                if kind == "done":
                    if asked_to_stop is not None:
                        raise asked_to_stop
                    return value
                raise value if asked_to_stop is None else asked_to_stop  # "failed"

    def close(self) -> None:
        try:
            self._conn.send(None)
        except (OSError, BrokenPipeError):
            pass
        self._proc.join(timeout=5)
        if self._proc.is_alive():
            self._proc.terminate()


# ---- in the worker -----------------------------------------------------------------------

def _serve(conn, stop) -> None:
    """A worker: tasks read one after another until told to end (None)."""
    models = _Models()
    while True:
        try:
            task = conn.recv()
        except EOFError:
            return
        if task is None:
            return
        try:
            conn.send(("done", _do(task, models, conn, stop)))
        except BaseException as e:  # noqa: BLE001 (sent back, raised where the read was asked for)
            conn.send(("failed", _sendable(e)))


def _do(task: dict, models: "_Models", conn, stop):
    from .convert import _keep_read, keep_a_read, read_floor
    from .server import Stopped
    from .workspace import Workspace

    if task["do"] == "view":  # a drawing drawn whole (drawview.py): written where asked
        from .drawview import write_view

        return write_view(task["drawing"], task["out"], say=lambda line: conn.send(("say", line)))
    if task["do"] == "floor views":  # floors' parts of a drawing, as a package carries them
        from .drawview import write_floor_views

        return write_floor_views(task["drawing"], task["floors"], say=lambda line: conn.send(("say", line)))
    ws = Workspace.model_validate_json(task["ws"])
    if task["do"] == "keep":
        return keep_a_read(ws, task["floor_id"], task["folder"], task["reads"])

    def say(line: str) -> None:
        conn.send(("say", line))
        if stop.is_set():
            raise Stopped("stopped")

    model, vision, symbols = models.use(task["models"])
    read = read_floor(ws, task["floor_id"], task["folder"], model, symbols, vision, say=say, tiles=task["tiles"],
                      drawn=task["drawn"], reads=task["reads"])
    _keep_read(task["floor_id"], read.kept)  # (here, for the floor read again: on disk once it is applied)
    return read, ws.readings, ws.vision


class _Models:
    """The models a worker reads with, as Studio's settings say, made again when they change."""

    def __init__(self):
        self._said = None
        self._models = (None, None, None)
        self._symbols = None

    def use(self, said: dict):
        if said != self._said:
            from .llm import LocalModel
            from .vision import VisionModel

            model = LocalModel(url=said["model_url"]) if said.get("model_url") else None
            vision = None
            if said.get("vision"):
                vision = VisionModel(model=said.get("vision_model") or None)
                if said.get("helpers") is not None and hasattr(vision, "configure"):
                    vision.configure(said["helpers"])
            self._said, self._models = said, (model, vision, None)
        model, vision, _ = self._models
        symbols = None
        if said.get("symbols"):
            if self._symbols is None:
                from .symbols import SymbolSpotter

                self._symbols = SymbolSpotter()
            symbols = self._symbols
        return model, (vision if vision is not None and vision.available() else None), symbols


def _sendable(e: BaseException) -> BaseException:
    """An error as it can be sent back (pickled), with what it said."""
    import pickle

    try:
        pickle.dumps(e)
        return e
    except Exception:  # noqa: BLE001
        return RuntimeError(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def models_settings(studio) -> dict:
    """The models' settings a worker reads with: Studio's (server.Studio)."""
    model_url = None
    if studio.model.available():
        try:
            model_url = studio.model._ensure_started()  # (the server Studio started: asked over HTTP)
        except Exception:  # noqa: BLE001 (read without it: the read says so)
            model_url = None
    return {"model_url": model_url, "vision": studio.vision.available(),
            "vision_model": getattr(studio.vision, "model", None), "helpers": studio._helpers_said,
            "symbols": studio.symbols.available()}
