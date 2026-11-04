from accelerated.hdl import compile_source
from accelerated.alu32 import alu32_source
from accelerated.branch_frontend import branch_compare_source
from accelerated.isa import signed
MASK = 4294967295

CART = None


def analysis_metadata(image):
    """Every ROM row as assembled: used by the register-width and value-range analyses."""
    rows = []
    for pc in range(0, len(image), 4):
        w = int.from_bytes(image[pc:pc + 4], 'little')
        op = w & 127
        f3 = w >> 12 & 7
        f7 = w >> 25
        rd = w >> 7 & 31
        ra = w >> 15 & 31
        rb = w >> 20 & 31
        r = op == 51
        i = op == 19
        load = op == 3
        store = op == 35
        branch = op == 99
        jump = op in (111, 103)
        upper = op in (55, 23)
        usesrd = r or i or load or jump or upper
        usesa = r or i or load or store or branch or (op == 103)
        usesb = r or store or branch
        legal = r and (f7 == 0 or (f7 == 32 and f3 in (0, 5))) or (i and (f3 not in (1, 5) or f7 == 0 or (f3 == 5 and f7 == 32))) or (load and f3 in (0, 1, 2, 4, 5)) or (store and f3 in (0, 1, 2)) or (branch and f3 in (0, 1, 4, 5, 6, 7)) or upper or (op == 111) or (op == 103 and f3 == 0) or (op == 15 and f3 == 0) or (w in (115, 1048691))
        legal = legal and (not (usesrd and rd >= 16 or (usesa and ra >= 16) or (usesb and rb >= 16)))
        imm = signed(w >> 20, 12)
        if store:
            imm = signed(w >> 25 << 5 | w >> 7 & 31, 12)
        if upper:
            imm = w & 4294963200
        target = pc + 4
        if branch:
            target = pc + signed(w >> 31 << 12 | (w >> 7 & 1) << 11 | (w >> 25 & 63) << 5 | (w >> 8 & 15) << 1, 13)
        if op == 111:
            target = pc + signed(w >> 31 << 20 | (w >> 12 & 255) << 12 | (w >> 20 & 1) << 11 | (w >> 21 & 1023) << 1, 21)
        subtract = r and f3 == 0 and (f7 == 32) or ((r or i) and f3 in (2, 3))
        aluop = 2 if subtract else {4: 3, 6: 4, 7: 5}.get(f3, 1) if r or i else 1
        rows.append(dict(pc=pc, word=w, ra=ra & 15 if usesa else 0, rb=rb & 15 if usesb else 0, rd=rd & 15 if usesrd else 0, imm=imm & MASK, target=target & MASK, link=pc + 4, legal=int(legal), load=int(load), store=int(store), branch=int(branch), jal=int(op == 111), jalr=int(op == 103), lui=int(op == 55), auipc=int(op == 23), immediate=int(i or load or store or (op == 103) or upper), f3=f3, aluop=aluop, shiftleft=int((r or i) and f3 == 1), shiftright=int((r or i) and f3 == 5), arithmetic=int(f7 == 32), compare=int((r or i) and f3 in (2, 3)), static_memory=int((load or store) and ra == 0), stop=int(w in (115, 1048691)), stopcause=11 if w == 115 else 3))
    return rows


def metadata(image):
    """Decode view: rows inside the CART (first, end) byte range are supplied by an external
    cartridge, so the baked-in decoder must know nothing about them, nor about the padding before them."""
    rows = analysis_metadata(image)
    if CART is None:
        return rows
    first, end = CART
    return [dict.fromkeys(r, 0) | dict(pc=r['pc'], link=r['pc'] + 4) if first <= r['pc'] < end or not r['word'] else r for r in rows]


