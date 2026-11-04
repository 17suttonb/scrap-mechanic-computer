from __future__ import annotations
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
import json
import re
import time
from accelerated.circuit import Circuit, DEFAULT_BLUEPRINTS
from accelerated import sections

@dataclass(frozen=True)
class Token:
    text: str
    line: int
    column: int

class HDLError(ValueError):

    def __init__(self, message, token=None):
        self.message = message
        self.line = token.line if token else 1
        self.column = token.column if token else 1
        super().__init__(f'Line {self.line}, column {self.column}: {message}')

def lex(source):
    pattern = re.compile('--[^\\n]*|\\s+|[xX]"[0-9a-fA-F_]+"|"[01_]+"|\\\'[01]\\\'|[A-Za-z][A-Za-z0-9_]*|[0-9][0-9_]*|<=|:=|=>|/=|>=|\\*\\*|[;:,().+*/=&<>\\-\\\']')
    tokens, pos, line, col = ([], 0, 1, 1)
    while pos < len(source):
        m = pattern.match(source, pos)
        if not m:
            raise HDLError(f'Unexpected character {source[pos]!r}; only binary logic values are supported.', Token('', line, col))
        text = m.group()
        if not text.isspace() and (not text.startswith('--')):
            tokens.append(Token(text.lower(), line, col))
        if '\n' in text:
            line += text.count('\n')
            col = len(text.rsplit('\n', 1)[1]) + 1
        else:
            col += len(text)
        pos = m.end()
    tokens.append(Token('<eof>', line, col))
    return tokens

@dataclass
class Expr:
    op: str
    args: tuple
    token: Token

