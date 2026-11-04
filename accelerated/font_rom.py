from itertools import combinations

def factor(font):
    w = len(font[' '][0])
    h = len(font[' '])
    rows = [set((ord(c) - 32 for c, g in font.items() if g[y][x] == '1')) for y in range(h) for x in range(w)]
    groups = []
    while True:
        candidates = {frozenset(a & b) for a, b in combinations(rows, 2)}
        best = None
        for group in candidates:
            if len(group) < 2:
                continue
            use = [i for i, row in enumerate(rows) if group <= row]
            gain = len(group) * (len(use) - 1) - len(use) - 4
            key = (gain, len(group), tuple(sorted(group)))
            if best is None or key > best[0]:
                best = (key, group, use)
        if best is None or best[0][0] <= 0:
            break
        _, group, use = best
        node = 64 + len(groups)
        groups.append(tuple(sorted(group)))
        for i in use:
            rows[i].difference_update(group)
            rows[i].add(node)
    return (groups, rows)

def declarations(count, font):
    groups, rows = factor(font)
    return '\n'.join((f'signal font_union_{i}:bit_vector({len(groups) - 1} downto 0); signal font_out_{i}:bit_vector({len(rows) - 1} downto 0);' for i in range(count)))

def decoder(cell, font):
    groups, rows = factor(font)
    s = ''
    for c in range(64):
        expr = f'chars_value({cell}) = bits({c},6)' if chr(c + 32) in font and c else "'0'"
        s += f'  glyph_select({cell})({c}) <= {expr};\n'

    def node(n):
        return f'glyph_select({cell})({n})' if n < 64 else f'font_union_{cell}({n - 64})'
    for i, pair in enumerate(groups):
        s += f'  font_union_{cell}({i}) <= or_reduce(' + ' & '.join((node(n) for n in pair)) + ');\n'
    for i, row in enumerate(rows):
        expr = 'or_reduce(' + ' & '.join((node(n) for n in sorted(row))) + ')' if row else "'0'"
        s += f'  font_out_{cell}({i}) <= {expr};\n'
    return s
