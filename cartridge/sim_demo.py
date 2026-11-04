"""Plug the demo cartridge into an export's CORE and CONSOLE, which are not rebuilt, and run both its programs.

Usage: Logic-Block-Simulator\\runtime\\python.exe cartridge\\sim_demo.py <export folder> <demo module folder>
The export is a folder of module blueprints from build.py, the demo module one from
`python -I cartridge/make_cartridge.py cartridge/demo/demo.json --blueprint <folder>`. Prints the hashes of the
CORE and CONSOLE it ran with.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bench_commands import Machine  # noqa: E402


def main():
    export, module = Path(sys.argv[1]), Path(sys.argv[2])
    ok = True
    with tempfile.TemporaryDirectory() as folder:
        for sub in sorted(p for p in export.iterdir() if (p / 'link-map.json').exists()):
            name = json.loads((sub / 'description.json').read_text(encoding='utf-8'))['name']
            if 'CARTRIDGE' in name:
                continue
            print(name, hashlib.sha256((sub / 'blueprint.json').read_bytes()).hexdigest())
            shutil.copytree(sub, Path(folder) / sub.name)
        shutil.copytree(module, Path(folder) / module.name)

        m = Machine(folder)
        assert m.menu_ready('2'), m.text()
        print('menu', m.text())
        ok = ok and m.text()[:2] == ['DEMO CART', '1 COUNTER'] and m.text()[2].startswith('2 E')
        m.start_program('1')
        m.sim.advance(3000)
        first = m.text()
        m.sim.advance(1600)
        second = m.text()
        print('COUNTER', first, second)
        ok = ok and first[0] == 'COUNT' and first[2] != second[2] and first[2].isdigit() and second[2].isdigit()

        m = Machine(folder)
        assert m.menu_ready('2'), m.text()
        m.start_program('2')
        for _ in range(200):
            m.sim.advance(40)
            if m.text()[0] == 'ECHO' and m.text()[2] == '>':
                break
        print('ECHO', m.text())
        seconds = m.command('HELLO', 4000)
        print(f'ECHO after HELLO, {seconds:.2f} s', m.text())
        ok = ok and 'HELLO' in ''.join(m.text()[2:]) and sum('HELLO' in row for row in m.text()) == 2
    print('PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
