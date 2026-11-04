"""Native digital signs, verified against 'sign test' and 'types of signs'.

controller.data is unpadded base64 of a raw LZ4 block containing a Lua save
table. A literal-only LZ4 block avoids a dependency while retaining the exact
native table schema {text=..., selected=1} observed in all three saved types.
"""
import base64
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class SignType:
    name: str
    uuid: str
    size: tuple
    text_limit: int


# User-facing sizes. The game calls the middle shape 'large' and the
# eight-wide shape 'extralarge'; all values match the native saved fixture.
SIGN_TYPES = {
    'small': SignType('small','4908ecfb-ec05-42c5-a02a-7675ccc3eb19',(3,2,1),6),
    'medium': SignType('medium','a96232ef-2778-4c23-8a11-7cbc5b1de8b0',(5,2,1),11),
    'large': SignType('large','46d85e5b-353c-4f06-8fd8-b9ddd3443245',(8,2,1),18),
}
SIGN_BY_UUID = {spec.uuid:spec for spec in SIGN_TYPES.values()}
SIGN_UUIDS = frozenset(SIGN_BY_UUID)

# Compatibility aliases for callers explicitly using the original 11-char sign.
SIGN_UUID = SIGN_TYPES['medium'].uuid
SIGN_SIZE = SIGN_TYPES['medium'].size
SIGN_TEXT_LIMIT = SIGN_TYPES['medium'].text_limit
SAMPLE_DATA = '0ExVQQAAAAEFAAAAAgIFAPAGAHRleHQEAAAAC0hlbGxvLCBXb3JsEACwCHNlbGVjdGVkCAE'
_PREFIX = bytes.fromhex('4c5541000000010500000002020000000200') + b'text\x04'
_SUFFIX = b'\x04\x00\x00\x00\x08selected\x08\x01'


def decode_lz4(block):
    """Bounded raw-block decoder for fixture verification and label inspection."""
    out, i = bytearray(), 0
    def length(value):
        nonlocal i
        if value == 15:
            while True:
                if i >= len(block):
                    raise ValueError('Truncated LZ4 length')
                extra = block[i]
                i += 1
                value += extra
                if extra != 255:
                    break
        return value
    while i < len(block):
        token = block[i]
        i += 1
        size = length(token >> 4)
        if i+size > len(block):
            raise ValueError('Truncated LZ4 literals')
        out.extend(block[i:i+size])
        if len(out) > 65536:
            raise ValueError('Oversized LZ4 block')
        i += size
        if i == len(block):
            break
        if i+2 > len(block):
            raise ValueError('Truncated LZ4 offset')
        offset = int.from_bytes(block[i:i+2], 'little')
        i += 2
        size = length(token & 15)+4
        if not 0 < offset <= len(out) or len(out)+size > 65536:
            raise ValueError('Invalid LZ4 match')
        for _ in range(size):
            out.append(out[-offset])
    return bytes(out)


def encode_sign_text(text, sign_type='medium'):
    spec=SIGN_TYPES[sign_type]
    if not isinstance(text, str) or len(text) > spec.text_limit:
        raise ValueError(f'{spec.name.capitalize()} signs accept at most {spec.text_limit} characters.')
    encoded = text.encode('utf-8')
    raw = _PREFIX + struct.pack('>I', len(encoded)) + encoded + _SUFFIX
    block = bytearray([min(len(raw), 15) << 4])
    if len(raw) >= 15:
        extra = len(raw)-15
        while extra >= 255:
            block.append(255)
            extra -= 255
        block.append(extra)
    block.extend(raw)
    return base64.b64encode(block).decode('ascii').rstrip('=')


def decode_sign_text(data):
    block = base64.b64decode(data + '=' * (-len(data) % 4), validate=True)
    raw = decode_lz4(block)
    if not raw.startswith(_PREFIX):
        raise ValueError('Unrecognized digital sign save schema')
    length = struct.unpack('>I', raw[len(_PREFIX):len(_PREFIX)+4])[0]
    start = len(_PREFIX)+4
    if raw[start+length:] != _SUFFIX:
        raise ValueError('Unexpected digital sign table fields')
    return raw[start:start+length].decode('utf-8')


def sign_chunks(text, sign_type='medium'):
    """Legacy fixed-size splitting. Exporters use plan_signs for mixed sizes."""
    text = ' '.join(str(text).split())
    limit=SIGN_TYPES[sign_type].text_limit
    return [text[i:i+limit] for i in range(0,len(text),limit)] or ['']


def choose_sign(text):
    for spec in SIGN_TYPES.values():
        if len(text)<=spec.text_limit:return spec
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
    text=' '.join(str(text).split())
    if not text:return [SignPiece('',SIGN_TYPES['small'])]
    best=[None]*(len(text)+1);best[-1]=((0,0,0),[])
    for start in range(len(text)-1,-1,-1):
        winner=None
        for length in range(1,min(18,len(text)-start)+1):
            end=start+length;part=text[start:end];spec=choose_sign(part)
            tail_score,tail=best[end]
            split=int(end<len(text) and not text[end-1].isspace() and not text[end].isspace())
            score=(tail_score[0]+1,tail_score[1]+split,tail_score[2]+spec.size[0])
            key=score+(-length,)
            if winner is None or key<winner[0]:winner=(key,score,[SignPiece(part,spec)]+tail)
        best[start]=(winner[1],winner[2])
    return best[0][1]


def make_sign(text, controller_id, position, mode, sign_type=None):
    spec=choose_sign(text) if sign_type is None else SIGN_TYPES[sign_type]
    return dict(color='4C6FE3' if mode == 'in' else 'F5D742',
                controller=dict(active=False, controllers=None, data=encode_sign_text(text,spec.name),
                                id=controller_id, joints=None),
                pos=dict(position), shapeId=spec.uuid, xaxis=1, zaxis=3)


def sign_record(child,text,port,mode):
    spec=SIGN_BY_UUID[child['shapeId']]
    return dict(controllerId=child['controller']['id'],text=text,position=dict(child['pos']),
                size=dict(zip('xyz',spec.size)),shapeId=spec.uuid,sign_type=spec.name,
                character_limit=spec.text_limit,port=port,mode=mode,
                xaxis=child['xaxis'],zaxis=child['zaxis'])
