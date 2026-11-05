from pathlib import Path
import hashlib
import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.workbook.defined_name import DefinedName
from test_doc_tools import client


def _book(path):
    w=Workbook();s=w.active;s.title='Items'
    s.append(['Article','Description','Result','Code'])
    s.append(['00123','human','=1+1',123]);s['D2'].number_format='00000'
    s.append(['NA',None,None,'NULL'])
    w.save(path);w.close()


@pytest.mark.parametrize('suffix,encoding,delimiter',[('.csv','utf-8-sig',';'),('.tsv','utf-16','\t'),('.csv','cp1251',',')])
def test_csv_preserves_identifiers_across_tools_and_upload(client,suffix,encoding,delimiter):
    c,root=client;path=root/('codes'+suffix)
    text=f'article{delimiter}description\r\n00123{delimiter}"строка\nвторая"\r\nNA{delimiter}NULL\r\n'
    path.write_bytes(text.encode(encoding))
    q=c.post('/query_table',json={'path':path.name,'header_row':1}).json()
    assert q['rows'][0]['values']==['00123','строка\nвторая']
    assert q['rows'][1]['values']==['NA','NULL']
    doc=c.post('/read_document',json={'path':path.name}).json()
    assert '00123' in doc['content'] and 'NULL' in doc['content']
    upload=c.put('/process',content=path.read_bytes(),headers={'X-Filename':path.name})
    assert upload.status_code==200,upload.text
    assert '00123' in upload.json()[0]['page_content']


def test_formula_modes_and_empty_filter(client):
    c,root=client;_book(root/'t.xlsx')
    skill=c.get('/document_skill',params={'file_type':'spreadsheets'})
    assert skill.status_code==200 and 'overwrite_existing' in skill.json()['instructions']
    args={'path':'t.xlsx','header_row':1,'read_mode':'both'}
    q=c.post('/query_table',json=args).json()
    assert q['rows'][0]['values']==['00123','human',None,'00123']
    assert q['formula_cells'][0]['formula']=='=1+1'
    assert q['formula_cells'][0]['cache_missing']
    assert q['raw_numeric_cells'][0]['raw_value']==123
    q=c.post('/query_table',json={**args,'read_mode':'formulas'}).json()
    assert q['rows'][0]['values'][2]=='=1+1'
    empty=c.post('/query_table',json={**args,'filters':[{'column':'C','op':'empty'}]}).json()
    assert [r['row'] for r in empty['rows']]==[3]
    d=c.post('/describe_table',json={'path':'t.xlsx'}).json()
    assert d['details'][0]['formula_cells']==1


def test_explicit_editing_formula_replacement_and_clearing(client):
    c,root=client;_book(root/'t.xlsx');before=(root/'t.xlsx').read_bytes()
    payload={'path':'t.xlsx','header_row':1,'updates':[{'row':2,'values':{'B':'edited','C':'replace'}}]}
    protected=c.post('/fill_table',json=payload).json()
    assert protected['cells_written']==0 and protected['problems_total']==2
    edited=c.post('/fill_table',json={**payload,'overwrite_existing':True}).json()
    assert edited['cells_written']==1 and edited['problems_total']==1
    done=c.post('/fill_table',json={**payload,'overwrite_existing':True,'replace_formulas':True}).json()
    assert done['cells_written']==2 and done['verification']['reopened']
    clear=c.post('/fill_table',json={'path':done['path'],'header_row':1,'overwrite_existing':True,'updates':[{'row':2,'values':{'B':None}}]}).json()
    w=load_workbook(root/clear['path']);assert w['Items']['B2'].value is None;w.close()
    assert (root/'t.xlsx').read_bytes()==before


def test_saved_copy_preserves_template_features(client):
    c,root=client;_book(root/'template.xlsx')
    w=load_workbook(root/'template.xlsx');s=w['Items']
    s.merge_cells('F1:G1');s['F1']='Heading';s['A2'].font=Font(bold=True)
    s['A2'].fill=PatternFill('solid',fgColor='FFFF00')
    s.freeze_panes='B2';s.auto_filter.ref='A1:D3';s.column_dimensions['B'].width=25;s.row_dimensions[2].height=30
    dv=DataValidation(type='list',formula1='"a,b"');s.add_data_validation(dv);dv.add(s['B3'])
    w.defined_names.add(DefinedName('Articles',attr_text="'Items'!$A$2:$A$3"))
    other=w.create_sheet('Reference');other['A1']='keep';other['B1']='=1+2'
    w.save(root/'template.xlsx');w.close()
    response=c.post('/fill_table',json={'path':'template.xlsx','header_row':1,'updates':[{'row':3,'values':{'B':'a'}}]})
    assert response.status_code==200,response.text
    out=response.json();assert out['verification']['reopened'] and not out['verification']['recalculated']
    result=load_workbook(root/out['path']);s=result['Items']
    assert str(next(iter(s.merged_cells.ranges)))=='F1:G1'
    assert s['A2'].font.bold and s['B3'].value=='a'
    assert s.freeze_panes=='B2' and s.auto_filter.ref=='A1:D3'
    assert len(s.data_validations.dataValidation)==1 and 'Articles' in result.defined_names
    assert result['Reference']['B1'].value=='=1+2';result.close()


def test_verification_failure_keeps_previous_output(client,monkeypatch):
    c,root=client;(root/'exports').mkdir();path=root/'exports/out.xlsx';_book(path);before=path.read_bytes()
    original=Workbook.save
    def corrupt(wb,target):
        original(wb,target)
        check=load_workbook(target);check.active['A2']='damaged';original(check,target);check.close()
    monkeypatch.setattr(Workbook,'save',corrupt)
    r=c.post('/fill_table',json={'path':'exports/out.xlsx','header_row':1,'updates':[{'row':3,'values':{'B':'new'}}]})
    assert r.status_code==400,r.text
    assert path.read_bytes()==before
    assert not list(path.parent.glob('.table-*'))


def test_macro_editing_is_rejected_before_copy(client):
    c,root=client;_book(root/'macro.xlsm')
    out=c.post('/fill_table',json={'path':'macro.xlsm','updates':[{'row':3,'values':{'B':'new'}}]})
    assert out.status_code==400 and 'XLSM' in out.json()['detail']
    assert not (root/'exports').exists()


def test_create_excel_is_verified_and_outputs_are_unique(client):
    c,root=client;paths=[]
    payload={'filename':'result.xlsx','sheets':[{'name':'Result','headers':['Article','Formula'],'rows':[['00123','=1+1']]}]}
    for _ in range(3):
        r=c.post('/write_excel',json=payload)
        assert r.status_code==200,r.text
        out=r.json();paths.append(out['path']);assert out['verification']['reopened']
    assert len(set(paths))==3
    w=load_workbook(root/paths[-1]);assert w.active['A2'].value=='00123';assert w.active['B2'].value=='=1+1';w.close()
