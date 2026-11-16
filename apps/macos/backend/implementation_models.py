"""Strict, side-effect-free Token Plan implementation model contract."""
DEFAULT = 'glm-5.3'
MODELS = (DEFAULT, 'qwen4-max')


def parse(value):
    if value is None:
        return DEFAULT
    if not isinstance(value, str) or value not in MODELS:
        raise ValueError('Token Plan implementation model must be glm-5.3 or qwen4-max')
    return value
