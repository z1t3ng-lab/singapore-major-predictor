from fastapi.testclient import TestClient
from app import database
from app.main import app

def test_preview_scenarios_backup_restore_and_map_normalization(tmp_path, monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'workspace.sqlite3')
    with TestClient(app) as c:
        c.post('/api/demo')
        initial=c.get('/api/workspace/state').json()['version']
        preview=c.post('/api/workspace/preview',json={'payload':{'teams':[{'id':'new','name':'Preview team','seed':1,'stage_entry':'stage1'}]}})
        assert preview.status_code==200,preview.text
        assert len(c.get('/api/teams').json()['teams'])==32
        assert c.get('/api/workspace/state').json()['version']==initial
        snap=c.post('/api/workspace/backups').json()['id']
        r=c.post('/api/simulate',json={'simulations':4,'seed':73}).json()
        assert c.post('/api/workspace/scenarios',json={'name':'My picks','run_id':r['run_id']}).status_code==200
        assert len(c.get('/api/workspace/scenarios').json())==1
        comparison=c.get('/api/workspace/compare',params={'before':r['run_id'],'after':r['run_id']}).json()
        assert all(x['change']==0 for x in comparison['odds'])
        maps=c.post('/api/workspace/maps',json={'team_a_id':'demo-01','team_b_id':'demo-02','maps':['a','b','c'],'cutoff':'2026-10-22T00:00:00Z'})
        assert maps.status_code==200,maps.text
        p=maps.json();assert abs(p['team_a_probability']+p['team_b_probability']-1)<1e-9
        assert c.get('/api/workspace/performance').json()['count']==0
        assert c.post(f'/api/workspace/backups/{snap}/restore',json={'confirm':False,'expected_state_version':initial}).status_code==422
        assert c.post(f'/api/workspace/backups/{snap}/restore',json={'confirm':True,'expected_state_version':initial}).status_code==200
        assert c.get('/api/workspace/history').json()==[]
        assert c.get('/api/workspace/state').json()['version']>initial

def test_failed_preview_is_read_only_and_import_staleness(tmp_path, monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'workspace.sqlite3')
    with TestClient(app) as c:
        c.post('/api/demo')
        with database.connect() as db:
            logs = db.execute('SELECT COUNT(*) FROM source_log').fetchone()[0]
        invalid = c.post('/api/workspace/preview',json={'payload':{'matches':[{'id':'bad'}]}})
        assert invalid.status_code == 422
        with database.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM source_log').fetchone()[0] == logs
        payload={'teams':[{'id':'demo-01','name':'Edited demonstration team','seed':1}]}
        preview=c.post('/api/workspace/preview',json={'payload':payload}).json()
        version=preview['state_version']
        assert c.post(f'/api/import/json?expected_state_version={version}',json=payload).status_code==200
        assert c.post(f'/api/import/json?expected_state_version={version}',json=payload).status_code==409
        assert next(t for t in c.get('/api/teams').json()['teams'] if t['id']=='demo-01')['synthetic']
        assert c.get('/api/workspace/backups').json()


def test_saved_scenario_recomputes_confirmed_upset_and_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'workspace.sqlite3')
    with TestClient(app) as c:
        c.post('/api/demo')
        initial=c.post('/api/simulate',json={'simulations':8,'seed':73}).json()
        key=c.post('/api/workspace/scenarios',json={'name':'Original picks','run_id':initial['run_id']}).json()['id']
        match=initial['projected_path']['stages']['stage1']['rounds'][0]['matches'][0]
        loser=match['loser_id']
        confirmed=c.put(f"/api/matches/{match['id']}/result",json={'winner_id':loser,'expected_result_version':0})
        assert confirmed.status_code==200,confirmed.text
        assert c.get('/api/workspace/scenarios').json()[0]['stale']
        rerun=c.post(f'/api/workspace/scenarios/{key}/run')
        assert rerun.status_code==200,rerun.text
        assert rerun.json()['projected_path']['stages']['stage1']['rounds'][0]['matches'][0]['winner_id']==loser
        assert not c.get('/api/workspace/scenarios').json()[0]['stale']
        profile=c.get(f'/api/workspace/teams/{loser}').json()
        assert profile['path'] and profile['head_to_head'][0]['wins']==1
        diff=c.get('/api/workspace/compare',params={'before':initial['run_id'],'after':rerun.json()['run_id']}).json()
        assert diff['removed_matches'] and diff['added_matches']


