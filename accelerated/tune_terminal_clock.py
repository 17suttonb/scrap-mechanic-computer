from copy import deepcopy
from accelerated.clock import native_clock_period_ticks
PHASES = ('capture', 'gap_after_capture', 'commit', 'gap_after_commit')

def configure_clock(clock, ticks):
    clock = deepcopy(clock)
    clock.pop('phase_ticks', None)
    clock.pop('phase_seconds', None)
    clock['phase_ticks_by_name'] = dict(zip(PHASES, ticks))
    clock['version'] = 2
    clock['nominal_microstep_ticks'] = native_clock_period_ticks(ticks)
    clock['nominal_microstep_seconds'] = clock['nominal_microstep_ticks'] / 40
    clock['timer_only_sum_ticks'] = sum(ticks)
    clock['period_model'] = 'Four configured delays + four native sampling updates + one NOR update'
    for timer, value in zip(clock['timers'], ticks):
        timer.update(seconds=value // 40, ticks=value % 40)
    return clock
