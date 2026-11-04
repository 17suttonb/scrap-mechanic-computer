"""Draw exported modules as images, coloured as in the game, to judge a layout without loading it.

Usage: python tools/render_layout.py <folder holding module folders, or one module> <output folder> [--prefix NAME]
Writes <prefix><module>.png for each module: an isometric view from behind and above, an isometric view from
the front, and a plan from above with the sign texts. Needs Pillow.
"""
import argparse
import base64
import json
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE = '4ad97d49-'
SIGNS = {'4908ecfb-ec05-42c5-a02a-7675ccc3eb19': (3, 2, 1), 'a96232ef-2778-4c23-8a11-7cbc5b1de8b0': (5, 2, 1), '46d85e5b-353c-4f06-8fd8-b9ddd3443245': (8, 2, 1)}
TIMER = '8f7fd0e7-c46e-4944-a414-7ce2437bb30f'
BACKGROUND = (18, 20, 26)


def cells_of(p):
    x, y, z = p['pos']['x'], p['pos']['y'], p['pos']['z']
    axes = p['xaxis'], p['zaxis']
    if p['shapeId'] in SIGNS:
        w, d, h = SIGNS[p['shapeId']]
        if axes == (1, -2):
            return [(x + a, y - 1, z + b) for a in range(w) for b in range(d)]
        return [(x + a, y + b, z) for a in range(w) for b in range(d)]
    if p['shapeId'] == TIMER:
        return [(x, y, z), (x, y + 1, z)]
    if p['shapeId'].startswith(BASE):
        b = p['bounds']
        return [(x + a, y + c, z + e) for a in range(b['x']) for c in range(b['y']) for e in range(b['z'])]
    if axes == (1, -2):
        return [(x, y - 1, z)]
    if axes == (-1, 3):
        return [(x - 1, y - 1, z)]
    return [(x, y, z)]


def sign_text(p):
    data = base64.b64decode(p['controller']['data'] + '===')
    m = re.search(rb'text\x04(....)', data, re.S)
    return data[m.end():m.end() + int.from_bytes(m[1], 'big')].decode('utf-8', 'replace') if m else ''


def rgb(colour, factor=1.0):
    return tuple(min(255, int(int(colour[k:k + 2], 16) * factor)) for k in (0, 2, 4))


def load(folder):
    bp = json.loads((folder / 'blueprint.json').read_text(encoding='utf-8'))
    cells, signs = {}, []
    for body in bp['bodies']:
        for p in body['childs']:
            for cell in cells_of(p):
                cells[cell] = p['color']
            if p['shapeId'] in SIGNS:
                signs.append((sign_text(p), p))
    return cells, signs


def iso(cells, face=1, scale=4):
    """Isometric view: face=1 looks from +x +y (behind the machine), face=-1 from -x -y (in front)."""
    def project(x, y, z):
        return (face * (x - y)) * scale, face * (x + y) * scale * 0.5 - z * scale
    shown = sorted(cells, key=lambda c: (face * c[0] * -1 + face * c[1] * -1, c[2]))
    corners = [project(*c) for c in cells]
    left, right = min(p[0] for p in corners) - 2 * scale, max(p[0] for p in corners) + 2 * scale
    top, bottom = min(p[1] for p in corners) - 2 * scale, max(p[1] for p in corners) + 2 * scale
    image = Image.new('RGB', (int(right - left), int(bottom - top)), BACKGROUND)
    draw = ImageDraw.Draw(image)
    out = lambda p: (p[0] - left, p[1] - top)
    step = -face
    # painter: cells furthest from the viewer first; only faces with nothing in front are drawn
    for x, y, z in shown:
        colour = cells[x, y, z]
        s = step
        a = lambda dx, dy, dz: out(project(x + dx, y + dy, z + dz))
        near_x, near_y = (1, 1) if face == 1 else (0, 0)
        far_x, far_y = 1 - near_x, 1 - near_y
        if (x, y, z + 1) not in cells:
            draw.polygon([a(near_x, near_y, 1), a(far_x, near_y, 1), a(far_x, far_y, 1), a(near_x, far_y, 1)], fill=rgb(colour, 1.0))
        if (x + (1 if face == 1 else -1), y, z) not in cells:
            draw.polygon([a(near_x, near_y, 1), a(near_x, far_y, 1), a(near_x, far_y, 0), a(near_x, near_y, 0)], fill=rgb(colour, 0.72))
        if (x, y + (1 if face == 1 else -1), z) not in cells:
            draw.polygon([a(near_x, near_y, 1), a(far_x, near_y, 1), a(far_x, near_y, 0), a(near_x, near_y, 0)], fill=rgb(colour, 0.5))
    return image


def plan(cells, signs, scale=7):
    tops = {}
    for (x, y, z), colour in cells.items():
        if (x, y) not in tops or z > tops[x, y][0]:
            tops[x, y] = (z, colour)
    xs, ys = [c[0] for c in tops], [c[1] for c in tops]
    left, right, low, high = min(xs), max(xs), min(ys), max(ys)
    image = Image.new('RGB', ((right - left + 3) * scale, (high - low + 3) * scale), BACKGROUND)
    draw = ImageDraw.Draw(image)
    tallest = max(z for z, _ in tops.values()) or 1
    for (x, y), (z, colour) in tops.items():
        px, py = (x - left + 1) * scale, (high - y + 1) * scale
        draw.rectangle([px, py, px + scale - 1, py + scale - 1], fill=rgb(colour, 0.55 + 0.45 * z / tallest))
    try:
        font = ImageFont.truetype('arialbd.ttf', max(8, scale))
    except OSError:
        font = ImageFont.load_default()
    for text, p in signs:
        if p['zaxis'] == -2 or not text:
            continue
        w = SIGNS[p['shapeId']][0]
        px, py = (p['pos']['x'] - left + 1) * scale, (high - p['pos']['y']) * scale
        draw.rectangle([px, py, px + w * scale - 1, py + 2 * scale - 1], fill=rgb(p['color'], 0.95))
        fill = (0, 0, 0)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((px + 1, py + scale - (box[3] - box[1]) // 2 - 1), text, fill=fill, font=font)
    return image


def sheet(images):
    margin = 16
    width = max(i.width for i in images) + margin * 2
    result = Image.new('RGB', (width, sum(i.height for i in images) + margin * (len(images) + 1)), BACKGROUND)
    y = margin
    for i in images:
        result.paste(i, ((width - i.width) // 2, y))
        y += i.height + margin
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--prefix', default='layout-')
    args = parser.parse_args()
    folders = [args.folder] if (args.folder / 'blueprint.json').exists() else sorted(f for f in args.folder.iterdir() if (f / 'blueprint.json').exists())
    args.output.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        name = re.search(r'(CORE|CONSOLE|CARTRIDGE)', json.loads((folder / 'description.json').read_text(encoding='utf-8'))['name'])
        name = name[1].lower() if name else folder.name[:8]
        cells, signs = load(folder)
        path = args.output / f'{args.prefix}{name}.png'
        sheet([plan(cells, signs), iso(cells, 1), iso(cells, -1)]).save(path)
        print(path, len(cells), 'cells')


if __name__ == '__main__':
    main()
