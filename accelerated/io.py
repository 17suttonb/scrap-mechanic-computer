from pathlib import Path
import json

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')
