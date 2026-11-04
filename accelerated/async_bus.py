from collections import Counter
from copy import deepcopy
from accelerated.hdl import Build

def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('CPU bus transformation expected one occurrence: ' + old[:90])
    return source.replace(old, new, 1)

def service_source(ram_bytes=32):
    if not 16 <= ram_bytes <= 256 or ram_bytes & ram_bytes - 1:
        raise ValueError('Power-of-two RAM size from 16 to 256 required')
    rb = ram_bytes.bit_length() - 1
    return f'entity rv_memory_service is\n  port(capture,commit,reset,bus_request,bus_write:in bit;\n       bus_address:in bit_vector({rb - 1} downto 0); bus_kind:in bit_vector(1 downto 0);\n       bus_write_data,keyboard_data:in bit_vector(7 downto 0);\n       bus_ack:out bit; bus_read_data:out bit_vector(7 downto 0);\n       gpio,ram_zero:out bit_vector(31 downto 0));\nend;\narchitecture gates of rv_memory_service is\n  type ram_t is array(0 to {ram_bytes - 1}) of bit_vector(7 downto 0);\n  type bytes_t is array(0 to 3) of bit_vector(7 downto 0);\n  signal ram:ram_t; signal gpio_bytes:bytes_t;\n  signal cap_en,com_en,request_one,request_one_stage,request_two,request_two_stage:bit;\n  signal token_stage,ack_q,fire_stage,write_stage:bit;\n  signal address_stage:bit_vector({rb - 1} downto 0); signal kind_stage:bit_vector(1 downto 0);\n  signal data_stage,read_mux,response:bit_vector(7 downto 0);\nbegin\n  cap_en <= capture and not commit and not reset;\n  com_en <= commit and not capture and not reset;\n  request_one_stage <= latch(bus_request,cap_en,reset); request_one <= latch(request_one_stage,com_en,reset);\n  request_two_stage <= latch(request_one,cap_en,reset); request_two <= latch(request_two_stage,com_en,reset);\n  fire_stage <= latch((request_one xnor request_two) and (request_two xor ack_q),cap_en,reset);\n  token_stage <= latch(request_two,cap_en,reset);\n  address_stage <= latch(bus_address,cap_en,reset);\n  kind_stage <= latch(bus_kind,cap_en,reset); write_stage <= latch(bus_write,cap_en,reset);\n  data_stage <= latch(bus_write_data,cap_en,reset);\n  memory_cells:for i in 0 to {ram_bytes - 1} generate\n    ram(i) <= latch(data_stage,com_en and fire_stage and write_stage and (kind_stage = bits(0,2)) and (address_stage = bits(i,{rb})),reset);\n  end generate;\n  gpio_cells:for i in 0 to 3 generate\n    gpio_bytes(i) <= latch(data_stage,com_en and fire_stage and write_stage and (kind_stage = bits(1,2)) and (address_stage(1 downto 0) = bits(i,2)),reset);\n  end generate;\n  read_mux <= ram(to_integer(address_stage)) when kind_stage = bits(0,2) else\n              gpio_bytes(to_integer(address_stage(1 downto 0))) when kind_stage = bits(1,2) else\n              keyboard_data when (kind_stage = bits(2,2)) and (address_stage(1 downto 0) = bits(0,2)) else x"00";\n  response <= latch(read_mux,com_en and fire_stage,reset);\n  ack_q <= latch(token_stage,com_en and fire_stage,reset);\n  bus_ack <= ack_q; bus_read_data <= response;\n  gpio <= gpio_bytes(3) & gpio_bytes(2) & gpio_bytes(1) & gpio_bytes(0);\n  ram_zero <= ram(3) & ram(2) & ram(1) & ram(0);\nend;\n'