def test_performance_uses_earliest_prematch_forecast_and_separates_demo(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import json, math
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'workspace.sqlite3')
    with TestClient(app) as c:
        c.post('/api/demo')
        first=c.post('/api/simulate',json={'simulations':2}).json()
        match=first['projected_path']['stages']['stage1']['rounds'][0]['matches'][0]
        p=match['prediction']['team_a_probability']
        response=c.put(f"/api/matches/{match['id']}/result",json={'winner_id':match['team_b_id'],'expected_result_version':0})
        assert response.status_code==200,response.text
        now=datetime.now(timezone.utc)
        with database.connect() as db:
            db.execute('UPDATE prediction_runs SET created_at=? WHERE id=?',((now-timedelta(days=2)).isoformat(),first['run_id']))
            # Also exercise naive source timestamps, which normalize to UTC.
            db.execute('UPDATE matches SET played_at=? WHERE id=?',((now-timedelta(days=1)).replace(tzinfo=None).isoformat(),match['id']))
        assert c.get('/api/workspace/performance').json()['count']==0
        scores=c.get('/api/workspace/performance?include_demo=true').json()
        assert scores['count']==1
        assert abs(scores['brier']-p*p)<1e-10
        assert abs(scores['log_loss']+math.log(1-p))<1e-10
        assert sum(b['count'] for b in scores['bins'])==1
        # A record with missing match time must never produce an invented accuracy.
        with database.connect() as db: db.execute('UPDATE matches SET played_at=NULL')
        assert c.get('/api/workspace/performance?include_demo=true').json()['count']==0


def test_stage_settings_and_stale_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'workspace.sqlite3')
    with TestClient(app) as c:
        version=c.get('/api/workspace/state').json()['version']
        snapshot=c.post('/api/workspace/backups').json()['id']
        settings={'expected_state_version':version,'opening_best_of':3,'decider_best_of':3,'start_date':'2026-11-25','end_date':'2026-11-27'}
        assert c.put('/api/workspace/stages/stage1',json=settings).status_code==200
        stage=c.get('/api/workspace/stages').json()[0]
        assert stage['rules']['opening_match_best_of']==3
        assert c.post(f'/api/workspace/backups/{snapshot}/restore',json={'confirm':True,'expected_state_version':version}).status_code==409
        assert c.put('/api/workspace/stages/stage1',json=settings).status_code==409

def test_map_explorer_cutoff_and_series_probability(tmp_path, monkeypatch):
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'workspace.sqlite3')
    with TestClient(app) as c:
        c.post('/api/demo')
        payload={'matches':[{'id':'map-history','team_a_id':'demo-01','team_b_id':'demo-02','winner_id':'demo-01','best_of':1,'status':'completed','played_at':'2026-10-21T12:00:00Z','available_at':'2026-10-22T12:00:00Z'}], 'map_results':[{'match_id':'map-history','map_name':'de_nuke','winner_id':'demo-01','team_a_score':13,'team_b_score':7}]}
        response=c.post('/api/import/json',json=payload)
        assert response.status_code==200,response.text
        request={'team_a_id':'demo-01','team_b_id':'demo-02','maps':['de_nuke','de_mirage','de_inferno'],'cutoff':'2026-10-21T23:00:00Z'}
        early=c.post('/api/workspace/maps',json=request).json()
        assert all(m['samples_a']==0 for m in early['maps'])
        p=early['maps'][0]['probability']
        assert abs(early['team_a_probability']-(3*p*p-2*p**3))<1e-10
        request['cutoff']='2026-10-23T00:00:00Z'
        later=c.post('/api/workspace/maps',json=request).json()
        assert later['maps'][0]['samples_a']==1 and later['maps'][0]['samples_b']==1
        assert abs(later['team_a_probability']+later['team_b_probability']-1)<1e-10
        request['maps']=['de_nuke','de_nuke','de_inferno']
        assert c.post('/api/workspace/maps',json=request).status_code==422
