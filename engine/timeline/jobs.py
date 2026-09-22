"""Files de tâches de fond du studio.

Deux files, pour ne pas se marcher dessus :
  * `MEDIA` : préparation des médias (sonde, proxy, vignettes, forme d'onde),
    deux à la fois — ffmpeg sait occuper plusieurs cœurs, mais importer un
    dossier de trente rushs ne doit pas lancer trente encodages ;
  * `TRANSCRIBE` : Whisper, un seul à la fois (mémoire du GPU).

Une tâche qui plante n'arrête pas la file : son erreur est rangée dans le
média concerné par la fonction elle-même.
"""
from __future__ import annotations

import queue
import threading
import traceback


class JobQueue:
    def __init__(self, name: str, workers: int) -> None:
        self.name = name
        self.workers = workers
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._started = False
        self._pending: set = set()     # clés en attente ou en cours (pas de doublon)

    def submit(self, key, fn, *args) -> bool:
        """Met `fn(*args)` en file. Faux si la même clé y est déjà."""
        with self._lock:
            if key in self._pending:
                return False
            self._pending.add(key)
            if not self._started:
                for i in range(self.workers):
                    threading.Thread(target=self._loop, daemon=True,
                                     name=f"{self.name}-{i}").start()
                self._started = True
        self._q.put((key, fn, args))
        return True

    def busy(self, key) -> bool:
        with self._lock:
            return key in self._pending

    def _loop(self) -> None:
        while True:
            key, fn, args = self._q.get()
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - la file doit survivre
                traceback.print_exc()
            finally:
                with self._lock:
                    self._pending.discard(key)
                self._q.task_done()

    def join(self) -> None:
        """Attend que la file soit vide (tests)."""
        self._q.join()


MEDIA = JobQueue("media", 2)
TRANSCRIBE = JobQueue("whisper", 1)