def combine(cpu, service, terminal=None):
    """Join real output nodes to input pins; only the two clocks stay separate."""
    circuit = cpu.circuit.clone()
    maps = [{i: i for i in cpu.circuit.nodes}]
    builds = [cpu, service]
    if terminal:
        builds.append(terminal)
    for build in builds[1:]:
        mapping = {}
        for i, node in build.circuit.nodes.items():
            prefix = 'Service ' if build is service else 'Terminal '
            label = 'timing ' + prefix + node['label'][7:] if node['label'].startswith('timing ') else prefix + node['label']
            new = circuit.add(node['kind'], node['x'], node['y'], label)
            circuit.nodes[new]['initial'] = node['initial']
            mapping[i] = new
        for a, b in build.circuit.edges:
            circuit.connect(mapping[a], mapping[b])
        maps.append(mapping)
    pins = lambda module, name: [maps[module][i] for i in builds[module].ports[name]['node_ids']]
    replacement = {}

    def bind(target_module, target, source_module, source):
        targets, sources = (pins(target_module, target), pins(source_module, source))
        if len(targets) != len(sources):
            raise ValueError('Bus width mismatch')
        replacement.update(zip(targets, sources))
    for name in ('bus_request', 'bus_write', 'bus_address', 'bus_write_data', 'bus_kind'):
        bind(1, name, 0, name)
    for name in ('bus_ack', 'bus_read_data', 'gpio', 'ram_zero'):
        if cpu.ports[name]['mode'] == 'in':
            bind(0, name, 1, name)
    if 'input_pop' in cpu.ports:
        bind(1, 'input_pop', 0, 'input_pop')
        for name in ('input_cache_data', 'input_cache_token', 'input_cache_filled'):
            bind(0, name, 1, name)
    bind(1, 'reset', 0, 'reset')
    if terminal:
        bind(2, 'reset', 0, 'reset')
        bind(2, 'faulted', 0, 'faulted')
        bind(2, 'gpio', 1, 'gpio')
        if 'input_index' in cpu.ports:
            replacement.update(zip(pins(2, 'gpio')[24:29], pins(0, 'input_index')))
        bind(2, 'capture', 1, 'capture')
        bind(2, 'commit', 1, 'commit')
        bind(1, 'keyboard_data', 2, 'keyboard_data')
        bind(0, 'input_byte', 2, 'keyboard_data')
        if 'input_length' in cpu.ports:
            bind(0, 'input_length', 2, 'keyboard_length')
    else:
        bind(1, 'keyboard_data', 0, 'input_byte')

    def resolve(i):
        while i in replacement:
            i = replacement[i]
        return i
    circuit.edges = {(resolve(a), resolve(b)) for a, b in circuit.edges}
    circuit.remove(replacement)
    ports = deepcopy(cpu.ports)
    for name in ports:
        ports[name]['node_ids'] = [resolve(i) for i in pins(0, name)]
    for name in ('gpio', 'ram_zero'):
        ports[name]['mode'] = 'out'
    if terminal:
        ports['input_byte']['mode'] = 'out'
    for name in ('capture', 'commit'):
        ports['service_' + name] = dict(service.ports[name], node_ids=pins(1, name))
    if terminal:
        for name in ('keys', 'keyboard_data', 'terminal_pixels', 'busy', 'full'):
            ports[name] = dict(terminal.ports[name], node_ids=[resolve(i) for i in pins(2, name)])
    for p in ports.values():
        p['hidden'] = True
    outgoing = {}
    for a, b in circuit.edges:
        outgoing.setdefault(a, []).append(b)
    for source, targets in outgoing.items():
        if len(targets) <= 128:
            continue
        circuit.edges.difference_update(((source, target) for target in targets))
        for start in range(0, len(targets), 128):
            buffer = circuit.add('OR', 0, 0, 'Async phase buffer')
            circuit.connect(source, buffer)
            for target in targets[start:start + 128]:
                circuit.connect(buffer, target)
    circuit.validate()
    report = deepcopy(cpu.report)
    report.update(blocks=len(circuit.nodes), wires=len(circuit.edges), gates=dict(Counter((n['kind'] for n in circuit.nodes.values()))), storage_bits=sum((b.report['storage_bits'] for b in builds)), async_bus=dict(protocol='One outstanding bundled-data toggle request/ack; two token samples in each domain', service='RAM, GPIO, input byte' + (', keyboard and text display' if terminal else ''), rom='Local decoded ROM', reset='Both domains reset together'))
    report['async_bus']['core_retiming'] = 'Fetch settling, load-to-writeback settling, early ALU operands, direct byte equality, wide ALU/register selectors'
    if cpu.report.get('cpu', {}).get('local_word_ram'):
        report['async_bus'].update(service='GPIO and input byte' + (', keyboard and text display' if terminal else ''), RAM='CPU-clocked 32-bit local transfers with byte masks; no service transactions')
    if terminal and 'terminal_boot' in terminal.report:
        report['terminal_boot'] = terminal.report['terminal_boot']
    if terminal and 'terminal_help' in terminal.report:
        report['terminal_help'] = terminal.report['terminal_help']
    if terminal and 'terminal_geometry' in terminal.report:
        report['terminal_geometry'] = terminal.report['terminal_geometry']
    if terminal:
        for key in ('tap_keyboard', 'keyboard_ready_label'):
            if key in terminal.report:
                report[key] = deepcopy(terminal.report[key])
    return Build(circuit, ports, report, cpu.source + '\n' + service.source + ('\n' + terminal.source if terminal else ''))
