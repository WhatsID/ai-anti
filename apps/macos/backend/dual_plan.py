"""Side-effect-free desktop equivalent of the CLI's dual-plan contracts."""

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


AGY_KEY = 'agy_relay.dual_plan'
AGY_ALLOWED_TOP = {'review', 'implementation', 'dual_plan'}
AGY_ALLOWED_KEYS = {'enabled', 'partner', 'model', 'effort', 'synthesizer', 'on_failure'}
AGY_PARTNERS = ('claude',)
AGY_SYNTHESIZERS = ('agy', 'claude')
AGY_DEFAULTS = {'enabled': False, 'partner': 'claude', 'model': None, 'effort': None,
                'synthesizer': 'agy', 'on_failure': 'stop'}


def _agy_object(value, key, allowed, error):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise error('ai-anti config: ' + key + ' must be an object')
    unknown = value.keys() - allowed
    if unknown:
        raise error('ai-anti config: ' + key + ' has unknown keys: '
                    + ', '.join(sorted(map(repr, unknown))))
    return value


def parse_agy(ai, error):
    if ai is None:
        return None
    if not isinstance(ai, dict):
        raise error('ai-anti config: agy_relay parent is not an object')
    relay = _agy_object(ai.get('agy_relay'), 'agy_relay', AGY_ALLOWED_TOP, error)
    value = _agy_object((relay or {}).get('dual_plan'), AGY_KEY, AGY_ALLOWED_KEYS, error)
    if value is None:
        return None

    result = dict(AGY_DEFAULTS)
    prefix = 'ai-anti config: ' + AGY_KEY
    enabled = value.get('enabled', False)
    if type(enabled) is not bool:
        raise error(prefix + '.enabled must be a boolean')
    result['enabled'] = enabled
    for field, allowed in (('partner', AGY_PARTNERS), ('effort', EFFORTS),
                           ('synthesizer', AGY_SYNTHESIZERS), ('on_failure', ON_FAILURE)):
        selected = value.get(field)
        if selected is not None:
            if not isinstance(selected, str) or selected not in allowed:
                raise error(f'{prefix}.{field} must be one of ' + ', '.join(allowed))
            result[field] = selected
    model = value.get('model')
    if model is not None and (not isinstance(model, str) or not model or any(c.isspace() for c in model)):
        raise error(prefix + '.model must be a non-empty string without whitespace')
    result['model'] = model
    return result


def apply_agy_enabled(ai, enabled, error):
    if type(enabled) is not bool:
        raise error('agy_dual_plan_enabled 必须为布尔值')
    stored = parse_agy(ai, error)
    if (stored or AGY_DEFAULTS)['enabled'] == enabled:
        return
    if ai.get('agy_relay') is None:
        ai['agy_relay'] = {}
    relay = ai['agy_relay']
    if relay.get('dual_plan') is None:
        relay['dual_plan'] = dict(AGY_DEFAULTS, enabled=enabled)
    else:
        relay['dual_plan']['enabled'] = enabled