class Parser:
    PRECEDENCE = {'or': 1, 'nor': 1, 'xor': 1, 'xnor': 1, 'and': 2, 'nand': 2, '=': 3, '/=': 3, '<': 3, '>': 3, '<=': 3, '>=': 3, 'sll': 4, 'srl': 4, '&': 5, '+': 5, '-': 5, '*': 6, '/': 6, 'mod': 6, '**': 7}

    def __init__(self, source):
        self.tokens = lex(source)
        self.index = 0

    @property
    def t(self):
        return self.tokens[self.index]

    def take(self):
        t = self.t
        self.index += 1
        return t

    def accept(self, text):
        if self.t.text == text:
            return self.take()
        return None

    def expect(self, text):
        if self.t.text != text:
            raise HDLError(f'Expected {text!r}, found {self.t.text!r}.', self.t)
        return self.take()

    def name(self):
        t = self.take()
        if not re.fullmatch('[a-z][a-z0-9_]*', t.text):
            raise HDLError('Expected an identifier.', t)
        return t

    def names(self):
        result = [self.name()]
        while self.accept(','):
            result.append(self.name())
        return result

    def expr(self, minimum=0):
        t = self.take()
        if t.text in ('not', '-', '+'):
            left = Expr('unary', (t.text, self.expr(8)), t)
        elif t.text == '(':
            if self.accept('others'):
                self.expect('=>')
                left = Expr('others', (self.expr(),), t)
            else:
                left = self.expr()
            self.expect(')')
        elif t.text[0:1].isdigit():
            left = Expr('int', (int(t.text.replace('_', '')),), t)
        elif t.text.startswith(('"', "'", 'x"')):
            if t.text.startswith('x"'):
                s = t.text[2:-1].replace('_', '')
                bits = f'{int(s, 16):0{len(s) * 4}b}'
            else:
                bits = t.text[1:-1].replace('_', '')
            left = Expr('bits', (bits,), t)
        elif re.fullmatch('[a-z][a-z0-9_]*', t.text):
            left = Expr('name', (t.text,), t)
        else:
            raise HDLError(f'Expected an expression, found {t.text!r}.', t)
        while True:
            if self.accept('('):
                args = []
                if self.t.text != ')':
                    first = self.expr()
                    if self.t.text in ('to', 'downto'):
                        direction = self.take().text
                        args = [Expr('range', (first, direction, self.expr()), first.token)]
                    else:
                        args = [first]
                        while self.accept(','):
                            args.append(self.expr())
                self.expect(')')
                left = Expr('call', (left, tuple(args)), left.token)
                continue
            if self.accept("'"):
                attr = self.name()
                left = Expr('attribute', (left, attr.text), attr)
                continue
            op = self.t.text
            precedence = self.PRECEDENCE.get(op, -1)
            if precedence < minimum:
                break
            operator = self.take()
            right = self.expr(precedence + (0 if op == '**' else 1))
            left = Expr('binary', (op, left, right), operator)
        return left

    def conditional(self):
        yes = self.expr()
        if self.accept('when'):
            condition = self.expr()
            self.expect('else')
            return Expr('when', (yes, condition, self.conditional()), yes.token)
        return yes

    def range(self):
        a = self.expr()
        if self.t.text not in ('to', 'downto'):
            raise HDLError('Expected to or downto in range.', self.t)
        direction = self.take().text
        b = self.expr()
        return (a, direction, b)

    def type_spec(self):
        name = self.name()
        bounds = None
        if self.accept('('):
            bounds = self.range()
            self.expect(')')
        return (name, bounds)

    def finish(self, category):
        self.expect('end')
        self.accept(category)
        if self.t.text != ';':
            self.name()
        self.expect(';')

    def declarations(self):
        declarations = []
        while self.t.text != 'begin':
            t = self.take()
            if t.text == 'type':
                n = self.name()
                self.expect('is')
                self.expect('array')
                self.expect('(')
                bounds = self.range()
                self.expect(')')
                self.expect('of')
                element = self.type_spec()
                self.expect(';')
                declarations.append(('type', n, bounds, element))
            elif t.text in ('signal', 'constant'):
                names = self.names()
                self.expect(':')
                spec = self.type_spec()
                initial = self.conditional() if self.accept(':=') else None
                self.expect(';')
                declarations.append((t.text, names, spec, initial))
            else:
                raise HDLError('Expected signal, constant, type, or begin. Processes and functions are outside this subset.', t)
        self.expect('begin')
        return declarations

    def associations(self):
        self.expect('map')
        self.expect('(')
        result = {}
        if self.t.text != ')':
            while True:
                n = self.name()
                self.expect('=>')
                if n.text in result:
                    raise HDLError(f'Duplicate mapping for {n.text}.', n)
                result[n.text] = self.conditional()
                if not self.accept(','):
                    break
        self.expect(')')
        return result

    def body(self):
        result = []
        while self.t.text != 'end':
            if self.t.text == '<eof>':
                raise HDLError('Missing end for architecture or generate block.', self.t)
            if self.t.text in ('process', 'with'):
                raise HDLError('Use concurrent <= assignments and latch(data, enable[, reset]); processes and with/select are not supported.', self.t)
            start = self.t
            if self.tokens[self.index + 1].text == ':':
                label = self.name()
                self.expect(':')
                if self.accept('for'):
                    var = self.name()
                    self.expect('in')
                    bounds = self.range()
                    self.expect('generate')
                    body = self.body()
                    self.finish('generate')
                    result.append(('generate', label, var, bounds, body))
                elif self.accept('if'):
                    condition = self.expr()
                    self.expect('generate')
                    body = self.body()
                    self.finish('generate')
                    result.append(('ifgenerate', label, condition, body))
                else:
                    self.accept('entity')
                    entity = self.name()
                    if self.accept('.'):
                        if entity.text != 'work':
                            raise HDLError('Only entities in work are supported.', entity)
                        entity = self.name()
                    generics = self.associations() if self.accept('generic') else {}
                    self.expect('port')
                    ports = self.associations()
                    self.expect(';')
                    result.append(('instance', label, entity, generics, ports))
            else:
                target = self.reference()
                self.expect('<=')
                value = self.conditional()
                self.expect(';')
                result.append(('assign', start, target, value))
        return result

    def reference(self):
        t = self.name()
        target = Expr('name', (t.text,), t)
        while self.accept('('):
            a = self.expr()
            if self.t.text in ('to', 'downto'):
                direction = self.take().text
                a = Expr('range', (a, direction, self.expr()), a.token)
            self.expect(')')
            target = Expr('call', (target, (a,)), t)
        return target

    def parse(self):
        entities, architectures = ({}, {})
        while self.t.text != '<eof>':
            if self.t.text in ('library', 'use'):
                while self.t.text not in (';', '<eof>'):
                    self.take()
                self.expect(';')
            elif self.accept('entity'):
                name = self.name()
                if name.text in entities:
                    raise HDLError('Duplicate entity.', name)
                self.expect('is')
                generic, ports = ([], [])
                if self.accept('generic'):
                    self.expect('(')
                    while self.t.text != ')':
                        names = self.names()
                        self.expect(':')
                        kind = self.name()
                        if kind.text not in ('integer', 'natural', 'positive'):
                            raise HDLError('Generics must be integer, natural or positive.', kind)
                        default = self.expr() if self.accept(':=') else None
                        generic.extend(((n, kind.text, default) for n in names))
                        if not self.accept(';'):
                            break
                    self.expect(')')
                    self.expect(';')
                if self.accept('port'):
                    self.expect('(')
                    while self.t.text != ')':
                        names = self.names()
                        self.expect(':')
                        mode = self.take()
                        if mode.text not in ('in', 'out'):
                            raise HDLError('Only in and out ports are supported.', mode)
                        spec = self.type_spec()
                        ports.extend(((n, mode.text, spec) for n in names))
                        if not self.accept(';'):
                            break
                    self.expect(')')
                    self.expect(';')
                self.finish('entity')
                entities[name.text] = dict(token=name, generics=generic, ports=ports)
            elif self.accept('architecture'):
                self.name()
                self.expect('of')
                entity = self.name()
                if entity.text in architectures:
                    raise HDLError('Only one architecture per entity is supported.', entity)
                self.expect('is')
                declarations = self.declarations()
                body = self.body()
                self.finish('architecture')
                architectures[entity.text] = (declarations, body)
            else:
                raise HDLError('Expected entity, architecture, library, or use.', self.t)
        for name, entity in entities.items():
            if name not in architectures:
                raise HDLError(f'Entity {name} has no architecture.', entity['token'])
            entity['declarations'], entity['body'] = architectures[name]
        for name in architectures:
            if name not in entities:
                raise HDLError(f'Architecture references missing entity {name}.')
        if not entities:
            raise HDLError('No entity found.')
        return entities

@dataclass(frozen=True)
class Bits:
    nets: tuple
    indices: tuple = ()

    def __len__(self):
        return len(self.nets)

@dataclass
class Array:
    items: dict
    indices: tuple

@dataclass(frozen=True)
class HDLType:
    indices: tuple
    element: 'HDLType | None' = None

