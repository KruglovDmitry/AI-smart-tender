import json
import pytest

from app.table_tool.evidence import verify, research


def test_invalid_quote_or_dictionary_value_never_reaches_review():
    def forbidden(*args):
        raise AssertionError('No valid cells should reach the model')
    rows = [{'row': 5, 'values': {'D': '24V', 'K': 'invented'},
             'evidence': {'D': [{'page': 1, 'quote': 'fabricated quote 24V'}],
                          'K': [{'page': 1, 'quote': 'Product type relay'}]}}]
    result, issues = verify(rows, {'D': {}, 'K': {'allowed': ['relay']}},
                            {1: 'Product type relay, supply 5V DC'}, '', forbidden, json.loads)
    assert result[0]['values'] == {} and len(issues) == 2


def test_independent_review_rejects_wrong_rating_and_missing_verdict():
    rows = [{'row': 5, 'values': {'D': '6V AC', 'K': 'relay'}, 'evidence': {
        c: [{'page': 1, 'quote': 'Product supply 5V DC'}] for c in ['D', 'K']}}]
    result, issues = verify(rows, {'D': {}, 'K': {}}, {1: 'Product supply 5V DC'}, '',
        lambda _: json.dumps({'cells': [{'row': 5, 'column': 'D', 'supported': False}]}), json.loads)
    assert result[0]['values'] == {} and len(issues) == 2


def test_approved_value_keeps_source_quote():
    row = {'row': 5, 'values': {'D': '5V DC'}, 'evidence': {'D': [{'page': 1, 'quote': 'Product supply 5V DC'}]}}
    result, issues = verify([row], {'D': {}}, {1: 'Product supply 5V DC'}, '',
        lambda _: '{"cells":[{"row":5,"column":"D","supported":true}]}', json.loads)
    assert result[0]['values'] == {'D': '5V DC'} and not issues


def test_research_can_escape_accessory_page(monkeypatch):
    from app import doc_index
    class Index:
        pages = [1, 2]
        def text(self, n, **kwargs):
            return ['Accessory socket compatible with X123', 'Product X123 order code: 5V DC'][n-1], 'text'
    monkeypatch.setattr(doc_index, 'get_index', lambda _: Index())
    monkeypatch.setattr(doc_index, 'search_document', lambda *a, **k: {'results': [{'page': 1}]})
    answers = iter(['{"requests":[{"tool":"read","pages":[2]}]}', '{"requests":[],"ready":true}'])
    pages, trace = research('catalog.pdf', ['X123'], lambda _: next(answers), json.loads)
    assert '5V DC' in pages[2] and trace[-1]['pages'] == [2]


def test_batch_persists_approved_evidence_and_drops_unapproved_cell(monkeypatch):
    from app.table_tool import jobs, evidence
    monkeypatch.setattr(evidence, 'research', lambda *a, **kw: ({1: 'Product supply 5V DC'}, [{'tool': 'read', 'pages': [1]}]))
    responses = iter([
        json.dumps({'rows': [{'row': 5, 'values': {'D': '5V DC', 'F': 'made-up-code'},
                    'evidence': {'D': [{'page': 1, 'quote': 'Product supply 5V DC'}]}}]}),
        '{"cells":[{"row":5,"column":"D","supported":true}]}',
    ])
    monkeypatch.setattr(jobs, '_chat', lambda _: next(responses))
    job = {'spec': {'document': 'catalog.pdf', 'instruction': 'Fill',
                   'outputs': {'D': {'header': 'Description'}, 'F': {'header': 'Code'}}}}
    rows, _ = jobs._run_batch(job, [{'row': 5, 'lookup': 'X123', 'input': {'Article': 'X123'}}], {}, {})
    assert rows[0]['values'] == {'D': '5V DC'}
    assert job['evidence'][0]['cells']['D'][0]['page'] == 1
    assert job['verification_issues'][0]['column'] == 'F'


def test_balanced_high_confidence_avoids_review_but_checks_quotes():
    def forbidden(*args):
        raise AssertionError('Balanced high-confidence row needs no model review')
    row = {'row': 5, 'confidence': 'high', 'values': {'D': '5V DC', 'F': 'bad'},
           'evidence': {'D': [{'page': 1, 'quote': 'Product supply 5V DC'}]}}
    rows, issues = verify([row], {'D': {}, 'F': {}}, {1: 'Product supply 5V DC'}, '', forbidden,
                          json.loads, mode='balanced')
    assert rows[0]['values'] == {'D': '5V DC'}
    assert rows[0]['verification'] == 'quote_only' and len(issues) == 1


def test_usage_counts_actual_api_tokens(monkeypatch):
    from app.table_tool import jobs
    monkeypatch.setattr(jobs, '_llm_config', lambda: ('https://example.invalid', 'key', 'model'))
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {'choices': [{'message': {'content': '{}'}}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}
    monkeypatch.setattr(jobs.httpx, 'post', lambda *a, **kw: Response())
    metrics = dict(requests=0, prompt_tokens=0, completion_tokens=0, total_tokens=0, responses_without_usage=0)
    token = jobs._usage.set(metrics)
    try:
        assert jobs._chat([]) == '{}'
    finally:
        jobs._usage.reset(token)
    assert metrics['requests'] == 1 and metrics['total_tokens'] == 120


@pytest.mark.parametrize('mode,base,disabled', [
    ('balanced', 'https://api.deepseek.com', True),
    ('strict', 'https://api.deepseek.com', False),
    ('balanced', 'https://other.example', False),
])
def test_thinking_override_is_scoped(monkeypatch, mode, base, disabled):
    from app.table_tool import jobs
    monkeypatch.setattr(jobs, '_llm_config', lambda: (base, 'key', 'deepseek-flash'))
    sent = {}
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': '{}'}}]}
    def post(*args, **kwargs):
        sent.update(kwargs['json'])
        return Response()
    monkeypatch.setattr(jobs.httpx, 'post', post)
    token = jobs._quality.set(mode)
    try:
        jobs._chat([])
    finally:
        jobs._quality.reset(token)
    assert ('thinking' in sent) is disabled
    if disabled:
        assert sent['thinking'] == {'type': 'enabled'} and sent['max_tokens'] == 16384
        assert sent['reasoning_effort'] == 'low'


def test_balanced_disputed_value_is_retained_and_flagged():
    row = {'row': 5, 'confidence': 'medium', 'values': {'D': '5V DC'},
           'evidence': {'D': [{'page': 1, 'quote': 'Product supply 5V DC'}]}}
    rows, issues = verify([row], {'D': {}}, {1: 'Product supply 5V DC'}, '',
        lambda _: '{"cells":[{"row":5,"column":"D","supported":false}]}', json.loads, mode='balanced')
    assert rows[0]['values'] == {'D': '5V DC'}
    assert rows[0]['confidence'] == 'low' and rows[0]['verification'] == 'needs_review' and issues
