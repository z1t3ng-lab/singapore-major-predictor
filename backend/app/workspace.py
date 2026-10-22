"""Workspace tools built on the existing persisted forecasts and import pipeline."""
from __future__ import annotations
import json, math, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import database as db
from .engine import _confirmed_winner

router = APIRouter(prefix='/api/workspace')

def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

def matches(result):
    path = result.get('projected_path', {})
    return [m for s in path.get('stages', {}).values() for r in s['rounds'] for m in r['matches']] + [m for r in path.get('playoffs', {}).get('rounds', []) for m in r['matches']]

def backup(reason='manual'):
    folder = db.DB_PATH.parent / 'backups'; folder.mkdir(parents=True, exist_ok=True)
    name = f'{db.utc_now().replace(":", "-")}-{uuid.uuid4().hex[:8]}.sqlite3'
    with db.connect() as source, sqlite3.connect(folder / name) as target:
        source.backup(target)
    (folder / (name + '.json')).write_text(json.dumps({'reason': reason, 'created_at': db.utc_now()}))
    return name

@router.get('/backups')
def backups():
    folder = db.DB_PATH.parent / 'backups'
    return [{'id': p.name, 'bytes': p.stat().st_size, **json.loads(p.with_name(p.name+'.json').read_text())} for p in sorted(folder.glob('*.sqlite3'), reverse=True)]

@router.post('/backups')
def create_backup(): return {'id': backup()}

class Restore(BaseModel):
    confirm: bool = False
    expected_state_version: int

@router.post('/backups/{name}/restore')
def restore(name: str, request: Restore):
    if not request.confirm: raise HTTPException(422, 'Confirm restoring this backup first.')
    if Path(name).name != name: raise HTTPException(422, 'Invalid backup name')
    path = db.DB_PATH.parent / 'backups' / name
    if not path.is_file(): raise HTTPException(404, 'Backup not found')
    with db.connect() as c:
        current = db.state_version(c)
        if current != request.expected_state_version: raise HTTPException(409, 'Data changed. Refresh before restoring.')
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise HTTPException(422, 'Backup failed integrity check')
        backup('before restore')
        with db.connect() as destination: source.backup(destination)
    db.initialize_database()
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO metadata VALUES('tournament_state_version', ?)", (str(current+1),))
    return {'ok': True}

@router.get('/history')
def history():
    with db.connect() as c:
        return [dict(r) for r in c.execute('SELECT id,created_at,data_cutoff,model_name,simulations,state_version,scenario_json FROM prediction_runs ORDER BY rowid DESC LIMIT 100')]

@router.get('/runs/{run_id}')
def run(run_id: str):
    with db.connect() as c:
        row = c.execute('SELECT result_json FROM prediction_runs WHERE id=?', (run_id,)).fetchone()
        if not row: raise HTTPException(404, 'Forecast not found')
        return json.loads(row[0])

