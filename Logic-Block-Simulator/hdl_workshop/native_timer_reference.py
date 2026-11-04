"""Slow literal TimerController oracle from the pinned 1.0.6.889 native bytes.

This is independent of the sparse simulator and uses a 304-byte buffer,
not event scheduling or a packed-integer optimization.  A compute call samples
the FIRST committed parent, ORs its high sample into bit N, peeks bit zero,
then shifts every byte.  A separate commit call publishes future output.

State loading is explicit.  The native constructor has empty history and
false current/future; JSON loading restores active plus *pending* settings,
and the full pending-settings initialization lifecycle is not established.
Supplying active=True here describes a native object in that state, not a
claim that every newly loaded blueprint reaches it before its first tick.

References: timer_compute-decompiled.txt, timer_reset-decompiled.txt,
timer_pending_apply_assembly.json and timer-native-notes.txt in investigation/game-logic.
This oracle covers authoritative/non-null-global logic state.  Native UI
occupancy masks, networking/interpolation and wall-clock scheduling are not
modeled.  No native runtime execution is implied.
"""
from dataclasses import dataclass, field
from typing import Iterable


BUFFER_BYTES = 0x130
BUFFER_BITS = BUFFER_BYTES * 8
MAX_SAFE_DELAY = BUFFER_BITS - 1


def delay_index(seconds: int, ticks: int) -> int:
    """Check raw byte fields and native allocation bounds, not UI limits.

    Native arithmetic is seconds*40+ticks from two uint8 fields.  The UI's
    complete permitted configuration range is not established.  Raw values
    whose resulting bit index exceeds 2431 address outside the timer's native
    history allocation; a safe reference model rejects that undefined case.
    Noncanonical but in-bounds fields such as (0,40) are intentionally allowed.
    """
    if type(seconds) is not int or type(ticks) is not int:
        raise ValueError('Timer fields must be integers, not booleans/floats')
    if not 0 <= seconds <= 255 or not 0 <= ticks <= 255:
        raise ValueError('Timer fields are raw unsigned bytes')
    index = seconds * 40 + ticks
    if index >= BUFFER_BITS:
        raise ValueError('Timer delay index exceeds native 304-byte allocation')
    return index


@dataclass
class TimerOracle:
    seconds: int = 0
    ticks: int = 0
    active: bool = False
    future: bool = False
    history: bytearray = field(default_factory=lambda: bytearray(BUFFER_BYTES))
    changed: bool = False
    dirty: bool = False
    authoritative: bool = True
    updates: int = 0

    def __post_init__(self):
        delay_index(self.seconds, self.ticks)
        if len(self.history) != BUFFER_BYTES:
            raise ValueError('Decoded native timer history must be 304 bytes')
        self.history = bytearray(self.history)
        self.active = bool(self.active)
        self.future = bool(self.future)

    @property
    def configured_delay(self) -> int:
        return delay_index(self.seconds, self.ticks)

    def compute(self, parents: Iterable[bool] = ()) -> bool:
        """Native slot38: update history; return uncommitted future output."""
        index = self.configured_delay
        parent = next(iter(parents), False)
        # 0x140c67b42..bbf: false input does NOT overwrite an existing 1.
        if parent:
            self.history[index // 8] |= 1 << (index % 8)
        # 0x140c67bd9..be0: peek before shifting.  N=0 sees this call's sample.
        if self.authoritative:
            self.future = bool(self.history[0] & 1)
        # 0x140c67c10..cb0: literal little-endian bytewise full-buffer shift.
        for byte in range(BUFFER_BYTES - 1):
            self.history[byte] = ((self.history[byte] >> 1)
                                  | ((self.history[byte + 1] & 1) << 7))
        self.history[-1] >>= 1
        self.updates += 1
        return self.future

    def commit(self) -> bool:
        """Native slot41: publish future; changed flag is sticky until cleared."""
        if self.authoritative:
            if self.active != self.future:
                self.changed = True
                self.dirty = True
            self.active = self.future
        return self.active

    def step(self, parents: Iterable[bool] = ()) -> bool:
        """One timer's compute+commit, with caller-supplied old parent states."""
        self.compute(parents)
        return self.commit()

    def clear_changed(self) -> None:
        """Native slot18 at 0x140c679d0 clears only the output-change flag."""
        self.changed = False

    def reset(self) -> bool:
        """Literal slot63 behavior; NOT equivalent to a low wired input.

        0x140c685f0..68658 does not explicitly clear future.  Its precise game
        lifecycle is unresolved, so callers should not silently invoke this
        merely because the simulated front-panel RESET switch is high.
        """
        if not self.authoritative or not (self.active or any(self.history)):
            return False
        self.history[:] = bytes(BUFFER_BYTES)
        self.active = False
        self.changed = True
        self.dirty = True
        return True

    def apply_settings(self, seconds: int, ticks: int) -> bool:
        """Native settings receiver: raw-field changes flush history/current.

        Equivalent combined delays with different raw bytes still count as
        changes.  Current false is forced without clearing future or explicitly
        setting the output-change flag in the observed native receiver body.
        """
        delay_index(seconds, ticks)
        changed = (seconds, ticks) != (self.seconds, self.ticks)
        self.seconds, self.ticks = seconds, ticks
        if changed:
            self.history[:] = bytes(BUFFER_BYTES)
            if self.active:
                self.dirty = True
            self.active = False
        return changed

    def set_raw_fields(self, seconds: int, ticks: int) -> None:
        """Internal-state test operation retaining history, NOT native UI apply.

        Useful to expose OR-preservation and unusual prefilled histories.  Do
        not use this method as the simulator's public settings-change behavior.
        """
        delay_index(seconds, ticks)
        self.seconds, self.ticks = seconds, ticks

    def restore_history(self, decoded: bytes) -> None:
        """Restore the explicitly decoded native 0x130-byte history payload.

        This does not infer active/current or settings from those bytes.  Stream
        save/restore methods handle the history separately from other state.
        """
        if len(decoded) != BUFFER_BYTES:
            raise ValueError('Decoded native timer history must be 304 bytes')
        self.history[:] = decoded

    def snapshot(self) -> dict:
        return dict(seconds=self.seconds, ticks=self.ticks,
                    active=self.active, future=self.future,
                    history=bytes(self.history), changed=self.changed,
                    dirty=self.dirty, authoritative=self.authoritative,
                    updates=self.updates)
