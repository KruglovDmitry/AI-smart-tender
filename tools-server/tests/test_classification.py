import json

from app.table_tool import classification as c, jobs


def test_official_snapshot_has_specific_relay_types_and_preserves_provenance():
    data = c.snapshot()
    records = {e['code']: e for e in data['entries']}
    assert len(records) > 20000
    assert data['version'] == '148/2026' and data['url'].startswith('https://rosstat.gov.ru/')
    assert records['27.12.24.110']['title'] == 'Реле управления промежуточные'
    assert records['27.12.24.190']['title'] == 'Реле прочие'


def test_example_code_seeds_siblings_without_forcing_same_code():
    options = c.candidates('Реле управления промежуточное', ['27.12.24.190'])
    assert {'27.12.24.110', '27.12.24.190'} <= {r['code'] for r in options}


def test_classification_only_job_needs_no_catalog_quote(monkeypatch):
    def chat(messages):
        assert 'candidate' in messages[0]['content']
        payload = json.loads(messages[1]['content'])
        assert payload['catalog_entries']['F']['27.12.24.110']['title'] == 'Реле управления промежуточные'
        assert isinstance(payload['tasks'][0]['candidates']['F'][0], str)
        return json.dumps({'cells': [{'row': 5, 'column': 'F', 'code': '27.12.24.110',
                                     'confidence': 'high', 'reason': 'Промежуточное реле управления'}]})
    monkeypatch.setattr(jobs, '_chat', chat)
    job = {'spec': {'outputs': {'F': {'header': 'F (Код ОКПД2)'}}, 'instruction': 'Подбери код',
                    'examples': [{'output': {'F': '27.12.24.190'}}]}}
    rows, _ = jobs._run_batch(job, [{'row': 5, 'input': {'Тип': 'Реле управления промежуточное'}, 'lookup': 'X'}], {}, {})
    assert rows[0]['values']['F'] == '27.12.24.110'
    assert rows[0]['confidence'] == 'low'
    assert job['classification'][0]['source']['version'] == '148/2026'


def test_invented_or_disallowed_code_is_not_written():
    batch = [{'row': 5, 'input': {'Тип': 'Реле'}, 'lookup': 'X'}]
    for code in ['99.99.99.999', '27.12.24.110']:
        rows, audit, issues = c.classify(batch, [], {'F': {'allowed': ['27.12.24.190']}},
            [{'output': {'F': '27.12.24.190'}}],
            lambda _: json.dumps({'cells': [{'row': 5, 'column': 'F', 'code': code, 'reason': 'test'}]}), json.loads)
        assert rows == [] and not audit and issues