class Scenario(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    run_id: str

@router.post('/scenarios')
def save_scenario(request: Scenario):
    if not request.name.strip(): raise HTTPException(422, "Name cannot be blank")
    run(request.run_id)
    with db.connect() as c:
        key = str(uuid.uuid4())
        c.execute('INSERT INTO saved_scenarios VALUES(?,?,?,?)', (key, request.name.strip(), request.run_id, db.utc_now()))
    return {'id': key}

@router.get('/scenarios')
def scenarios():
    with db.connect() as c:
        return [{**dict(r), 'stale': r['state_version'] != db.state_version(c)} for r in c.execute('SELECT s.*,r.state_version FROM saved_scenarios s JOIN prediction_runs r ON r.id=s.run_id ORDER BY s.created_at DESC')]

@router.get('/compare')
def compare(before: str, after: str):
    a, b = run(before), run(after)
    old = {m['id']: m for m in matches(a)}; new = {m['id']: m for m in matches(b)}
    odds = []
    for team in set(a['probabilities']) | set(b['probabilities']):
        p = a['probabilities'].get(team, {}).get('champion', {}).get('probability', 0)
        q = b['probabilities'].get(team, {}).get('champion', {}).get('probability', 0)
        progression = {key: {'before':a['probabilities'].get(team,{}).get(key,{}).get('probability',0),'after':b['probabilities'].get(team,{}).get(key,{}).get('probability',0)} for key in ('stage1_advance','stage2_advance','playoffs','semifinals','final')}
        odds.append({'team_id': team, 'before': p, 'after': q, 'change': q-p, 'progression':progression})
    return {'odds': sorted(odds, key=lambda r: -abs(r['change'])), 'removed_matches': [m for k,m in old.items() if k not in new], 'added_matches': [m for k,m in new.items() if k not in old], 'stages_before': a['projected_path']['stages'], 'stages_after': b['projected_path']['stages'], 'playoffs_before': a['projected_path']['playoffs'], 'playoffs_after': b['projected_path']['playoffs'], 'note': 'Probability differences include Monte Carlo sampling variation; use the same seed and run count for comparisons.'}

@router.get('/performance')
def performance(include_demo: bool = False):
    # One earliest eligible, non-scenario forecast per actual matchup. Never score a post-result forecast.
    with db.connect() as c:
        actual = {r['id']: dict(r) for r in c.execute('SELECT * FROM matches') if _confirmed_winner(dict(r))}
        synthetic = {r[0] for r in c.execute('SELECT id FROM teams WHERE synthetic=1')}
        rows = {}; excluded = set()
        for saved in c.execute('SELECT * FROM prediction_runs ORDER BY created_at,rowid'):
            if json.loads(saved['scenario_json'] or '{}'): continue
            for match in matches(json.loads(saved['result_json'] or '{}')):
                key = match.get('fixture_id') or match['id']; outcome = actual.get(key)
                if not outcome or key in rows: continue
                if {outcome['team_a_id'],outcome['team_b_id']} != {match['team_a_id'],match['team_b_id']}: continue
                if not include_demo and (outcome['team_a_id'] in synthetic or outcome['team_b_id'] in synthetic): continue
                deadline = outcome['played_at']
                if not deadline or timestamp(saved['created_at']) >= timestamp(deadline) or match.get('confirmed_winner_id'): excluded.add(key); continue
                p = match['prediction']['team_a_probability']; y = int(_confirmed_winner(outcome) == match['team_a_id'])
                rows[key] = {'match_id': key, 'team_a_id': match['team_a_id'], 'team_b_id': match['team_b_id'], 'probability': p, 'winner_id': _confirmed_winner(outcome), 'forecast_at': saved['created_at'], 'correct': (p >= .5) == bool(y), 'brier': (p-y)**2, 'log_loss': -math.log(max(1e-12, p if y else 1-p)), 'observed': y, 'timing': 'before match time'}
        values = list(rows.values()); n = len(values)
        bins=[]
        for i in range(5):
            group=[r for r in values if min(4,int(r['probability']*5))==i]
            bins.append({'range': f'{i*20}–{(i+1)*20}%', 'count': len(group), 'predicted': sum(r['probability'] for r in group)/len(group) if group else None, 'observed': sum(r['observed'] for r in group)/len(group) if group else None})
        return {'count': n, 'accuracy': sum(r['correct'] for r in values)/n if n else None, 'brier': sum(r['brier'] for r in values)/n if n else None, 'log_loss': sum(r['log_loss'] for r in values)/n if n else None, 'bins': bins, 'matches': values, 'excluded': len(excluded-set(rows)), 'note': 'Saved displayed probabilities, including context adjustments. Default evaluation excludes synthetic teams and results with unknown match times. Only forecasts recorded before the match are scored.'}

@router.get('/teams/{team_id}')
def profile(team_id: str):
    with db.connect() as c:
        team = c.execute('SELECT * FROM teams WHERE id=?', (team_id,)).fetchone()
        if not team: raise HTTPException(404, 'Team not found')
        roster = [dict(r) for r in c.execute('SELECT p.name,rm.* FROM roster_memberships rm JOIN players p ON p.id=rm.player_id WHERE team_id=? ORDER BY started_at DESC', (team_id,))]
        recent = [dict(r) for r in c.execute('SELECT * FROM matches WHERE team_a_id=? OR team_b_id=? ORDER BY played_at DESC LIMIT 50', (team_id,team_id))]
        maps = [dict(r) for r in c.execute('SELECT mr.*,m.played_at FROM map_results mr JOIN matches m ON m.id=mr.match_id WHERE m.team_a_id=? OR m.team_b_id=? ORDER BY m.played_at DESC', (team_id,team_id))]
        stats = [dict(r) for r in c.execute("SELECT ps.*,p.name FROM player_stats ps JOIN players p ON p.id=ps.player_id WHERE ps.player_id IN (SELECT player_id FROM roster_memberships WHERE team_id=?) ORDER BY ps.available_at DESC LIMIT 100", (team_id,))]
        h2h = {}
        for match in recent:
            winner = _confirmed_winner(match)
            if not winner: continue
            opponent = match['team_b_id'] if match['team_a_id'] == team_id else match['team_a_id']
            row = h2h.setdefault(opponent, {'opponent': opponent, 'wins': 0, 'losses': 0})
            row['wins' if winner == team_id else 'losses'] += 1
        latest = c.execute('SELECT result_json FROM prediction_runs WHERE state_version=? ORDER BY rowid DESC LIMIT 1', (db.state_version(c),)).fetchone()
        forecast = json.loads(latest[0]) if latest and latest[0] else None
        path = [m for m in matches(forecast) if team_id in (m['team_a_id'],m['team_b_id'])] if forecast else []
        return {'team':dict(team),'roster':roster,'recent':recent,'maps':maps,'player_stats':stats,'head_to_head':list(h2h.values()),'path':path,'probabilities':forecast['probabilities'].get(team_id,{}) if forecast else {},'state_version':db.state_version(c)}

class MapRequest(BaseModel):
    team_a_id: str
    team_b_id: str
    maps: list[str] = Field(min_length=1,max_length=5)
    cutoff: str

@router.post('/maps')
def map_explorer(request: MapRequest):
    from .main import predict, MatchPredictionRequest, _cutoff
    if len(request.maps) not in (1,3,5) or len(set(request.maps)) != len(request.maps) or any(not m.strip() for m in request.maps): raise HTTPException(422, 'Choose 1, 3 or 5 distinct maps in playing order.')
    cutoff = _cutoff(request.cutoff)
    baseline = predict(MatchPredictionRequest(team_a_id=request.team_a_id,team_b_id=request.team_b_id,best_of=1,cutoff=cutoff))
    probabilities=[]
    with db.connect() as c:
        for map_name in request.maps:
            stats=[]
            for team in (request.team_a_id,request.team_b_id):
                rows=c.execute('''SELECT mr.winner_id FROM map_results mr JOIN matches m ON m.id=mr.match_id
                WHERE mr.map_name=? AND (m.team_a_id=? OR m.team_b_id=?) AND m.status='completed'
                AND mr.winner_id IS NOT NULL AND julianday(m.played_at)<=julianday(?) AND julianday(m.available_at)<=julianday(?)''',(map_name,team,team,cutoff,cutoff)).fetchall()
                stats.append(((sum(r[0]==team for r in rows)+4)/(len(rows)+8),len(rows)))
            p=baseline.get('base_map_win_probability_a', baseline['team_a_probability'])
            # Deliberately use map history only when both teams have observations.
            if min(s[1] for s in stats): p=1/(1+math.exp(-(math.log(p/(1-p))+.7*(stats[0][0]-stats[1][0]))))
            probabilities.append({'map':map_name,'probability':p,'samples_a':stats[0][1],'samples_b':stats[1][1]})
    needed=len(request.maps)//2+1
    states={(0,0):1.}; won=0.
    for item in probabilities:
        nxt={}
        for (a,b),weight in states.items():
            for x,y,q in ((a+1,b,item['probability']),(a,b+1,1-item['probability'])):
                if x==needed: won+=weight*q
                elif y<needed: nxt[(x,y)]=nxt.get((x,y),0)+weight*q
        states=nxt
    return {'team_a_probability':won,'team_b_probability':1-won,'maps':probabilities,'note':'Exploratory Elo map baseline plus Beta(4,4) map-history adjustment (fixed coefficient 0.7). Other context adjustments are excluded to avoid counting map history twice. This is not a validated accuracy improvement. Maps are assumed independent. Remove banned maps and enter remaining maps in playing order; this does not change official fixtures.'}

class ImportPreview(BaseModel):
    payload: dict
    replace_demo: bool = False

@router.post('/preview')
def preview(request: ImportPreview):
    from .importers import import_records, ALLOWED_RECORDS
    if set(request.payload) - set(ALLOWED_RECORDS): raise HTTPException(422, 'Unknown record type in import')
    warnings=[]
    for kind, items in request.payload.items():
        if not isinstance(items,list) or any(not isinstance(item,dict) for item in items): raise HTTPException(422, f'{kind} must be a list of records')
        ids=[item.get('id') for item in items if isinstance(item,dict) and item.get('id')]
        if len(ids)!=len(set(ids)): raise HTTPException(422,f'Duplicate identifiers in {kind}')
    for t in request.payload.get('teams',[]):
        if not t.get('seed'): warnings.append(f"{t.get('name','Team')}: missing seed")
    with db.connect() as source, sqlite3.connect(':memory:') as sandbox:
        sandbox.row_factory=sqlite3.Row; source.backup(sandbox); sandbox.execute('PRAGMA foreign_keys=ON')
        counts=import_records(request.payload,None,'pgl-major-singapore-2026',request.replace_demo,lambda:sandbox)
        if sandbox.execute('SELECT COUNT(*) FROM stage_entrants').fetchone()[0] == 32:
            from .main import _build_tournament_result
            try: _build_tournament_result(sandbox,1,73,db.utc_now(),{},allow_training=False)
            except ValueError as error: raise HTTPException(422,f'Import would make the tournament inconsistent: {error}') from error
    with db.connect() as c: version = db.state_version(c)
    return {'counts':counts,'warnings':warnings,'state_version':version}

@router.get('/updates')
def updates():
    import urllib.request, urllib.error
    url='https://api.github.com/repos/z1t3ng-lab/singapore-major-predictor/releases/latest'
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'MajorForecast'}),timeout=10) as response: release=json.load(response)
        import re
        parsed = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', release['tag_name'])
        newer = tuple(map(int,parsed.groups())) > (1,2,0) if parsed else None
        message = 'Update available. Review the release notes before installing.' if newer else ('You are running version 1.2.0; no newer stable release was found.' if newer is False else 'A release is published. Compare its version and release notes before installing.')
        return {'current_version':'1.2.0','version':release['tag_name'],'update_available':newer,'url':release['html_url'],'message':message}
    except urllib.error.HTTPError as error:
        if error.code==404: return {'message':'No packaged release has been published yet.','url':'https://github.com/z1t3ng-lab/singapore-major-predictor/releases'}
        raise HTTPException(502,'GitHub update check unavailable')
    except (OSError,ValueError): raise HTTPException(502,'Cannot check updates. Check your internet connection.')

