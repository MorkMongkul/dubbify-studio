"""macOS priorities (QoS classes) for the threads that run character recognition's models.

The start-up's work runs at BACKGROUND (store.at_background_priority): on Apple Silicon that is the efficiency cores, so
a fanless Mac stays cool. Timing this Mac's face step (characters.speed) always runs at DEFAULT: a timing taken on the
efficiency cores would turn faces off for the whole app version."""
import ctypes
import sys
from contextlib import contextmanager

DEFAULT, BACKGROUND = 0x15, 0x09   # QOS_CLASS_DEFAULT, QOS_CLASS_BACKGROUND


@contextmanager
def thread_at(qos: int):
    """This thread at `qos` while inside, then back as it was (macOS; nothing elsewhere). The threads and processes it
    starts meanwhile — ffmpeg, the models' — get the same (measured: a whole run moved to the efficiency cores)."""
    if sys.platform != "darwin":
        yield
        return
    system = ctypes.CDLL("/usr/lib/libSystem.dylib")
    system.qos_class_self.restype = ctypes.c_uint
    before = system.qos_class_self() or DEFAULT
    system.pthread_set_qos_class_self_np(qos, 0)
    try:
        yield
    finally:
        system.pthread_set_qos_class_self_np(before, 0)