def source(image, ram_bytes=32, *, data_rom=True):
    rows = metadata(image)
    n = len(rows)
    ib = max(1, (n - 1).bit_length())
    rb = ram_bytes.bit_length() - 1
    widths = {'ra': 4, 'rb': 4, 'rd': 4, 'imm': 32, 'target': 32, 'link': 32, 'word': 32, 'f3': 3, 'aluop': 3, 'stopcause': 4}
    flags = ['legal', 'load', 'store', 'branch', 'jal', 'jalr', 'lui', 'auipc', 'immediate', 'shiftleft', 'shiftright', 'arithmetic', 'compare', 'stop', 'static_memory']
    decl = '\n'.join((f'  signal d_{name}:bit_vector({bits - 1} downto 0);' for name, bits in widths.items()))
    decl += '\n' + '\n'.join((f'  signal d_{name}:bit;' for name in flags))
    lines = [alu32_source(4, operations=(1, 2, 3, 4, 5)), branch_compare_source(), f'entity rv32e_cpu is\n  port(capture,commit,reset,bus_ack:in bit; input_byte,bus_read_data:in bit_vector(7 downto 0);\n       gpio:in bit_vector(31 downto 0);\n       bus_request,bus_write:out bit; bus_address:out bit_vector({rb - 1} downto 0);\n       bus_kind:out bit_vector(1 downto 0);bus_write_data:out bit_vector(7 downto 0);\n       input_index:out bit_vector(4 downto 0);\n       halted,faulted:out bit; trap_cause:out bit_vector(3 downto 0);\n       pc,instruction,ram_zero,x3,x4,x15:out bit_vector(31 downto 0);retired:out bit_vector(15 downto 0));\nend;\narchitecture gates of rv32e_cpu is\n  type registers_t is array(0 to 15) of bit_vector(31 downto 0);\n  type ram_t is array(0 to {ram_bytes - 1}) of bit_vector(7 downto 0);\n  type shift_t is array(0 to 5) of bit_vector(31 downto 0);\n  signal regs:registers_t;signal ram,write_bytes:ram_t;signal store_size:bit_vector(1 downto 0);\n  signal select_pc:bit_vector({n - 1} downto 0);\n  signal select_a,select_b,select_write,write_stage:bit_vector(15 downto 0);\n  signal pc_q,pc_stage,next_pc,a,b,operand_b,alu_a,alu_y,result,result_stage,address,load_word,ram_word,rom_word:bit_vector(31 downto 0);\n  signal ret_q,ret_stage:bit_vector(15 downto 0);\n  signal shift_left,shift_right:shift_t;\n  signal carry,taken,unsigned_less,signed_less,cap_en,com_en,pc_valid,mem_aligned,mem_valid:bit;\n  signal region_ram,region_rom,region_input,region_gpio,external,complete,execute_ok,execute_stage:bit;\n  signal pending,pending_stage,launch,launch_stage,request_q,request_stage,ack_one,ack_one_stage,ack_two,ack_two_stage:bit;\n  signal slow_class,slow_q,slow_stage:bit;\n  signal stop_now,stop_stage,halt_q,cause_bad,fault_q,fault_stage:bit;\n  signal cause,cause_stage,cause_q:bit_vector(3 downto 0);\n  signal addr_stage:bit_vector({rb - 1} downto 0);signal data_stage:bit_vector(7 downto 0);signal kind_stage:bit_vector(1 downto 0);\n  signal index_q,index_stage:bit_vector(4 downto 0);signal pop_stage,ready_seen:bit;\n  signal store_stage:bit;signal mask_stage:bit_vector({ram_bytes - 1} downto 0);\n  signal store_data:bit_vector(31 downto 0);signal byte_value:bit_vector(7 downto 0);signal half_value:bit_vector(15 downto 0);\n{decl}\nbegin\n  cap_en <= capture and not commit and not reset;\n  com_en <= commit and not capture and not reset;\n  pc_valid <= not or_reduce(pc_q(31 downto {ib + 2}) & pc_q(1 downto 0)) and (resize(pc_q({ib + 1} downto 2),{ib + 1}) < bits({n},{ib + 1}));\n']
    for j in range(n):
        lines.append(f'  select_pc({j}) <= pc_q({ib + 1} downto 2) = bits({j},{ib});')

    def red(terms):

        def cat(items):
            if len(items) <= 128:
                return ' & '.join(items)
            mid = len(items) // 2
            return '(' + cat(items[:mid]) + ') & (' + cat(items[mid:]) + ')'
        return 'or_reduce(' + (cat(terms) if len(terms) > 256 else ' & '.join(terms)) + ')' if terms else "'0'"
    for key, bits in widths.items():
        for bit in range(bits):
            lines.append(f'  d_{key}({bit}) <= ' + red([f'select_pc({j})' for j, r in enumerate(rows) if r[key] >> bit & 1]) + ';')
    for key in flags:
        lines.append(f'  d_{key} <= ' + red([f'select_pc({j})' for j, r in enumerate(rows) if r[key]]) + ';')
    lines += ['  pc <= pc_q; instruction <= d_word; retired <= ret_q;\n  halted <= halt_q;faulted <= fault_q;trap_cause <= cause_q;\n  regs(0) <= bits(0,32); x3 <= regs(3);x4 <= regs(4);x15 <= regs(15);\n  alu_a <= bits(0,32) when d_lui else pc_q when d_auipc else a;\n  operand_b <= d_imm when d_immediate else b;\n  alu:entity work.rv_alu32 port map(a=>alu_a,b=>operand_b,carry_in=>d_aluop(1),operation=>d_aluop,result=>alu_y,carry_out=>carry);\n  compare:entity work.rv_branch_compare port map(a=>a,b=>b,funct3=>d_f3,taken=>taken);\n  unsigned_less <= not carry;\n  signed_less <= a(31) when a(31) /= operand_b(31) else not carry;\n  address <= d_imm when d_static_memory else alu_y;\n  region_ram <= address(31 downto ' + str(rb) + f""") = bits({4096 >> rb},{32 - rb});\n  region_rom <= {(f'address < bits({len(image)},32)' if data_rom else "'0'")};\n  region_input <= address(31 downto 3) = bits({4294967056 >> 3},29);\n  region_gpio <= address(31 downto 3) = bits({4294967040 >> 3},29);\n  mem_aligned <= (d_f3(1 downto 0) = bits(0,2)) or\n                 ((d_f3(1 downto 0) = bits(1,2)) and not address(0)) or\n                 ((d_f3(1 downto 0) = bits(2,2)) and (address(1 downto 0) = bits(0,2)));\n  mem_valid <= region_ram or (d_load and (region_rom or region_input)) or region_gpio;\n  external <= region_gpio and (d_load or d_store);\n  slow_class <= ((d_load or d_store) and not d_static_memory) or d_shiftleft or d_shiftright;\n  slow_stage <= latch(slow_class and not execute_ok,cap_en,reset);\n  slow_q <= latch(slow_stage,com_en,reset);\n  complete <= (not external or (pending and (ack_one xnor ack_two) and (ack_two xnor request_q))) and (not slow_class or slow_q);\n  launch <= external and not pending and not halt_q and not stop_now;\n  launch_stage <= latch(launch,cap_en,reset);\n  request_stage <= latch(not request_q,cap_en,reset);\n  request_q <= latch(request_stage,com_en and launch_stage,reset);\n  pending_stage <= latch((pending or launch) and not (pending and complete),cap_en,reset);\n  pending <= latch(pending_stage,com_en,reset);\n  ack_one_stage <= latch(bus_ack,cap_en,reset);ack_one <= latch(ack_one_stage,com_en,reset);\n  ack_two_stage <= latch(ack_one,cap_en,reset);ack_two <= latch(ack_two_stage,com_en,reset);\n  addr_stage <= latch(address({rb - 1} downto 0),cap_en,reset);\n  data_stage <= latch(b(7 downto 0),cap_en,reset);\n  kind_stage <= latch(bits(1,2),cap_en,reset);\n  bus_request <= request_q;bus_write <= d_store;bus_address <= addr_stage;bus_write_data <= data_stage;bus_kind <= kind_stage;\n  input_index <= index_q;\n  ready_seen <= latch(input_byte(7),com_en,reset);\n  index_stage <= latch(index_q + bits(1,5),cap_en,reset);\n  pop_stage <= latch(execute_ok and d_load and region_input and address(2) and ready_seen,cap_en,reset);\n  index_q <= latch(index_stage,com_en and pop_stage,reset or gpio(29));\n  byte_value <= '0' & input_byte(6 downto 0) when region_input and address(2) else ready_seen & bits(0,7) when region_input else\n                bus_read_data when region_gpio else ram_word(7 downto 0) when region_ram else rom_word(7 downto 0);\n  half_value <= ram_word(15 downto 0) when region_ram else rom_word(15 downto 0) when region_rom else bits(0,8) & byte_value;\n  load_word <= resize(byte_value,32) when (d_f3 = bits(4,3)) else\n               (bits(16777215,24) & byte_value) when (d_f3 = bits(0,3)) and byte_value(7) else\n               resize(byte_value,32) when d_f3 = bits(0,3) else\n               resize(half_value,32) when (d_f3 = bits(5,3)) else\n               (bits(65535,16) & half_value) when (d_f3 = bits(1,3)) and half_value(15) else\n               resize(half_value,32) when d_f3 = bits(1,3) else\n               ram_word when region_ram else rom_word when region_rom else resize(byte_value,32);\n  shift_left(0) <= a;shift_right(0) <= a;\n"""]
    for j in range(5):
        count = 1 << j
        lines.append(f'  shift_left({j + 1}) <= shift_left({j})({31 - count} downto 0) & bits(0,{count}) when operand_b({j}) else shift_left({j});')
        fill = f'(bits({(1 << count) - 1},{count}) when d_arithmetic and a(31) else bits(0,{count}))'
        lines.append(f'  shift_right({j + 1}) <= bits({(1 << count) - 1},{count}) & shift_right({j})(31 downto {count}) when operand_b({j}) and d_arithmetic and a(31) else bits(0,{count}) & shift_right({j})(31 downto {count}) when operand_b({j}) else shift_right({j});')
    lines += ["  result <= load_word when d_load else d_link when d_jal or d_jalr else\n            shift_left(5) when d_shiftleft else shift_right(5) when d_shiftright else\n            resize(unsigned_less,32) when d_compare and d_f3(0) else resize(signed_less,32) when d_compare else alu_y;\n  next_pc <= alu_y(31 downto 1) & '0' when d_jalr else d_target when d_jal or (d_branch and taken) else d_link;\n  cause <= bits(0,4) when or_reduce(pc_q(1 downto 0)) else bits(1,4) when not pc_valid else bits(2,4) when not d_legal else\n           d_stopcause when d_stop else bits(6,4) when d_store and not mem_aligned else bits(4,4) when d_load and not mem_aligned else\n           bits(7,4) when d_store and not mem_valid else bits(5,4) when d_load and not mem_valid else\n           bits(0,4);\n  cause_bad <= or_reduce(cause) or or_reduce(pc_q(1 downto 0)) or ((d_jal or d_jalr or (d_branch and taken)) and or_reduce(next_pc(1 downto 0)));\n  stop_now <= cause_bad and (not slow_class or slow_q);\n  stop_stage <= latch(stop_now,cap_en,reset);\n  cause_stage <= latch(cause,cap_en,reset);\n  halt_q <= latch('1',com_en and stop_stage,reset);\n  cause_q <= latch(cause_stage,com_en and stop_stage,reset);\n  fault_stage <= latch(stop_now and not d_stop,cap_en,reset);\n  fault_q <= latch('1',com_en and fault_stage,reset);\n  execute_ok <= complete and not halt_q and not stop_now;\n  execute_stage <= latch(execute_ok,cap_en,reset);\n  result_stage <= latch(result,cap_en,reset);pc_stage <= latch(next_pc,cap_en,reset);\n  pc_q <= latch(pc_stage,com_en and execute_stage,reset);\n  ret_stage <= latch(ret_q + bits(1,16),cap_en,reset);ret_q <= latch(ret_stage,com_en and execute_stage,reset);\n  store_stage <= latch(execute_ok and d_store and region_ram,cap_en,reset);\n  store_data <= latch(b,cap_en,reset);\n  store_size <= latch(d_f3(1 downto 0),cap_en,reset);\n"]
    for r in range(16):
        lines.append(f'  select_a({r}) <= d_ra = bits({r},4);select_b({r}) <= d_rb = bits({r},4);')
        lines.append(f'  select_write({r}) <= (d_rd = bits({r},4)) and execute_ok;write_stage({r}) <= latch(select_write({r}),cap_en,reset);')
        if r:
            lines.append(f'  regs({r}) <= latch(result_stage,com_en and write_stage({r}),reset);')
    for bit in range(32):
        for name, sel in (('a', 'a'), ('b', 'b')):
            lines.append(f'  {name}({bit}) <= ' + red([f'(select_{sel}({r}) and regs({r})({bit}))' for r in range(1, 16)]) + ';')
    for lane in range(4):
        for bit in range(8):
            terms = []
            for off in range(ram_bytes - lane):
                terms.append(f'((address({rb - 1} downto 0) = bits({off},{rb})) and ram({off + lane})({bit}))')
            lines.append(f'  ram_word({lane * 8 + bit}) <= ' + red(terms) + ';')
            terms = [f'((address = bits({off},32)) and ' + ("'1'" if image[off + lane] >> bit & 1 else "'0'") + ')' for off in range(len(image) - lane)] if data_rom else []
            lines.append(f'  rom_word({lane * 8 + bit}) <= ' + red(terms) + ';')
    for addr in range(ram_bytes):
        cond = []
        for lane in range(4):
            if addr >= lane:
                cond.append(f'((address({rb - 1} downto 0) = bits({addr - lane},{rb})) and ' + ("'1'" if lane == 0 else '(d_f3 = bits(2,3))' if lane >= 2 else '(d_f3 /= bits(0,3))') + ')')
        lines.append(f'  mask_stage({addr}) <= latch(' + red(cond) + ',cap_en,reset);')
        value = f'store_data({addr % 4 * 8 + 7} downto {addr % 4 * 8}) when store_size = bits(2,2) else store_data({addr % 2 * 8 + 7} downto {addr % 2 * 8}) when store_size = bits(1,2) else store_data(7 downto 0)'
        lines.append(f'  write_bytes({addr}) <= {value};')
        lines.append(f'  ram({addr}) <= latch(write_bytes({addr}),com_en and store_stage and mask_stage({addr}),reset);')
    lines.append('  ram_zero <= ram(3) & ram(2) & ram(1) & ram(0);\nend;')
    return '\n'.join(lines)