@router.get('/state')
def workspace_state():
    with db.connect() as c: return {'version': db.state_version(c)}

@router.post('/scenarios/{scenario_id}/run')
def rerun_scenario(scenario_id: str):
    from .main import simulate, SimulationRequest
    with db.connect() as c:
        row=c.execute('SELECT run_id FROM saved_scenarios WHERE id=?',(scenario_id,)).fetchone()
        if not row: raise HTTPException(404,'Scenario not found')
    previous=run(row[0])
    result=simulate(SimulationRequest(simulations=previous['simulations'],seed=previous['seed'],cutoff=db.utc_now(),scenario_overrides=previous['scenario']['overrides']))
    with db.connect() as c: c.execute('UPDATE saved_scenarios SET run_id=? WHERE id=?',(result['run_id'],scenario_id))
    return result

class FilePreview(BaseModel):
    text: str = Field(max_length=10_000_000)
    kind: str
    replace_demo: bool = False

@router.post('/preview-file')
def preview_file(request: FilePreview):
    from .importers import csv_payload
    try:
        payload = json.loads(request.text) if request.kind == 'json' else csv_payload(request.text, request.kind)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    if not isinstance(payload,dict): raise HTTPException(422,'Import must contain an object of record arrays')
    return preview(ImportPreview(payload=payload,replace_demo=request.replace_demo))

