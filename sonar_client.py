"""Bounded SonarQube Web API v1 reads. Never follows redirects."""
from datetime import datetime, timezone
import json
import math
import re
import ssl
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPHandler, HTTPSHandler, HTTPRedirectHandler, Request, ProxyHandler

from debtpack import relative, token, MAX_ISSUES

# Sonar rule ids (e.g. "csharpsquid:S2094", "typescript:S1481"). The colon is
# not allowed by token(), so rule needs its own validation pattern.
RULE = re.compile(r'[A-Za-z0-9_:.-]{1,200}')


class Blocked(ValueError):
    """Evidence or capability is unavailable; not a passing result."""


def endpoint(value, allow_http=False):
    if not isinstance(value, str) or any(ord(x) < 33 for x in value) or '\\' in value:
        raise Blocked('invalid endpoint')
    p = urlsplit(value)
    scheme_ok = p.scheme == 'https' or (allow_http and p.scheme == 'http')
    if (not scheme_ok or not p.hostname or p.username or p.password or p.query or p.fragment
            or '%' in p.netloc or any(x in ('.', '..') for x in p.path.split('/'))):
        raise Blocked('endpoint must be canonical HTTPS without credentials, query or fragment '
                      '(HTTP requires an explicit opt-in)')
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Blocked('redirect refused; configure the final trusted HTTPS endpoint')


