from accelerated.compaction import compact_build
from accelerated.async_bus import combine

def compile_system(image, cpu_factory, service_factory, terminal):
    cpu = cpu_factory(image, 64, rom_style='decode', debug_ports=False, specialize_rom=True, external_bus=True)
    return compact_build(combine(cpu, service_factory(64), terminal), max_consumers=2)
