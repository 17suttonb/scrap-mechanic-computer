from pathlib import Path
from functools import partial
import hashlib
from dataclasses import replace
import shutil
import uuid
from accelerated.compaction import compact_build
from accelerated.isa import assemble
from accelerated.cpu import cpu
from accelerated.tap_terminal import terminal
from accelerated.system import compile_system
from accelerated.export_system import export_async
from accelerated.cpu_build import terminal_cpu
from accelerated.complete_console import complete_service
from accelerated.optimise import compact
from accelerated.packet import prepare, Encoder
from accelerated.timing import retime
from accelerated.split import prototype
from accelerated.layout import build as layout
from accelerated.io import read, save
from accelerated.cartridge import combined_image, build as build_cartridge

def build_current(package_root, output, work, flat=False, beta=False, fast_shifts=False, key_queue=0, cartridge=None):
    package_root = Path(package_root).resolve()
    recipe = read(package_root / 'release.json')
    work = Path(work).resolve() / str(uuid.uuid4())
    (work / 'exports').mkdir(parents=True)
    program = assemble((package_root / 'firmware/basic.s').read_text(encoding='utf-8'), compact_li=beta)
    if hashlib.sha256(program.image).hexdigest() != recipe['firmware_sha256'] and not beta:
        raise ValueError('Firmware differs from the current release')
    cart_module = None
    if cartridge is not None:
        if not beta:
            raise ValueError('A cartridge needs the unpinned --beta build')
        cartridge = Path(cartridge).read_bytes()
        program = replace(program, image=combined_image(program.image, cartridge))
        cart_module = build_cartridge(cartridge)
    display = terminal(tuple(recipe['boot_text']), help_text=tuple(recipe['help_text']), columns=12, latch_style='pulse_xor', expose_length=True, key_queue=key_queue)
    factory = partial(cpu, data_rom=False, beta=beta, fast_shifts=fast_shifts, cartridge=cartridge)
    result = compile_system(program.image, factory, complete_service, display, cart_module)
    cart_ports = tuple(name for name in result.ports if name.startswith('cart_'))
    result = compact_build(terminal_cpu(result, ('service_capture', 'service_commit', 'keys', 'terminal_pixels', 'busy', 'full') + cart_ports), max_consumers=2, beta=beta)
    result = compact(result)
    result.circuit.name = 'RV32E - private resident text core'
    result.report['terminal_title'] = 'FAST MODULAR'
    folder = export_async(result, program, work / 'exports', recipe['cpu_ticks'], 7)
    retime(folder, recipe['cpu_ticks'], [0, 8, 0, 18])
    terminal_map = read(folder / 'terminal-map.json')
    terminal_map.update(fast_console=True, ascii_input=True, complete_console=True)
    save(folder / 'terminal-map.json', terminal_map)
    save(folder / 'program-timers.json', dict(bindings=[], words=0, frame_updates=0))
    record = dict(folder=str(folder.resolve()), blueprint_sha256=hashlib.sha256((folder / 'blueprint.json').read_bytes()).hexdigest())
    record = prepare(record, work, recipe['source_packet_orders'])
    folder = Path(record['folder'])
    retime(folder, recipe['cpu_ticks'], recipe['service_ticks'])
    orders = recipe['release_packet_orders']
    raw = Encoder(read(folder / 'blueprint.json')).encode(orders['child_order'], orders['controller_order'], orders['pos_order'])
    (folder / 'blueprint.json').write_bytes(raw)
    record.update(blueprint_sha256=hashlib.sha256(raw).hexdigest(), orders=orders)
    release = work / 'release'
    prototype(record, release)
    layout(release, flat=flat)
    modules = read(release / 'modules.json')['modules']
    comparison = {}
    for key, module in modules.items():
        expected = recipe['modules'].get(key) or dict(blueprint_sha256=None, packet_bytes=None)
        comparison[key] = dict(expected=expected['blueprint_sha256'], rebuilt=module['blueprint_sha256'], identical=expected['blueprint_sha256'] == module['blueprint_sha256'])
        if flat or beta:
            # The flat panel layout is an unpinned variant: only the import limit applies.
            if not module['packet_bytes'] < 524288:
                raise ValueError(f'{key} packet size exceeds the import limit')
            continue
        if not comparison[key]['identical']:
            raise ValueError(f'{key} blueprint differs from the current release')
        if not expected['packet_bytes'] == module['packet_bytes'] < 524288:
            raise ValueError(f'{key} packet size differs from the current release or exceeds the import limit')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for key, module in modules.items():
        dest = output / str(uuid.uuid4())
        shutil.copytree(module['folder'], dest)
        description = read(dest / 'description.json')
        description['localId'] = dest.name
        save(dest / 'description.json', description)
        shutil.copy2(package_root / ('cartridge/ASSEMBLY.md' if cart_module else 'ASSEMBLY.md'), dest / 'ASSEMBLY.md')
        print('EXPORTED', description['name'], dest, flush=True)
        comparison[key]['export_folder'] = str(dest)
    save(work / 'verification.json', comparison)
    print('FLAT PANEL VARIANT: layout differs from the pinned release by design' if flat else 'BETA: unpinned optimised build; hashes differ from the pinned release by design' if beta else 'IDENTICAL: both accelerated blueprint.json files match the pinned release', flush=True)
    return (comparison, work)
