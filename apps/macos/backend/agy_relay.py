"""AGY relay validation and in-memory model edits for the desktop store."""
import relay_routes as routes

KEY = 'agy_relay'
REVIEW_DEFAULT = routes.DEFAULT_REVIEW
CHANGE_KEYS = ('agy_review_model', 'agy_implementation_model', 'agy_review_route', 'agy_implementation_route')
PROVIDERS = {'review': routes.AGY_REVIEW_PROVIDERS, 'implementation': routes.AGY_IMPLEMENTATION_PROVIDERS}


def section(ai, error):
    return routes.parse_host_relay(ai, KEY, routes.AGY_SECTION_KEYS, error)


def parse_route(value, role, error):
    return routes.parse_route(value, KEY, role, PROVIDERS[role], error)


def _parse_role(relay, role, error):
    return parse_route(relay.get(role), role, error)


def parse_review(ai, error):
    relay = section(ai, error) or {}
    routes.parse_codex_defaults(relay.get('codex_defaults'), KEY, error)
    return _parse_role(relay, 'review', error)


def parse_implementation(ai, error):
    relay = section(ai, error) or {}
    routes.parse_codex_defaults(relay.get('codex_defaults'), KEY, error)
    return _parse_role(relay, 'implementation', error)


def parse(ai, error):
    relay = section(ai, error) or {}
    return {'review': _parse_role(relay, 'review', error),
            'implementation': _parse_role(relay, 'implementation', error),
            'codex_defaults': routes.parse_codex_defaults(relay.get('codex_defaults'), KEY, error)}


def _target(changes, role, current, error):
    route_key, model_key = 'agy_' + role + '_route', 'agy_' + role + '_model'
    if route_key in changes:
        if model_key in changes:
            raise error(route_key + ' 与 ' + model_key + ' 不能同时提交')
        if not isinstance(changes[route_key], dict):
            raise error(route_key + ' 必须为对象')
        return parse_route(changes[route_key], role, error)
    if model_key not in changes:
        return current
    model = changes[model_key]
    if not isinstance(model, str):
        raise error(model_key + ' 必须为字符串')
    if role == 'implementation' and model == '':
        if current is not None:
            raise error('应用不清空 AGY 实施配置；如需移除请手工编辑 agy_relay.implementation')
        return None
    return parse_route({'provider': 'codex', 'model': model or None}, role, error)


def _identity(route):
    if route is None:
        return None
    return route['provider'], route.get('service'), route['model']


def apply(ai, changes, error):
    if not isinstance(changes, dict) or any(key not in CHANGE_KEYS for key in changes):
        raise error('AGY 接力配置变更字段无效')
    try:
        stored = parse(ai, error)
    except error as exc:
        raise error(f'AGY 接力配置无效，请先修复 config.json 中的 {KEY}') from exc

    relay = dict(ai.get(KEY) or {})
    original_defaults = relay.get('codex_defaults') or {}
    defaults = dict(original_defaults)
    changed = False
    for role in routes.ROLES:
        current = stored[role]
        target = _target(changes, role, current, error)
        if _identity(current) == _identity(target):
            continue
        changed = True
        if routes.is_official(target):
            if current and not routes.is_official(current):
                effort = {key: value for key, value in current.items()
                          if key in ('effort', 'high_risk_effort') and value is not None}
                if effort:
                    defaults[role] = effort
            relay[role] = target
        elif current and not routes.is_official(current):
            native = dict(relay[role]) if relay.get(role) is not None else {'provider': 'codex'}
            if target['model'] is None:
                native.pop('model', None)
            else:
                native['model'] = target['model']
            relay[role] = native
        else:
            native = {'provider': 'codex', 'model': target['model']}
            if routes.is_official(current) and role in defaults:
                native.update(stored['codex_defaults'][role])
                del defaults[role]
            relay[role] = native
    if not changed:
        return False
    if defaults != original_defaults:
        if defaults:
            relay['codex_defaults'] = defaults
        else:
            del relay['codex_defaults']
    ai[KEY] = relay
    return True
