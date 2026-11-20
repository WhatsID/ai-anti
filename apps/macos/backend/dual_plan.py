"""Side-effect-free desktop equivalent of the CLI's Claude dual-plan contract."""

KEY = 'claude_relay.dual_plan'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
PARTNERS = ('agy',)
SYNTHESIZERS = ('claude', 'agy')
ON_FAILURE = ('stop', 'fallback')
DEFAULTS = {'enabled': False, 'partner': 'agy', 'model': None, 'effort': None,
            'synthesizer': 'claude', 'on_failure': 'stop'}


def parse(ai, error):
    value = ai
    for key in KEY.split('.'):
        if value is None:
            return None
        if not isinstance(value, dict):
            raise error(KEY + ' 的上级配置节必须为对象')
        value = value.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise error(KEY + ' 必须为对象')

    result = dict(DEFAULTS)
    enabled = value.get('enabled', False)
    if type(enabled) is not bool:
        raise error(KEY + '.enabled 必须为布尔值')
    result['enabled'] = enabled
    for key, allowed in (('partner', PARTNERS), ('effort', EFFORTS),
                         ('synthesizer', SYNTHESIZERS), ('on_failure', ON_FAILURE)):
        field = value.get(key)
        if field is not None:
            if not isinstance(field, str) or field not in allowed:
                raise error(f'{KEY}.{key} 无效；合法值：' + '/'.join(allowed))
            result[key] = field
    model = value.get('model')
    if model is not None and (not isinstance(model, str) or not model or any(c.isspace() for c in model)):
        raise error(KEY + '.model 必须为非空且不含空白的字符串')
    result['model'] = model
    return result


def apply_enabled(ai, enabled, error):
    if type(enabled) is not bool:
        raise error('dual_plan_enabled 必须为布尔值')
    try:
        stored = parse(ai, error)
    except error:
        raise error(f'双方案配置无效，请先修复 config.json 中的 {KEY}') from None
    if (stored or DEFAULTS)['enabled'] == enabled:
        return
    if ai.get('claude_relay') is None:
        ai['claude_relay'] = {}
    relay = ai['claude_relay']
    if relay.get('dual_plan') is None:
        relay['dual_plan'] = dict(DEFAULTS, enabled=enabled)
    else:
        relay['dual_plan']['enabled'] = enabled