class Client:
    def __init__(self, url, secret, timeout=30, allow_http=False):
        self.url = endpoint(url, allow_http=allow_http)
        self.secret = secret
        self.timeout = timeout
        # Ignore ambient proxy settings: a trust boundary must not be implicit.
        handlers = [ProxyHandler({})]
        if allow_http:
            handlers.append(HTTPHandler())
        handlers.append(HTTPSHandler(context=ssl.create_default_context()))
        handlers.append(NoRedirect())
        self.opener = build_opener(*handlers)

    def request(self, path, data=None, content_type=None, limit=4 * 1024 * 1024):
        if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./?=&%+,:~-]+', path)
                or path.startswith('/') or '..' in path or '://' in path):
            raise Blocked('unsafe API path')
        headers = {'Authorization': 'Bearer ' + self.secret, 'Accept-Encoding': 'identity'}
        if content_type:
            headers['Content-Type'] = content_type
        request = Request(self.url + '/' + path, data=data, headers=headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            if response.geturl() != request.full_url:
                raise Blocked('response endpoint changed')
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise Blocked('response byte budget exceeded')
            return raw, response.headers.get('Content-Type', 'application/octet-stream')

    def get(self, path, **params):
        raw, _ = self.request(path + ('?' + urlencode(params) if params else ''))
        return json.loads(raw)


def pages_all(get, path, field, params, maximum=100000, page_size=100, page_budget=1000):
    items, seen, expected = [], set(), None
    for page in range(1, page_budget + 1):
        data = get(path, **params, p=page, ps=page_size)
        paging = data.get('paging', {})
        total = paging.get('total')
        batch = data.get(field)
        if (type(total) is not int or not 0 <= total <= maximum or not isinstance(batch, list)
                or paging.get('pageIndex') != page or paging.get('pageSize') != page_size
                or (expected is not None and total != expected)):
            raise Blocked('pagination missing, changed or exceeds budget')
        expected = total
        if len(batch) != min(page_size, total - len(items)):
            raise Blocked('incomplete page')
        for item in batch:
            key = item.get('key')
            if not token(key) or key in seen:
                raise Blocked('missing or repeated item key')
            seen.add(key)
        items.extend(batch)
        if len(items) == total:
            return items
    raise Blocked('page budget exhausted')


def pages(get, path, field, params, maximum=MAX_ISSUES):
    return pages_all(get, path, field, params, maximum=maximum, page_size=100, page_budget=20)


def latest(get, project, branch, analysis, revision, started):
    data = get('api/project_analyses/search', project=project, branch=branch, ps=1)
    entries = data.get('analyses', [])
    if not entries or entries[0].get('key') != analysis or entries[0].get('revision') != revision:
        raise Blocked('latest branch analysis/revision mismatch or concurrent scan')
    try:
        stamp = datetime.fromisoformat(entries[0]['date'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        seconds = stamp.timestamp()
    except (KeyError, TypeError, ValueError):
        raise Blocked('analysis timestamp missing') from None
    now = datetime.now(timezone.utc).timestamp()
    if not max(started - 2, now - 86400) <= seconds <= now + 2:
        raise Blocked('analysis timestamp stale or future')
    return seconds


def normalize(item, project, kind):
    component = item.get('component', '')
    if item.get('project', project) != project or not component.startswith(project + ':'):
        raise Blocked('foreign project component')
    path = component[len(project) + 1:]
    relative(path)
    line = item.get('line')
    rule = item.get('rule')
    if not token(item.get('key')) or type(line) is not int or not 1 <= line <= 10000000:
        raise Blocked('finding has no usable file/line; manual triage required')
    if not isinstance(rule, str) or not RULE.fullmatch(rule):
        raise Blocked('finding has no usable rule; manual triage required')
    return {'id': item['key'], 'kind': kind, 'path': path, 'line': line, 'rule': rule}


def collect(get, project, branch, task_id, revision, started, attempts=60, sleep=time.sleep):
    if not token(task_id) or not 1 <= attempts <= 120:
        raise Blocked('invalid CE task or polling budget')
    for attempt in range(attempts):
        task = get('api/ce/task', id=task_id).get('task', {})
        if task.get('id') != task_id or task.get('componentKey') != project:
            raise Blocked('CE task identity mismatch')
        status = task.get('status')
        if status == 'SUCCESS':
            break
        if status not in ('PENDING', 'IN_PROGRESS'):
            raise Blocked('CE task failed, canceled or malformed')
        if attempt + 1 < attempts:
            sleep(2)
    else:
        raise Blocked('CE polling budget exhausted')
    analysis = task.get('analysisId')
    if not token(analysis):
        raise Blocked('CE analysisId missing')
    stamp = latest(get, project, branch, analysis, revision, started)
    gate = get('api/qualitygates/project_status', analysisId=analysis).get('projectStatus', {}).get('status')
    if gate not in ('OK', 'ERROR'):
        raise Blocked('quality gate unavailable')
    keys = ('coverage', 'duplicated_lines_density', 'code_smells', 'vulnerabilities',
            'security_hotspots', 'security_hotspots_reviewed')
    component = get('api/measures/component', component=project, branch=branch,
                    metricKeys=','.join(keys)).get('component', {})
    if component.get('key') != project:
        raise Blocked('metrics project mismatch')
    measures = {}
    for measure in component.get('measures', []):
        key = measure.get('metric')
        if key not in keys or key in measures:
            raise Blocked('unexpected or duplicate metric')
        try:
            value = float(measure['value'])
        except (KeyError, ValueError, TypeError):
            raise Blocked('metric has no numeric value') from None
        if not math.isfinite(value) or value < 0:
            raise Blocked('invalid metric value')
        if key in ('coverage', 'duplicated_lines_density', 'security_hotspots_reviewed'):
            if value > 100:
                raise Blocked('percentage out of range')
        elif not value.is_integer():
            raise Blocked('count must be an integer')
        measures[key] = value
    # Sonar omits this percentage when there are explicitly no hotspots.
    if measures.get('security_hotspots') == 0:
        measures.setdefault('security_hotspots_reviewed', 100.0)
    if set(measures) != set(keys):
        raise Blocked('required metric missing; not zero')
    issues = pages(get, 'api/issues/search', 'issues',
                   dict(componentKeys=project, branch=branch, resolved='false'))
    hotspots = pages(get, 'api/hotspots/search', 'hotspots', dict(projectKey=project, branch=branch))
    normalized = []
    for item in issues:
        kind = {'CODE_SMELL': 'smells', 'VULNERABILITY': 'security', 'BUG': 'smells'}.get(item.get('type'))
        if not kind:
            raise Blocked('unsupported issue taxonomy; explicit adapter update required')
        normalized.append(normalize(item, project, kind))
    unreviewed = 0
    for item in hotspots:
        status, resolution = item.get('status'), item.get('resolution')
        if status == 'TO_REVIEW':
            unreviewed += 1
            normalized.append(normalize(item, project, 'security'))
        elif status != 'REVIEWED' or resolution not in ('SAFE', 'FIXED'):
            raise Blocked('hotspot disposition unresolved or unsupported')
    if len(normalized) > MAX_ISSUES:
        raise Blocked('combined job budget exceeded')
    latest(get, project, branch, analysis, revision, started)
    return dict(analysis_id=analysis, task_id=task_id, project=project, branch=branch,
                timestamp=stamp, gate=gate, measures=measures, issues=normalized,
                unreviewed_hotspots=unreviewed,
                global_pass=(gate == 'OK' and measures['coverage'] > 90
                             and measures['duplicated_lines_density'] < 5
                             and measures['code_smells'] == measures['vulnerabilities'] == 0
                             and measures['security_hotspots_reviewed'] == 100
                             and unreviewed == 0 and not issues))
