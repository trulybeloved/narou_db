"""Shared Discord embed formatting for live notifications and samples."""

from datetime import datetime, timezone


STATES = {
    'publication': (
        '📚 Chapters published', 0x57F287,
        'New or modified chapters have been published successfully.',
        'Healthy', 'The next update will run on the normal schedule.',
    ),
    'retry': (
        '⏳ Update will retry', 0xFEE75C,
        'The latest update could not complete. An automatic retry is scheduled.',
        'Retrying', 'No action needed while automatic retries continue.',
    ),
    'continuing-failure': (
        '⚠️ Updates still failing', 0xE67E22,
        'The updater is still encountering failures. Automatic retries are continuing.',
        'Degraded', 'Check the updater logs if failures persist. Reminders are sent at most hourly.',
    ),
    'recovery': (
        '✅ Scraper recovered', 0x1ABC9C,
        'A complete update cycle succeeded. Scraping and publication are healthy again.',
        'Recovered', 'Normal scheduling has resumed.',
    ),
    'fatal': (
        '🛑 Updater stopped', 0xED4245,
        'The updater encountered a fatal failure and stopped scheduling updates.',
        'Stopped', 'Inspect the updater logs, resolve the issue, and restart the service.',
    ),
}


def notification_payload(state: str, *, ping: str = '', sample: bool = False,
                         changed_chapters: int | None = None, failures: int | None = None,
                         error_category: str | None = None, phase: str | None = None,
                         commit_url: str | None = None) -> dict:
    title, color, description, status, action = STATES[state]
    fields = [{'name': 'Status', 'value': status, 'inline': True}]
    for name, value in (
        ('Changed chapters', changed_chapters),
        ('Failures before recovery' if state == 'recovery' else 'Consecutive failures', failures),
        ('Error category', error_category), ('Phase', phase),
    ):
        if value is not None:
            fields.append({'name': name, 'value': str(value)[:1024] or 'Unknown', 'inline': True})
    if state == 'publication' and commit_url:
        fields.append({'name': 'Published commit', 'value': f'[View commit]({commit_url})', 'inline': False})
    fields.append({'name': 'Next step', 'value': action, 'inline': False})
    payload = {
        'embeds': [{
            'title': f'[SAMPLE] {title}' if sample else title,
            'description': description,
            'color': color,
            'fields': fields,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'footer': {'text': 'NarouDB autoscraper' + (' • Sample notification' if sample else '')},
        }],
        'allowed_mentions': {'parse': []},
    }
    if ping.isdigit():
        payload['content'] = f'<@{ping}>'
        payload['allowed_mentions']['users'] = [ping]
    return payload
