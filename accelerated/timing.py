from accelerated.circuit import encode_blueprint
from accelerated.io import read, save

def settings(bp, clock, values):
    assert len(values) == 4 and all((type(n) is int and 0 <= n < 2400 for n in values))
    ids = {t['controllerId']: n for t, n in zip(clock['timers'], values)}
    for body in bp['bodies']:
        for part in body['childs']:
            c = part.get('controller', {})
            if c.get('id') in ids:
                value = ids[c['id']]
                c.update(seconds=value // 40, ticks=value % 40)

def retime(folder, cpu, service):
    bp = read(folder / 'blueprint.json')
    c = read(folder / 'clock-map.json')
    s = read(folder / 'service-clock-map.json')
    names = ('capture', 'gap_after_capture', 'commit', 'gap_after_commit')
    for clock, values in ((c, cpu), (s, service)):
        settings(bp, clock, values)
        clock.pop('phase_ticks', None)
        clock.pop('phase_seconds', None)
        clock['phase_ticks_by_name'] = dict(zip(names, values))
        for timer, value in zip(clock['timers'], values):
            timer.update(seconds=value // 40, ticks=value % 40)
        clock.update(experimental_zero_timer_settings=True, nominal_microstep_ticks=sum(values) + 5, nominal_microstep_seconds=(sum(values) + 5) / 40)
    s.update(nominal_service_ticks=sum(service) + 5, nominal_service_seconds=(sum(service) + 5) / 40)
    save(folder / 'clock-map.json', c)
    save(folder / 'service-clock-map.json', s)
    report = read(folder / 'hdl-build.json')
    report.update(clock=c, service_clock=s, pulse_experiment=True)
    save(folder / 'hdl-build.json', report)
    (folder / 'blueprint.json').write_bytes(encode_blueprint(bp))
