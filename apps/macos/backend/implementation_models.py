"""Strict, side-effect-free Token Plan implementation model contract."""
DEFAULT = 'glm-5.3'
MODELS = (DEFAULT, 'qwen4-max', 'qwen3.8-max', 'deepseek-v4-pro', 'deepseek-v4.1-flash')


def parse(value):
    if value is None:
        return DEFAULT
    if not isinstance(value, str) or value not in MODELS:
        raise ValueError('Token Plan implementation model must be one of: ' + ', '.join(MODELS))
    return value
