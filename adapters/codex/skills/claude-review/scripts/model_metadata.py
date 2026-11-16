"""Known Qwen metadata, narrowed to text-only bounded CLI use."""
import copy
import json


def effort(model):
    return 'high' if model == 'qwen4-max' else 'medium'


def catalog(model):
    if model not in ('qwen3.8-max', 'qwen4-max'):
        return None
    entry = {'slug': 'qwen3.8-max', 'display_name': 'qwen3.8-max',
                 'description': 'Qwen text review', 'default_reasoning_level': 'xhigh',
                 'supported_reasoning_levels': [{'effort': x, 'description': x} for x in ('low', 'medium', 'xhigh')],
                 'shell_type': 'default', 'visibility': 'list', 'supported_in_api': True,
                 'priority': 1, 'base_instructions': '', 'supports_reasoning_summaries': False,
                 'support_verbosity': False, 'supports_parallel_tool_calls': False,
                 'context_window': 983616, 'effective_context_window_percent': 95,
                 'input_modalities': ['text'], 'supports_image_detail_original': False,
                 'experimental_supported_tools': [],
                 'truncation_policy': {'mode': 'bytes', 'limit': 65536}}
    if model == 'qwen4-max':
        entry.update(slug=model, display_name=model, default_reasoning_level='high',
                     context_window=1000000,
                     supported_reasoning_levels=[{'effort': x, 'description': x} for x in ('low', 'high', 'xhigh')])
    return {'models': [copy.deepcopy(entry)]}


def write_catalog(model, path):
    data = catalog(model)
    if data is None:
        return None
    path = path.resolve()
    path.write_text(json.dumps(data) + '\n', encoding='utf-8')
    return str(path)
