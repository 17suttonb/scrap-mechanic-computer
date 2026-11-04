"""Run and verify the bundled 30-operation, 8x32-bit timer RAM example."""
import json
from pathlib import Path

from native_sim_cli import load, run


def main():
    root = Path(__file__).resolve().parent
    example = root / 'examples/timer-ram'
    sim, result = run(example, ticks=12000, stimuli=load(example / 'inputs.json'),
                      watches=('ready', 'done', 'passed', 'failed', 'progress'))
    output = root / 'results'
    output.mkdir(exist_ok=True)
    (output / 'demo.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    (output / 'demo.simstate.json').write_text(
        json.dumps(sim.snapshot(), indent=2) + '\n', encoding='utf-8')
    expected = dict(ready='0x1', done='0x1', passed='0x1', failed='0x0', progress='0x1e')
    success = result['signals'] == expected
    print('Timer RAM self-test: ' + ('PASS' if success else 'FAIL'))
    for name, value in result['signals'].items():
        print(f'  {name.upper()}: {int(value, 16)}')
    print(f'Trace and resumable snapshot: {output}')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
