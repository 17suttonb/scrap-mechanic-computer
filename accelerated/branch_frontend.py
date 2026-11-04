def branch_compare_source():
    lines = ['entity rv_branch_compare is\n  port(a,b:in bit_vector(31 downto 0); funct3:in bit_vector(2 downto 0); taken:out bit);\nend;\narchitecture gates of rv_branch_compare is\n  signal same,less:bit_vector(31 downto 0);\n  signal group_same,group_less:bit_vector(7 downto 0);\n  signal eq,ult,slt:bit;\nbegin\n  same <= a xnor b; less <= (not a) and b;\n']

    def product(values):
        return 'and_reduce(' + ' & '.join(values) + ')'
    for group in range(8):
        base = group * 4
        terms = [product([f'same({j})' for j in range(base + 3, k, -1)] + [f'less({k})']) for k in range(base, base + 4)]
        lines += [f'  group_same({group}) <= and_reduce(same({base + 3} downto {base}));', f'  group_less({group}) <= or_reduce(' + ' & '.join(terms) + ');']
    terms = [product([f'group_same({j})' for j in range(7, k, -1)] + [f'group_less({k})']) for k in range(8)]
    lines.append('  ult <= or_reduce(' + ' & '.join(terms) + ');')
    lines.append('  eq <= and_reduce(group_same);\n  slt <= (a(31) and not b(31)) or (same(31) and ult);\n  taken <= eq when funct3 = bits(0,3) else not eq when funct3 = bits(1,3) else\n           slt when funct3 = bits(4,3) else not slt when funct3 = bits(5,3) else\n           ult when funct3 = bits(6,3) else not ult;\nend;\n')
    return '\n'.join(lines)
