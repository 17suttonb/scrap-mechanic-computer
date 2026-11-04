def alu32_source(group_bits=4, *, operations=tuple(range(8))):
    if group_bits not in (4, 8):
        raise ValueError('Select four- or eight-bit carry groups')
    if not operations or len(set(operations)) != len(operations) or any((op not in range(8) for op in operations)):
        raise ValueError('Distinct ALU operations 0..7 required')
    groups = 32 // group_bits
    lines = [f'entity rv_alu32 is\n  port(a,b:in bit_vector(31 downto 0); carry_in:in bit;\n       operation:in bit_vector(2 downto 0);\n       result:out bit_vector(31 downto 0); carry_out:out bit);\nend;\narchitecture gates of rv_alu32 is\n  signal subtract:bit;\n  signal bb,p,g,sums:bit_vector(31 downto 0);\n  signal c:bit_vector(32 downto 0);\n  signal gp,gg:bit_vector({groups - 1} downto 0);\n  signal select_op:bit_vector(7 downto 0);\n  type results_t is array(0 to 7) of bit_vector(31 downto 0);\n  type carries_t is array(0 to 7) of bit;\n  signal values:results_t; signal carries:carries_t;\nbegin\n  subtract <= operation = bits(2,3);\n  bb <= b xor (others => subtract); p <= a xor bb; g <= a and bb;\n  c(0) <= carry_in;\n']

    def product(terms):
        return 'and_reduce(' + ' & '.join(terms) + ')'

    def local_terms(base, count):
        return [product([f'p({j})' for j in range(base + count - 1, k, -1)] + [f'g({k})']) for k in range(base, base + count)]
    for group in range(groups):
        base = group * group_bits
        lines.append(f'  gp({group}) <= and_reduce(p({base + group_bits - 1} downto {base}));')
        lines.append(f'  gg({group}) <= or_reduce(' + ' & '.join(local_terms(base, group_bits)) + ');')
    for group in range(1, groups + 1):
        terms = [product([f'gp({j})' for j in range(group - 1, k, -1)] + [f'gg({k})']) for k in range(group)]
        terms.append(product([f'gp({j})' for j in range(group)] + ['carry_in']))
        lines.append(f'  c({group * group_bits}) <= or_reduce(' + ' & '.join(terms) + ');')
    for group in range(groups):
        base = group * group_bits
        for count in range(1, group_bits):
            terms = local_terms(base, count)
            terms.append(product([f'p({j})' for j in range(base, base + count)] + [f'c({base})']))
            lines.append(f'  c({base + count}) <= or_reduce(' + ' & '.join(terms) + ');')
    lines.append("  add_bits:for i in 0 to 31 generate sums(i) <= p(i) xor c(i); end generate;\n  values(0) <= b; values(1) <= sums; values(2) <= sums;\n  values(3) <= a xor b; values(4) <= a or b; values(5) <= a and b;\n  values(6) <= a(30 downto 0) & carry_in;\n  values(7) <= carry_in & a(31 downto 1);\n  carries(0) <= '0'; carries(1) <= c(32); carries(2) <= c(32);\n  carries(3) <= '0'; carries(4) <= '0'; carries(5) <= '0';\n  carries(6) <= a(31); carries(7) <= a(0);\n")
    for op in range(8):
        expression = f'operation = bits({op},3)' if op in operations else "'0'"
        lines.append(f'  select_op({op}) <= {expression};')
    for bit in range(32):
        terms = [f'(select_op({op}) and values({op})({bit}))' for op in operations]
        lines.append(f'  result({bit}) <= or_reduce(' + ' & '.join(terms) + ');')
    lines.append('  carry_out <= or_reduce(' + ' & '.join((f'(select_op({op}) and carries({op}))' for op in operations)) + ');\nend;\n')
    return '\n'.join(lines)
