"""Durable review gates. Called in the rendering thread, never via a UI timer."""
from copy import deepcopy

REVISION = 'first_export_review_v1'


def blocked(batch):
    return batch.get('review_session', {}).get('state') == 'waiting'


def observe(batch, item, pause):
    if not batch.get('config', {}).get('review_first_enabled'):
        return False
    session = batch.setdefault('review_session', dict(revision=REVISION, state='armed'))
    if session['state'] != 'armed' or item.get('status') != 'completed':
        return False
    session.update(state='waiting', first_item_id=item['id'],
                   first_output=item.get('output_path'), first_number=item.get('number'))
    if pause is not None:
        pause.set()
    return True


def resume(batch):
    result = deepcopy(batch)
    session = result.setdefault('review_session', dict(revision=REVISION))
    session['state'] = 'accepted'
    return result