def compile_core(image, ram_bytes=32, **options):
    data_rom = options.get('data_rom', True)
    result = compile_source(source(image, ram_bytes, data_rom=data_rom), top='rv32e_cpu', wide_gates=True, latch_style='nand')
    result.report['cpu'] = dict(isa='RV32E ROM-predecoded experimental', ram_bytes=ram_bytes, rom_bytes=len(image), microstates={}, architecture='One CAP/COM instruction; console transactions stall PC', data_readable_ROM=data_rom)
    from accelerated.ack_capture import stabilize_ack, capture_network
    result = stabilize_ack(result, window=11, stretch=8)
    c = result.circuit
    data = result.ports['input_byte']['node_ids'][7]
    capture = result.ports['capture']['node_ids'][0]
    commit = result.ports['commit']['node_ids'][0]
    reset = result.ports['reset']['node_ids'][0]
    old_targets = [b for a, b in c.edges if a == data]

    def add(kind, label, initial):
        i = c.add(kind, 0, 0, label)
        c.nodes[i]['initial'] = initial
        return i
    off = add('NOR', 'timing READY not commit', False)
    c.connect(commit, off)
    enabled = add('AND', 'timing READY capture', False)
    c.connect(capture, enabled)
    c.connect(off, enabled)
    q, nodes = capture_network(add, c.connect, data, enabled, reset, window=11, stretch=8, label_prefix='timing READY ')
    for b in old_targets:
        c.edges.remove((data, b))
        c.edges.add((q, b))
    result.report['cpu']['ready_capture'] = dict(qualification_updates=11, stretch_updates=8, nodes=nodes)
    result.report['cpu']['console_contract'] = 'Byte MMIO; INPUT status exposes READY in bit 7; ASCII NEXT; one submitted line held until ACK'
    c.validate()
    return result