def indices(a, direction, b):
    if abs(a) > 100000 or abs(b) > 100000:
        raise HDLError('Range endpoints exceed 100,000.')
    return tuple(range(a, b + (1 if direction == 'to' else -1), 1 if direction == 'to' else -1))

class Builder:

    def __init__(self, limit=45000):
        self.next_net = 2
        self.gates, self.refs, self.aliases, self.cache = ({}, {}, {}, {})
        self.limit = limit
        self.latch_count = 0
        self.storage = set()
        self.wide_gates = False
        self.latch_style = 'nor'
        self.section = None

    def allocate(self):
        if self.next_net > self.limit * 4:
            raise HDLError('Design exceeds the elaboration limit; reduce generic sizes.')
        n = self.next_net
        self.next_net += 1
        return n

    def reference(self, label, token, readonly=False):
        n = self.allocate()
        self.refs[n] = (label, token, readonly)
        return n

    def drive(self, targets, values, token):
        if not isinstance(targets, Bits) or not isinstance(values, Bits) or len(targets) != len(values):
            raise HDLError('Assignment width mismatch.', token)
        for target, source in zip(targets.nets, values.nets):
            if target not in self.refs or self.refs[target][2]:
                raise HDLError('Assignment target must be a writable signal or output port.', token)
            if target in self.aliases:
                raise HDLError(f'Multiple drivers for {self.refs[target][0]}.', token)
            self.aliases[target] = source

    def raw(self, kind, sources=(), label='', initial=False, token=None):
        if len(self.gates) >= self.limit:
            raise HDLError(f'Design exceeds {self.limit:,} logic blocks before fan-out buffers.', token)
        n = self.allocate()
        self.gates[n] = dict(kind=kind, sources=tuple(sources), label=label or (f'{kind} @{self.section}' if self.section else kind), initial=initial, token=token)
        return n

    def gate(self, kind, a, b=None):
        if kind == 'NOT':
            if a in (0, 1):
                return 1 - a
            return self.cached('NOR', (a,))
        if a == b:
            if kind in ('XOR', 'XNOR'):
                return int(kind == 'XNOR')
            return self.gate('NOT', a) if kind in ('NAND', 'NOR') else a
        if a in (0, 1) and b in (0, 1):
            return int({'AND': a & b, 'OR': a | b, 'XOR': a ^ b, 'NAND': 1 - (a & b), 'NOR': 1 - (a | b), 'XNOR': 1 - (a ^ b)}[kind])
        if a in (0, 1) or b in (0, 1):
            const, other = (a, b) if a in (0, 1) else (b, a)
            if kind in ('AND', 'NAND'):
                result = other if const else 0
            elif kind in ('OR', 'NOR'):
                result = 1 if const else other
            else:
                result = self.gate('NOT', other) if const else other
            return self.gate('NOT', result) if kind in ('NAND', 'NOR', 'XNOR') else result
        return self.cached(kind, tuple(sorted((a, b))))

    def cached(self, kind, sources):
        key = (kind, sources)
        if key not in self.cache:
            self.cache[key] = self.raw(kind, sources)
        return self.cache[key]

    def reduce(self, kind, nets):
        nets = list(nets)
        if not nets:
            return 1 if kind == 'AND' else 0
        if self.wide_gates and kind in ('AND', 'OR'):
            if kind == 'AND' and 0 in nets or (kind == 'OR' and 1 in nets):
                return int(kind == 'OR')
            identity = 1 if kind == 'AND' else 0
            nets = sorted(set(nets) - {identity})
            if not nets:
                return identity
            while len(nets) > 128:
                nets = [self.cached(kind, tuple(nets[i:i + 128])) if len(nets[i:i + 128]) > 1 else nets[i] for i in range(0, len(nets), 128)]
            return self.cached(kind, tuple(nets)) if len(nets) > 1 else nets[0]
        while len(nets) > 1:
            nets = [self.gate(kind, nets[i], nets[i + 1]) if i + 1 < len(nets) else nets[i] for i in range(0, len(nets), 2)]
        return nets[0]

    def mux(self, condition, yes, no):
        if condition in (0, 1):
            return yes if condition else no
        if yes == no:
            return yes
        return self.gate('OR', self.gate('AND', condition, yes), self.gate('AND', self.gate('NOT', condition), no))

    def add(self, a, b, subtract=False):
        carry, output = (int(subtract), [])
        for x, y in zip(a, b):
            if subtract:
                y = self.gate('NOT', y)
            p = self.gate('XOR', x, y)
            output.append(self.gate('XOR', p, carry))
            carry = self.gate('OR', self.gate('AND', x, y), self.gate('AND', p, carry))
        return tuple(output)

    def latch(self, data, enable, reset, token):
        self.latch_count += len(data)
        if self.latch_style == 'pulse_xor':
            from accelerated.pulse_storage import pulse_latches
            return Bits(tuple(pulse_latches(self, data, enable, reset, token)))
        q = []
        if self.latch_style == 'nand':
            disabled = self.gate('NOT', enable)
            running = self.gate('NOT', reset)
            for bit, d in enumerate(data):
                set_bar = self.gate('NOT', self.reduce('AND', (d, enable, running))) if reset != 0 else self.gate('NAND', d, enable)
                reset_bar = self.gate('OR', d, disabled)
                value = self.raw('NAND', label=f'latch Q line {token.line} bit {bit}', token=token)
                sources = (reset_bar, value, running) if reset != 0 else (reset_bar, value)
                opposite = self.raw('NAND', sources, f'latch /Q line {token.line} bit {bit}', True, token)
                self.gates[value]['sources'] = (set_bar, opposite)
                self.storage.update((value, opposite))
                q.append(value)
            return Bits(tuple(q))
        for bit, d in enumerate(data):
            s = self.gate('AND', d, enable)
            r = self.gate('AND', self.gate('NOT', d), enable)
            if reset != 0:
                s = self.gate('AND', s, self.gate('NOT', reset))
                r = self.gate('OR', r, reset)
            value = self.raw('NOR', label=f'latch Q line {token.line} bit {bit}', token=token)
            opposite = self.raw('NOR', (s, value), f'latch /Q line {token.line} bit {bit}', True, token)
            self.gates[value]['sources'] = (r, opposite)
            self.storage.update((value, opposite))
            q.append(value)
        return Bits(tuple(q))

    def materialize(self, name, ports):
        resolved = {0: 0, 1: 1}

        def resolve(n):
            trail, visiting = ([], set())
            while n not in resolved:
                if n in visiting:
                    label, token, _ = self.refs[n]
                    raise HDLError(f'Alias feedback at {label}; use latch() for storage.', token)
                visiting.add(n)
                trail.append(n)
                if n in self.refs:
                    if n not in self.aliases:
                        label, token, _ = self.refs[n]
                        raise HDLError(f'Signal {label} is not driven.', token)
                    n = self.aliases[n]
                else:
                    resolved[n] = n
            result = resolved[n]
            for item in trail:
                resolved[item] = result
            return result
        for n in self.refs:
            resolve(n)
        roots = []
        for port in ports.values():
            port['nets'] = [resolve(n) for n in port['nets']]
            roots.extend(port['nets'])
        reachable, pending = (set(), list(roots))
        while pending:
            n = resolve(pending.pop())
            if n in reachable:
                continue
            reachable.add(n)
            if n in self.gates:
                sources = tuple((resolve(x) for x in self.gates[n]['sources']))
                self.gates[n]['sources'] = sources
                pending.extend(sources)
        degree, children = ({}, defaultdict(list))
        for n in reachable:
            if n in (0, 1):
                degree[n] = 0
                continue
            sources = () if n in self.storage else self.gates[n]['sources']
            degree[n] = len(set(sources))
            for source in set(sources):
                children[source].append(n)
        ready = deque((n for n, d in degree.items() if d == 0))
        visited = 0
        while ready:
            n = ready.popleft()
            visited += 1
            for child in children[n]:
                degree[child] -= 1
                if degree[child] == 0:
                    ready.append(child)
        if visited != len(reachable):
            raise HDLError('Combinational feedback detected. Use latch() for deliberate storage.')
        circuit = Circuit(name)
        physical = {}
        if reachable & {0, 1}:
            physical[0] = circuit.add('OR', 0, 0, 'constant 0')
        if 1 in reachable:
            physical[1] = circuit.add('NOR', 0, 0, 'constant 1')
            circuit.connect(physical[0], physical[1])
            circuit.nodes[physical[1]]['initial'] = True
        for n in sorted(reachable - {0, 1}):
            g = self.gates[n]
            physical[n] = circuit.add(g['kind'], 0, 0, g['label'])
            circuit.nodes[physical[n]]['initial'] = g['initial']
        for n in sorted(reachable - {0, 1}):
            g = self.gates[n]
            sources = g['sources']
            if g['kind'] in ('XOR', 'XNOR'):
                sources = tuple((s for s, count in Counter(sources).items() if count % 2))
                if not sources:
                    if g['kind'] == 'XNOR':
                        zero = physical.get(0)
                        if zero is None:
                            zero = circuit.add('OR', 0, 0, 'constant 0')
                            physical[0] = zero
                        circuit.nodes[physical[n]]['kind'] = 'NOR'
                        circuit.connect(zero, physical[n])
                    else:
                        circuit.nodes[physical[n]]['kind'] = 'OR'
                    continue
            for source in set(sources):
                circuit.connect(physical[source], physical[n])
        interface = {}
        for pname, p in ports.items():
            ids = [physical[n] for n in p['nets']]
            if p['mode'] == 'out':
                ids = []
                for index, n in zip(p['indices'], p['nets']):
                    i = circuit.add('OUTPUT', 0, 0, f'{pname}({index})')
                    circuit.connect(physical[n], i)
                    ids.append(i)
            interface[pname] = dict(mode=p['mode'], indices=list(p['indices']), node_ids=ids)
        out = defaultdict(list)
        for a, b in circuit.edges:
            out[a].append(b)
        buffered_edges = set()

        def distribute(source, targets):
            if len(targets) <= 128:
                buffered_edges.update(((source, target) for target in targets))
            else:
                size = max(128, (len(targets) + 127) // 128)
                for start in range(0, len(targets), size):
                    i = circuit.add('OR', 0, 0, 'fan-out buffer')
                    buffered_edges.add((source, i))
                    distribute(i, targets[start:start + size])
        for source, targets in out.items():
            if source in targets:
                buffered_edges.add((source, source))
                targets = [target for target in targets if target != source]
            distribute(source, sorted(targets))
        circuit.edges = buffered_edges
        for index, n in enumerate(circuit.nodes.values()):
            n.update(x=index % 32 * 180 + 80, y=index // 32 * 110 + 80)
        circuit.reset()
        circuit.validate()
        return (circuit, interface)

class Elaborator:

    def __init__(self, entities):
        self.entities = entities
        self.builder = Builder()
        self.scope_count = 0
        self.top_ports = {}
        self.generic_values = {}

    def const(self, expr, env):
        result = self.evaluate(expr, env)
        if type(result) is not int:
            raise HDLError('Expected a compile-time integer (generic, constant, or generate index).', expr.token)
        return result

    def bounds(self, spec, env):
        a, direction, b = spec
        values = indices(self.const(a, env), direction, self.const(b, env))
        if len(values) > 50000:
            raise HDLError('Range contains more than 50,000 elements.', a.token)
        return values

    def hdl_type(self, spec, env):
        name, bounds = spec
        if name.text in ('std_logic', 'bit'):
            if bounds:
                raise HDLError('Scalar type cannot have a range.', name)
            return HDLType((0,))
        if name.text in ('std_logic_vector', 'bit_vector', 'unsigned'):
            if not bounds:
                raise HDLError('Vector type requires an explicit range.', name)
            declared = self.bounds(bounds, env)
            if not declared:
                raise HDLError('Null-width vectors are not supported.', name)
            if len(declared) > 8192:
                raise HDLError('Vectors are limited to 8,192 bits.', name)
            return HDLType(tuple(reversed(declared)))
        kind = env.get(name.text)
        if not isinstance(kind, HDLType) or bounds:
            raise HDLError(f'Unknown or unsupported type {name.text}.', name)
        return kind

    def new_signal(self, kind, label, token, readonly=False):
        if kind.element:
            return Array({i: self.new_signal(kind.element, f'{label}({i})', token, readonly) for i in kind.indices}, kind.indices)
        return Bits(tuple((self.builder.reference(f'{label}({i})', token, readonly) for i in kind.indices)), kind.indices)

    def bits(self, value, token, width=None):
        if isinstance(value, Bits):
            if width is not None and len(value) != width:
                raise HDLError(f'Width mismatch: expected {width} bits, got {len(value)}. Use resize(..., width).', token)
            return value
        if type(value) is int and width is not None:
            if value < 0 or value >= 2 ** width:
                raise HDLError(f'Integer {value} does not fit in {width} unsigned bits.', token)
            return Bits(tuple((value >> i & 1 for i in range(width))))
        raise HDLError('Expected a bit or vector expression.', token)

    def bind_name(self, env, token, value):
        if token.text in env:
            raise HDLError(f'Duplicate declaration {token.text}.', token)
        env[token.text] = value

    def evaluate(self, e, env, width=None):
        op, args, t = (e.op, e.args, e.token)
        b = self.builder
        if op == 'int':
            return args[0]
        if op == 'bits':
            return Bits(tuple((int(bit) for bit in reversed(args[0]))))
        if op == 'name':
            if args[0] in ('true', 'false'):
                return int(args[0] == 'true')
            if args[0] not in env:
                raise HDLError(f'Unknown name {args[0]}.', t)
            return env[args[0]]
        if op == 'others':
            if width is None:
                raise HDLError('others requires a known destination width.', t)
            bit = self.bits(self.evaluate(args[0], env), t, 1)
            return Bits(bit.nets * width)
        if op == 'attribute':
            value = self.evaluate(args[0], env)
            ix = value.indices if isinstance(value, (Bits, Array)) else ()
            if not ix:
                raise HDLError('Attribute requires a named signal, vector or array.', t)
            attrs = {'length': len(ix), 'high': max(ix), 'low': min(ix)}
            if args[1] not in attrs:
                raise HDLError('Only length, high, and low attributes are supported.', t)
            return attrs[args[1]]
        if op == 'unary':
            operator, a = args
            av = self.evaluate(a, env, width)
            if operator in ('-', '+'):
                if type(av) is not int:
                    raise HDLError('Unary signs require a compile-time integer.', t)
                return -av if operator == '-' else av
            av = self.bits(av, t, width)
            return Bits(tuple((b.gate('NOT', n) for n in av.nets)))
        if op == 'when':
            yes, condition, no = args
            cv = self.evaluate(condition, env)
            if type(cv) is int:
                return self.evaluate(yes if cv else no, env, width)
            cv = self.bits(cv, t, 1).nets[0]
            av = self.evaluate(yes, env, width)
            bv = self.evaluate(no, env, width or (len(av) if isinstance(av, Bits) else None))
            effective = width or (len(av) if isinstance(av, Bits) else len(bv) if isinstance(bv, Bits) else None)
            if effective is None:
                raise HDLError('Cannot infer conditional vector width.', t)
            av, bv = (self.bits(av, t, effective), self.bits(bv, t, effective))
            return Bits(tuple((b.mux(cv, x, y) for x, y in zip(av.nets, bv.nets))))
        if op == 'binary':
            operator, a, other = args
            comparison = operator in ('=', '/=', '<', '<=', '>', '>=')
            context = None if comparison or operator == '&' else width
            av = self.evaluate(a, env, context)
            bv = self.evaluate(other, env, context or (len(av) if isinstance(av, Bits) and operator != '&' else None))
            if type(av) is int and type(bv) is int:
                if operator == '**' and (bv < 0 or bv > 24 or abs(av) > 65536):
                    raise HDLError('Exponentiation is limited to small compile-time integers.', t)
                if operator in ('/', 'mod') and bv == 0:
                    raise HDLError('Division by zero.', t)
                operations = {'+': lambda: av + bv, '-': lambda: av - bv, '*': lambda: av * bv, '/': lambda: int(av / bv), 'mod': lambda: av % bv, '**': lambda: av ** bv, '=': lambda: int(av == bv), '/=': lambda: int(av != bv), '<': lambda: int(av < bv), '<=': lambda: int(av <= bv), '>': lambda: int(av > bv), '>=': lambda: int(av >= bv), 'and': lambda: int(bool(av) and bool(bv)), 'or': lambda: int(bool(av) or bool(bv))}
                if operator in operations:
                    value = operations[operator]()
                    if abs(value) > 2 ** 32:
                        raise HDLError('Compile-time integer exceeds 32-bit bounds.', t)
                    return value
            if operator == '&':
                av, bv = (self.bits(av, t), self.bits(bv, t))
                return Bits(bv.nets + av.nets)
            if operator in ('sll', 'srl'):
                av = self.bits(av, t, width)
                shift = self.const(other, env)
                if shift < 0 or shift > 4096:
                    raise HDLError('Shift amount must be a constant between 0 and 4096.', t)
                shift = min(shift, len(av))
                return Bits((0,) * shift + av.nets[:len(av) - shift] if operator == 'sll' else av.nets[shift:] + (0,) * shift)
            effective = len(av) if isinstance(av, Bits) else len(bv) if isinstance(bv, Bits) else width
            if effective is None:
                raise HDLError('Cannot infer expression width.', t)
            av, bv = (self.bits(av, t, effective), self.bits(bv, t, effective))
            if operator in ('and', 'or', 'xor', 'nand', 'nor', 'xnor'):
                return Bits(tuple((b.gate(operator.upper(), x, y) for x, y in zip(av.nets, bv.nets))))
            if operator in ('+', '-'):
                return Bits(b.add(av.nets, bv.nets, operator == '-'))
            if comparison:
                equal = b.reduce('AND', (b.gate('XNOR', x, y) for x, y in zip(av.nets, bv.nets)))
                if operator in ('=', '/='):
                    return Bits((equal if operator == '=' else b.gate('NOT', equal),))
                less = 0
                for x, y in zip(av.nets, bv.nets):
                    less = b.gate('OR', b.gate('AND', b.gate('NOT', x), y), b.gate('AND', b.gate('XNOR', x, y), less))
                result = {'<': less, '<=': b.gate('OR', less, equal), '>': b.gate('NOT', b.gate('OR', less, equal)), '>=': b.gate('NOT', less)}[operator]
                return Bits((result,))
            raise HDLError(f'Operator {operator} requires compile-time integers or is unsupported for vectors.', t)
        if op == 'call':
            target, parameters = args
            if target.op == 'name' and target.args[0] in env:
                value = env[target.args[0]]
            elif target.op != 'name':
                value = self.evaluate(target, env)
            else:
                return self.function(target.args[0], parameters, env, width, t)
            if len(parameters) != 1:
                raise HDLError('Signal indexing takes one index or range.', t)
            index = parameters[0]
            if isinstance(value, Bits):
                ix = value.indices or tuple(range(len(value)))
                if index.op == 'range':
                    selected = tuple(reversed(self.bounds(index.args, env)))
                else:
                    selected = (self.const(index, env),)
                if any((i not in ix for i in selected)):
                    raise HDLError('Vector index/slice out of range.', t)
                return Bits(tuple((value.nets[ix.index(i)] for i in selected)), selected)
            if isinstance(value, Array):
                iv = self.evaluate(index, env)
                if type(iv) is int:
                    if iv not in value.items:
                        raise HDLError('Array index out of range.', t)
                    return value.items[iv]
                iv = self.bits(iv, t)
                if min(value.indices) != 0 or set(value.indices) != set(range(len(value.indices))):
                    raise HDLError('Dynamic array reads require a zero-based, contiguous array.', t)
                items = [value.items[i] for i in range(len(value.indices))]
                if not items or not all((isinstance(item, Bits) for item in items)):
                    raise HDLError('Dynamic reads support arrays of bits/vectors only.', t)
                item_width = len(items[0])
                if len(iv) > 16:
                    raise HDLError('Dynamic addresses are limited to 16 bits.', t)
                if len(items) > 2 ** len(iv):
                    raise HDLError('Address vector cannot select every array element.', t)
                for selector in iv.nets:
                    items = [Bits(tuple((b.mux(selector, y, x) for x, y in zip(items[i].nets, items[i + 1].nets if i + 1 < len(items) else (0,) * item_width)))) for i in range(0, len(items), 2)]
                return items[0]
            raise HDLError('Only vectors/arrays can be indexed.', t)
        raise HDLError('Unsupported expression.', t)

    def function(self, name, parameters, env, width, t):
        b = self.builder
        if name in ('rising_edge', 'falling_edge'):
            raise HDLError('Clock-edge functions are unsupported; use level-sensitive latch(data, enable[, reset]).', t)
        if name in ('unsigned', 'std_logic_vector', 'to_integer'):
            if len(parameters) != 1:
                raise HDLError(f'{name} expects one argument.', t)
            return self.evaluate(parameters[0], env, width)
        if name in ('to_unsigned', 'bits'):
            if len(parameters) != 2:
                raise HDLError(f'{name} expects a compile-time integer and width.', t)
            value, size = (self.const(p, env) for p in parameters)
            if not 1 <= size <= 4096:
                raise HDLError('Width must be between 1 and 4096.', t)
            return self.bits(value, t, size)
        if name == 'resize':
            if len(parameters) != 2:
                raise HDLError('resize expects a vector and constant width.', t)
            value = self.bits(self.evaluate(parameters[0], env), t)
            size = self.const(parameters[1], env)
            if not 1 <= size <= 4096:
                raise HDLError('Width must be between 1 and 4096.', t)
            return Bits((value.nets + (0,) * size)[:size])
        if name in ('and_reduce', 'or_reduce', 'xor_reduce'):
            if len(parameters) != 1:
                raise HDLError(f'{name} expects one vector.', t)
            value = self.bits(self.evaluate(parameters[0], env), t)
            return Bits((b.reduce(name.split('_')[0].upper(), value.nets),))
        if name == 'latch':
            if len(parameters) not in (2, 3):
                raise HDLError('latch(data, enable[, reset]) expects two or three arguments.', t)
            data = self.bits(self.evaluate(parameters[0], env, width), t, width)
            enable = self.bits(self.evaluate(parameters[1], env), t, 1).nets[0]
            reset = self.bits(self.evaluate(parameters[2], env), t, 1).nets[0] if len(parameters) == 3 else 0
            return b.latch(data.nets, enable, reset, t)
        raise HDLError(f'Unknown function {name}. Supported extensions: latch, resize, bits, and_reduce, or_reduce, xor_reduce.', t)

    def target(self, expression, env):
        if expression.op == 'call':
            for arg in expression.args[1]:
                if arg.op != 'range':
                    self.const(arg, env)
        value = self.evaluate(expression, env)
        if not isinstance(value, Bits):
            raise HDLError('Assign individual array elements, not an entire array.', expression.token)
        return value

    def instantiate(self, name, overrides, mappings, parent_env, prefix='', stack=()):
        if name not in self.entities:
            raise HDLError(f'Unknown entity {name}.')
        entity = self.entities[name]
        if name in stack or len(stack) >= 24:
            raise HDLError('Recursive or excessively deep entity instantiation.', entity['token'])
        self.scope_count += 1
        if self.scope_count > 50000:
            raise HDLError('Too many generated instances.', entity['token'])
        env = {}
        generic_names = {n.text for n, _, _ in entity['generics']}
        if set(overrides) - generic_names:
            raise HDLError(f"Unknown generic(s): {', '.join(sorted(set(overrides) - generic_names))}.", entity['token'])
        for token, kind, default in entity['generics']:
            if token.text in overrides:
                raw = overrides[token.text]
                value = raw if type(raw) is int else self.const(raw, parent_env)
            elif default is not None:
                value = self.const(default, env)
            else:
                raise HDLError(f'Generic {token.text} requires a value.', token)
            if not -2 ** 31 <= value < 2 ** 31 or (kind == 'positive' and value <= 0) or (kind == 'natural' and value < 0):
                raise HDLError(f'Invalid value {value} for {kind} generic {token.text}.', token)
            self.bind_name(env, token, value)
        if not prefix:
            self.generic_values = dict(env)
        port_names = {n.text for n, _, _ in entity['ports']}
        if mappings is not None and set(mappings) != port_names:
            raise HDLError(f"Port map must name every port exactly once. Expected: {', '.join(sorted(port_names))}.", entity['token'])
        for token, mode, spec in entity['ports']:
            kind = self.hdl_type(spec, env)
            if kind.element:
                raise HDLError('Array ports are unsupported; use vector ports.', token)
            value = self.new_signal(kind, prefix + token.text, token, readonly=mode == 'in')
            self.bind_name(env, token, value)
            if mappings is None:
                if mode == 'in':
                    for ref, ix in zip(value.nets, kind.indices):
                        net = self.builder.raw('INPUT', label=f'{token.text}({ix})', token=token)
                        self.builder.aliases[ref] = net
                self.top_ports[token.text] = dict(mode=mode, indices=kind.indices, nets=list(value.nets))
            elif mode == 'in':
                actual = self.bits(self.evaluate(mappings[token.text], parent_env, len(value)), token, len(value))
                for ref, source in zip(value.nets, actual.nets):
                    self.builder.aliases[ref] = source
        for declaration in entity['declarations']:
            category = declaration[0]
            if category == 'type':
                _, token, bounds, element = declaration
                kind = HDLType(self.bounds(bounds, env), self.hdl_type(element, env))
                if not kind.indices:
                    raise HDLError('Empty array declarations are unsupported.', token)
                self.bind_name(env, token, kind)
                continue
            _, tokens, spec, initial = declaration
            type_name = spec[0].text
            if category == 'constant' and type_name in ('integer', 'positive', 'natural'):
                if initial is None:
                    raise HDLError('Constants require a value.', tokens[0])
                value = self.const(initial, env)
                for token in tokens:
                    self.bind_name(env, token, value)
                continue
            if category == 'signal' and initial is not None:
                raise HDLError('Signal declaration initializers are unsupported. Use latch(..., reset) to initialize storage.', tokens[0])
            kind = self.hdl_type(spec, env)
            for token in tokens:
                if category == 'constant':
                    if kind.element or initial is None:
                        raise HDLError('Only scalar/vector and integer constants are supported.', token)
                    value = self.bits(self.evaluate(initial, env, len(kind.indices)), token, len(kind.indices))
                    value = Bits(value.nets, kind.indices)
                else:
                    value = self.new_signal(kind, prefix + token.text, token)
                self.bind_name(env, token, value)
        self.statements(entity['body'], env, prefix, stack + (name,))
        if mappings is not None:
            for token, mode, spec in entity['ports']:
                if mode == 'out':
                    actual = self.target(mappings[token.text], parent_env)
                    self.builder.drive(actual, env[token.text], mappings[token.text].token)

    def statements(self, statements, env, prefix, stack):
        for statement in statements:
            category = statement[0]
            if category == 'assign':
                _, token, target, value = statement
                outer, self.builder.section = (self.builder.section, sections.classify(stack, sections.target_name(target)))
                destination = self.target(target, env)
                source = self.bits(self.evaluate(value, env, len(destination)), value.token, len(destination))
                self.builder.drive(destination, source, token)
                self.builder.section = outer
            elif category == 'generate':
                _, label, var, bounds, body = statement
                if var.text in env:
                    raise HDLError('Generate variable shadows an existing name.', var)
                for i in self.bounds(bounds, env):
                    self.scope_count += 1
                    if self.scope_count > 50000:
                        raise HDLError('Generate expansion exceeds 50,000 iterations.', label)
                    self.statements(body, dict(env, **{var.text: i}), f'{prefix}{label.text}({i}).', stack)
            elif category == 'ifgenerate':
                _, label, condition, body = statement
                if self.const(condition, env):
                    self.statements(body, env, prefix + label.text + '.', stack)
            else:
                _, label, entity, overrides, mappings = statement
                outer, self.builder.section = (self.builder.section, sections.INSTANCE_SECTION.get(entity.text, self.builder.section))
                self.instantiate(entity.text, overrides, mappings, env, prefix + label.text + '.', stack)
                self.builder.section = outer

@dataclass
class Build:
    circuit: Circuit
    ports: dict
    report: dict
    source: str

    def export(self, destination=DEFAULT_BLUEPRINTS, layout='cube'):
        if layout not in ('auto', 'flat', 'stacked', 'cube'):
            raise ValueError('Packing must be auto, flat, stacked or cube.')
        folder = self.circuit.export(destination, ports=self.ports)
        manifest = json.loads((folder / 'circuit-map.json').read_text(encoding='utf-8'))['nodes']
        packing = json.loads((folder / 'packing.json').read_text(encoding='utf-8'))
        physical_map = {item['editorId']: item for item in manifest}
        port_map = {name: dict(mode=p['mode'], width=len(p['node_ids']), bits=[dict(index=index, **physical_map[node]) for index, node in zip(p['indices'], p['node_ids'])]) for name, p in self.ports.items()}
        (folder / 'source.vhd').write_text(self.source, encoding='utf-8')
        label_signs = packing['io_panel']['label_sign_count']
        (folder / 'hdl-build.json').write_text(json.dumps(dict(self.report, interface=self.ports, layout='cube', label_signs=label_signs, physical_blocks=packing['wiring']['physical_blocks'], physical_wires=packing['wiring']['physical_wires'], feedback_relays=packing['wiring']['feedback_relays'], packing=packing), indent=2), encoding='utf-8')
        (folder / 'port-map.json').write_text(json.dumps(port_map, indent=2), encoding='utf-8')
        return folder

def compile_source(source, top=None, generics=None, *, wide_gates=False, latch_style='nor'):
    if latch_style not in ('nor', 'nand', 'pulse_xor'):
        raise ValueError('Latch style must be nor, nand or experimental pulse_xor')
    start = time.perf_counter()
    try:
        entities = Parser(source).parse()
        if top is None:
            top = next(reversed(entities))
        top = top.lower()
        e = Elaborator(entities)
        e.builder.wide_gates = wide_gates
        e.builder.latch_style = latch_style
        e.instantiate(top, {k.lower(): v for k, v in (generics or {}).items()}, None, {})
        circuit, ports = e.builder.materialize(top, e.top_ports)
        warnings = ['Boolean preview only: game scheduler, lift startup and wire delays are not emulated.', 'Generated boards are limited to 50,000 parts. Large circuits may affect game performance.']
        if e.builder.latch_count and latch_style != 'pulse_xor':
            warnings.append('Storage uses level-sensitive gate latches, not edge-triggered flip-flops. Hold data/address steady while writing; lower enable and let the circuit settle before reading or changing address.')
        report = dict(top=top, generics=e.generic_values, blocks=len(circuit.nodes), wires=len(circuit.edges), storage_bits=sum((n['label'].startswith('latch Q ') for n in circuit.nodes.values())), gates=dict(Counter((n['kind'] for n in circuit.nodes.values()))), seconds=round(time.perf_counter() - start, 4), warnings=warnings)
        if latch_style != 'nor':
            report['latch_style'] = latch_style
        if latch_style == 'pulse_xor':
            report['warnings'].append('Experimental pulse storage: enable must be a single native update; reset settles through a delayed edge and 12-update release guard. Settle-separated Boolean preview is unsuitable.')
        return Build(circuit, ports, report, source)
    except RecursionError:
        raise HDLError('Expression or hierarchy is too deeply nested; split it into signals/components.') from None
    except (KeyError, IndexError, TypeError, OverflowError) as error:
        raise HDLError(f'Invalid construct: {error}.') from None
