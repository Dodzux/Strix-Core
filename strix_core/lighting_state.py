"""Validated per-user lighting settings. Loading never applies hardware output."""
import json

from .lighting import EFFECTS, validate_lighting


DEFAULT_PATTERN = {'effect': 'Static', 'zones': ['#cd467a', '#9268b4', '#6397ad', '#86d6b3'],
                   'speed': 1, 'paused': False, 'intensity': 70}


class LightingStore:
    def __init__(self, storage):
        self.storage = storage

    def load(self):
        try:
            value = json.loads((self.storage.root / 'lighting.json').read_text())
            return validate_lighting(value)
        except (OSError, ValueError, TypeError, AttributeError):
            return validate_lighting(DEFAULT_PATTERN)

    def save(self, pattern):
        clean = validate_lighting(pattern)
        self.storage.write('lighting.json', clean)
        return clean

    def next_effect(self, pattern=None, direction=1):
        if direction not in (-1, 1):
            raise ValueError('Lighting direction must be previous or next.')
        value = validate_lighting(pattern) if pattern is not None else self.load()
        value['effect'] = EFFECTS[(EFFECTS.index(value['effect']) + direction) % len(EFFECTS)]
        value['paused'] = False
        return value