@router.get('/stages')
def stage_settings():
    from .main import _stage_rows
    with db.connect() as c: return _stage_rows(c)

class StageSettings(BaseModel):
    expected_state_version: int
    opening_best_of: int
    decider_best_of: int
    start_date: str | None = None
    end_date: str | None = None

@router.put('/stages/{slug}')
def save_stage_settings(slug: str, request: StageSettings):
    if slug not in ('stage1','stage2','stage3'): raise HTTPException(422,'Choose a Swiss stage')
    if request.opening_best_of not in (1,3,5) or request.decider_best_of not in (1,3,5): raise HTTPException(422,'Formats must be BO1, BO3 or BO5')
    from datetime import date
    try:
        if request.start_date: date.fromisoformat(request.start_date)
        if request.end_date: date.fromisoformat(request.end_date)
        if request.start_date and request.end_date and request.start_date > request.end_date: raise ValueError('End date precedes start date')
    except ValueError as error: raise HTTPException(422,str(error)) from error
    with db.connect() as c:
        if db.state_version(c) != request.expected_state_version: raise HTTPException(409,'Data changed. Reload the settings.')
        stage = c.execute('SELECT * FROM stages WHERE slug=?',(slug,)).fetchone()
        rules = json.loads(stage['rules_json'])
        if c.execute("SELECT 1 FROM matches WHERE stage_slug=? AND (confirmed_winner_id IS NOT NULL OR winner_id IS NOT NULL)",(slug,)).fetchone():
            raise HTTPException(409,'This stage already has results. Clear results before changing stage settings.')
        backup('before stage settings change')
        rules.update(opening_match_best_of=request.opening_best_of, decider_match_best_of=request.decider_best_of, confirmation='User-configured; verify against organizer rules')
        rules['all_matches_best_of_three'] = False
        c.execute('UPDATE stages SET rules_json=?,start_date=?,end_date=? WHERE id=?',(json.dumps(rules),request.start_date,request.end_date,stage['id']))
        db.bump_state_version(c)
    return {'ok':True}
