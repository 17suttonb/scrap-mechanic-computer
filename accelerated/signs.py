import base64
import struct
from dataclasses import dataclass

@dataclass(frozen=True)
class SignType:
    name: str
    uuid: str
    size: tuple
    text_limit: int
SIGN_TYPES = {'small': SignType('small', '4908ecfb-ec05-42c5-a02a-7675ccc3eb19', (3, 2, 1), 6), 'medium': SignType('medium', 'a96232ef-2778-4c23-8a11-7cbc5b1de8b0', (5, 2, 1), 11), 'large': SignType('large', '46d85e5b-353c-4f06-8fd8-b9ddd3443245', (8, 2, 1), 18)}
SIGN_BY_UUID = {spec.uuid: spec for spec in SIGN_TYPES.values()}
_PREFIX = bytes.fromhex('4c5541000000010500000002020000000200') + b'text\x04'
_SUFFIX = b'\x04\x00\x00\x00\x08selected\x08\x01'

def encode_sign_text(text, sign_type='medium'):
    spec = SIGN_TYPES[sign_type]
    if not isinstance(text, str) or len(text) > spec.text_limit:
        raise ValueError(f'{spec.name.capitalize()} signs accept at most {spec.text_limit} characters.')
    encoded = text.encode('utf-8')
    raw = _PREFIX + struct.pack('>I', len(encoded)) + encoded + _SUFFIX
    block = bytearray([min(len(raw), 15) << 4])
    if len(raw) >= 15:
        extra = len(raw) - 15
        while extra >= 255:
            block.append(255)
            extra -= 255
        block.append(extra)
    block.extend(raw)
    return base64.b64encode(block).decode('ascii').rstrip('=')

def choose_sign(text):
    for spec in SIGN_TYPES.values():
        if len(text) <= spec.text_limit:
            return spec
    raise ValueError('Text exceeds one sign; use plan_signs to retain the full label')

@dataclass(frozen=True)
class SignPiece:
    text: str
    spec: SignType

def plan_signs(text):
    """Fewest signs, then word boundaries, then the smallest combined width.

    No abbreviation or truncation: concatenating the pieces gives the complete
    whitespace-normalized label. Short labels use the smallest fitting type.
    """
    text = ' '.join(str(text).split())
    if not text:
        return [SignPiece('', SIGN_TYPES['small'])]
    best = [None] * (len(text) + 1)
    best[-1] = ((0, 0, 0), [])
    for start in range(len(text) - 1, -1, -1):
        winner = None
        for length in range(1, min(18, len(text) - start) + 1):
            end = start + length
            part = text[start:end]
            spec = choose_sign(part)
            tail_score, tail = best[end]
            split = int(end < len(text) and (not text[end - 1].isspace()) and (not text[end].isspace()))
            score = (tail_score[0] + 1, tail_score[1] + split, tail_score[2] + spec.size[0])
            key = score + (-length,)
            if winner is None or key < winner[0]:
                winner = (key, score, [SignPiece(part, spec)] + tail)
        best[start] = (winner[1], winner[2])
    return best[0][1]

def make_sign(text, controller_id, position, mode, sign_type=None):
    spec = choose_sign(text) if sign_type is None else SIGN_TYPES[sign_type]
    return dict(color='4C6FE3' if mode == 'in' else 'F5D742', controller=dict(active=False, controllers=None, data=encode_sign_text(text, spec.name), id=controller_id, joints=None), pos=dict(position), shapeId=spec.uuid, xaxis=1, zaxis=3)

def sign_record(child, text, port, mode):
    spec = SIGN_BY_UUID[child['shapeId']]
    return dict(controllerId=child['controller']['id'], text=text, position=dict(child['pos']), size=dict(zip('xyz', spec.size)), shapeId=spec.uuid, sign_type=spec.name, character_limit=spec.text_limit, port=port, mode=mode, xaxis=child['xaxis'], zaxis=child['zaxis'])
