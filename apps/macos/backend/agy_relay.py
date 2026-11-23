"""AGY relay validation and in-memory model edits for the desktop store."""

KEY = 'agy_relay'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
REVIEW_DEFAULT = {'provider': 'codex', 'model': None, 'effort': None, 'high_risk_effort': None}
CHANGE_KEYS = ('agy_review_model', 'agy_implementation_model')


def _object(value, key, allowed, error):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise error('ai-anti config: ' + key + ' must be an object')
    unknown = value.keys() - allowed
    if unknown:
        raise error('ai-anti config: ' + key + ' has unknown keys: '
                    + ', '.join(sorted(map(repr, unknown))))
    return value


def _model(value, key, error):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise error('ai-anti config: ' + key + '.model must be a non-empty string without whitespace')


def _effort(value, key, error):
    if value is not None and (not isinstance(value, str) or value not in EFFORTS):
        raise error('ai-anti config: ' + key + ' must be one of ' + ', '.join(EFFORTS))
    return value


def _parse_route(ai, route, error):
    if ai is not None and not isinstance(ai, dict):
        raise error('ai-anti config: ' + KEY + ' parent is not an object')
    relay = _object(ai.get(KEY) if ai is not None else None, KEY, {'review', 'implementation', 'dual_plan'}, error)
    key = KEY + '.' + route
    reviewing = route == 'review'
    allowed = {'provider', 'model', 'effort'}
    if reviewing:
        allowed.add('high_risk_effort')
    value = _object((relay or {}).get(route), key, allowed, error)
    if value is None:
        return dict(REVIEW_DEFAULT) if reviewing else None
    provider = value.get('provider')
    if provider != 'codex' and not (reviewing and provider is None):
        raise error('ai-anti config: ' + key + '.provider must be codex')
    model = value.get('model')
    if not (reviewing and model is None):
        _model(model, key, error)
    effort = _effort(value.get('effort'), key + '.effort', error)
    result = {'provider': 'codex', 'model': model, 'effort': effort}
    if reviewing:
        elevated = _effort(value.get('high_risk_effort'), key + '.high_risk_effort', error)
        if effort is not None and elevated is not None and EFFORTS.index(elevated) < EFFORTS.index(effort):
            raise error('ai-anti config: ' + key + '.high_risk_effort must not be lower than ' + key + '.effort')
        result['high_risk_effort'] = elevated
    return result


def parse_review(ai, error):
    return _parse_route(ai, 'review', error)


def parse_implementation(ai, error):
    return _parse_route(ai, 'implementation', error)


def parse(ai, error):
    return {'review': parse_review(ai, error), 'implementation': parse_implementation(ai, error)}


def apply(ai, changes, error):
    if not isinstance(changes, dict) or any(key not in CHANGE_KEYS for key in changes):
        raise error('AGY 接力配置变更字段无效')
    for key, value in changes.items():
        if not isinstance(value, str):
            raise error(key + ' 必须为字符串')
    try:
        stored = parse(ai, error)
    except error as exc:
        raise error(f'AGY 接力配置无效，请先修复 config.json 中的 {KEY}') from exc

    review_changed = implementation_changed = False
    if 'agy_review_model' in changes:
        review_target = None if changes['agy_review_model'] == '' else changes['agy_review_model']
        if review_target is not None:
            _model(review_target, KEY + '.review', error)
        review_changed = review_target != stored['review']['model']
    if 'agy_implementation_model' in changes:
        impl_raw = changes['agy_implementation_model']
        if impl_raw == '':
            if stored['implementation'] is not None:
                raise error('应用不清空 AGY 实施配置；如需移除请手工编辑 agy_relay.implementation')
        else:
            _model(impl_raw, KEY + '.implementation', error)
            implementation_changed = impl_raw != (stored['implementation'] or {}).get('model')
    if not (review_changed or implementation_changed):
        return False

    if ai.get(KEY) is None:
        ai[KEY] = {}
    relay = ai[KEY]
    if review_changed:
        review = relay.get('review')
        if isinstance(review, dict):
            if review_target is None:
                review.pop('model', None)
            else:
                review['model'] = review_target
        else:
            relay['review'] = {'provider': 'codex', 'model': review_target}
    if implementation_changed:
        implementation = relay.get('implementation')
        if isinstance(implementation, dict):
            implementation['model'] = impl_raw
        else:
            relay['implementation'] = {'provider': 'codex', 'model': impl_raw}
    parse(ai, error)
    return True
