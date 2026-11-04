"""Fast drop-in for NativeLogicSimulator's run-and-read API, backed by the Rust library in this folder.

Build it with `cargo build --release` here. `make_simulator` falls back to the Python simulator if the
library has not been built. The blueprint is still loaded and checked by NativeLogicSimulator, which stays
the authority on the model; `verify.py` compares the two.
"""
import ctypes
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'Logic-Block-Simulator' / 'hdl_workshop'))
from native_simulator import NativeLogicSimulator  # noqa: E402

LIBRARY = HERE / 'target' / 'release' / 'fast_sim.dll'
_MODE = {'AND': 0, 'OR': 1, 'XOR': 2, 'NAND': 3, 'NOR': 4, 'XNOR': 5, 'INPUT': 6, 'TIMER': 7}


def _library():
    lib = ctypes.CDLL(str(LIBRARY))
    u8p, u32p = ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint32)
    lib.sim_new.restype = ctypes.c_void_p
    lib.sim_new.argtypes = [ctypes.c_size_t, u8p, u8p, u32p, u32p, u32p]
    lib.sim_free.argtypes = [ctypes.c_void_p]
    lib.sim_time.restype = ctypes.c_uint64
    lib.sim_time.argtypes = [ctypes.c_void_p]
    lib.sim_request.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint64]
    lib.sim_advance.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    lib.sim_read.argtypes = [ctypes.c_void_p, u32p, ctypes.c_size_t, u8p]
    lib.sim_values.argtypes = [ctypes.c_void_p, u8p]
    return lib


class FastLogicSimulator:
    def __init__(self, blueprint):
        model = NativeLogicSimulator(blueprint, input_mode='controller')
        self.order = sorted(model.nodes)
        self.index = {identifier: i for i, identifier in enumerate(self.order)}
        n = len(self.order)
        kinds = (ctypes.c_uint8 * n)(*(_MODE[model.kinds[i]] for i in self.order))
        values = (ctypes.c_uint8 * n)(*(int(model.values[i]) for i in self.order))
        delays = (ctypes.c_uint32 * n)(*(model.timer_states[i].delay if i in model.timer_states else 0 for i in self.order))
        starts, edges = [0], []
        for identifier in self.order:
            edges += [self.index[p] for p in model.parents[identifier]]
            starts.append(len(edges))
        self._lib = _library()
        self._sim = self._lib.sim_new(n, kinds, values, delays, (ctypes.c_uint32 * len(starts))(*starts), (ctypes.c_uint32 * max(1, len(edges)))(*edges))
        self._reads = {}

    def __del__(self):
        if getattr(self, '_sim', None):
            self._lib.sim_free(self._sim)

    @property
    def time(self):
        return self._lib.sim_time(self._sim)

    def request_input(self, identifier, value, *, at_tick=None):
        at = self.time + 1 if at_tick is None else at_tick
        if at <= self.time:
            raise ValueError('Input requests target a future commit tick')
        self._lib.sim_request(self._sim, self.index[identifier], int(bool(value)), at)

    def advance(self, updates):
        self._lib.sim_advance(self._sim, updates)

    def read_ids(self, bits):
        key = tuple(bits)
        prepared = self._reads.get(key)
        if prepared is None:
            prepared = self._reads[key] = ((ctypes.c_uint32 * len(key))(*(self.index[i] for i in key)), (ctypes.c_uint8 * ((len(key) + 7) // 8))())
        nodes, out = prepared
        self._lib.sim_read(self._sim, nodes, len(key), out)
        return int.from_bytes(out, 'little')

    def values(self):
        out = (ctypes.c_uint8 * len(self.order))()
        self._lib.sim_values(self._sim, out)
        return dict(zip(self.order, map(bool, out)))


def make_simulator(blueprint):
    if LIBRARY.exists():
        return FastLogicSimulator(blueprint)
    print('fast_sim not built; using the slower Python simulator (run cargo build --release in fast_sim/)', file=sys.stderr)
    return NativeLogicSimulator(blueprint, input_mode='controller')
