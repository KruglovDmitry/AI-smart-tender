import json

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
    monkeypatch.setattr(evidence, 'research', lambda *a: ({1: 'Product supply 5V DC'}, [{'tool': 'read', 'pages': [1]}]))
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
