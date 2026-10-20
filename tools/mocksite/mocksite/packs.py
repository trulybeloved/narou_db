"""Site packs compile complete state snapshots into ordinary HTTP routes.

Third-party packages can register a compiler via the ``mocksite.packs`` entry
point group. A compiler accepts a state dict and returns a list of route dicts.
"""

from copy import deepcopy
from html import escape
from importlib.metadata import entry_points


def narou_routes(state):
    chapters = state.get("chapters", [])
    page_size = state.get("page_size", 2)
    if not isinstance(page_size, int) or isinstance(page_size, bool) or page_size < 1:
        raise ValueError("page_size must be a positive integer")
    legacy = state.get("layout", "current") == "legacy"
    index_class = "index_box" if legacy else "p-eplist"
    entry_class = "novel_sublist2" if legacy else "p-eplist__sublist"
    update_class = "long_update" if legacy else "p-eplist__update"
    routes = []

    def route(path, body, query=None):
        return {"method": "GET", "path": path, "query": query or {},
                "responses": [{"body": '<!doctype html><html lang="ja"><meta charset="utf-8">'
                               + body + '</html>'}]}

    pages = max(1, (len(chapters) + page_size - 1) // page_size)
    for page in range(1, pages + 1):
        entries = []
        for chapter in chapters[(page - 1) * page_size:page * page_size]:
            uid, title = chapter["uid"], escape(chapter["title"])
            published = escape(chapter.get("published", "2025/01/01 12:00"))
            edited = chapter.get("edited")
            marker = f'<span title="{escape(edited, quote=True)} 改稿">（<u>改</u>）</span>' if edited else ''
            entries.append(f'<div class="{entry_class}"><a class="p-eplist__subtitle" '
                           f'href="/n2267be/{uid}/">{title}</a>'
                           f'<div class="{update_class}">{published}{marker}</div></div>')
        links = ''.join(f'<a href="/n2267be/?p={n}">{n}</a> ' for n in range(1, pages + 1))
        body = f'<h1>模擬小説</h1><div class="{index_class}">' + ''.join(entries) + f'</div><nav>{links}</nav>'
        routes.append(route('/n2267be/', body, {"p": str(page)}))
        if page == 1:
            routes.append(route('/n2267be/', body))
    served_uids = set()
    for chapter in chapters:
        if type(chapter["uid"]) is not int or chapter["uid"] < 1:
            raise ValueError("chapter uid must be a positive integer")
        if chapter['uid'] in served_uids:
            continue
        served_uids.add(chapter['uid'])
        title_class = 'novel_subtitle' if legacy else 'p-novel__title'
        body_attr = 'id="novel_honbun"' if legacy else 'class="p-novel__body"'
        # body_html is intentionally raw, permitting malformed-DOM scenarios.
        body = f'<h1 class="{title_class}">{escape(chapter["title"])}</h1>'
        body += f'<aside>前書き</aside><div {body_attr}>{chapter["body_html"]}</div><aside>後書き</aside>'
        routes.append(route(f'/n2267be/{chapter["uid"]}/', body))
    # Overrides replace matching generated routes, making fault fixtures explicit.
    for override in state.get("routes", []):
        key = lambda r: (r.get('method', 'GET'), r['path'], r.get('query', {}))
        routes = [r for r in routes if key(r) != key(override)]
        routes.append(override)
    return routes


def compile_routes(pack, state):
    if pack == "static":
        return state["routes"]
    if pack == "narou":
        return narou_routes(state)
    matches = list(entry_points(group="mocksite.packs", name=pack))
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous site pack: {pack}")
    return matches[0].load()(state)


def narou_scenario():
    chapters = [
        {"uid": 1, "title": "第一章１　始まり", "body_html": '<p>　静かな朝。</p><p><ruby>物語<rp>（</rp><rt>ものがたり</rt><rp>）</rp></ruby>が始まる。</p>'},
        {"uid": 2, "title": "第一章２　旅立ち", "body_html": '<p>　道を歩く。</p><p></p><p>次の町へ。</p>'},
        {"uid": 3, "title": "第一章３　出会い", "body_html": '<p>　友と出会う。</p>'},
    ]
    base = {"chapters": chapters, "page_size": 2}
    states = {name: deepcopy(base) for name in (
        'baseline', 'unchanged', 'appended', 'revised', 'title-change', 'body-only',
        'removed-listing', 'pagination-boundary', 'missing-selector', 'malformed-timestamp',
        'duplicate-entry', 'incomplete-index', 'legacy', '404', '429', '500', 'redirect',
        'delayed', 'fail-then-recover')}
    extra = {"uid": 4, "title": "第一章４　帰還", "body_html": '<p>　家に帰る。</p>', "published": "2025/01/02 12:00"}
    states['appended']['chapters'].append(extra)
    states['pagination-boundary']['chapters'] += [extra, {**extra, "uid": 5, "title": "第一章５　明日"}]
    states['revised']['chapters'][1].update(edited="2025/01/03 09:30", body_html='<p>　改稿された道。</p>')
    states['title-change']['chapters'][1]['title'] = '第一章２　新しい旅立ち'
    states['body-only']['chapters'][1]['body_html'] = '<p>　告知されない変更。</p>'
    states['removed-listing']['chapters'].pop()
    states['malformed-timestamp']['chapters'][0]['published'] = 'invalid date'
    states['duplicate-entry']['chapters'].append(deepcopy(chapters[0]))
    states['legacy']['layout'] = 'legacy'
    def override(name, responses, path='/n2267be/', query=None):
        states[name]['routes'] = [{"method": "GET", "path": path,
                                  "query": {"p": "1"} if query is None else query,
                                  "responses": responses}]
    override('missing-selector', [{"body": '<h1>Layout changed</h1>'}])
    override('incomplete-index', [{"body": '<h1>Unavailable index page</h1>'}], query={"p": "2"})
    for status in (404, 429, 500):
        override(str(status), [{"status": status, "headers": {"Retry-After": "1"} if status == 429 else {}, "body": 'Unavailable'}])
    override('redirect', [{"status": 302, "headers": {"Location": '/n2267be/'}}])
    good = narou_routes(base)[0]['responses'][0]
    override('delayed', [{**good, "delay_ms": 250}])
    override('fail-then-recover', [{"status": 500, "body": 'Temporary failure'}, good])
    return {"schema_version": 1, "id": "narou", "site_pack": "narou", "initial_state": "baseline", "states": states}
