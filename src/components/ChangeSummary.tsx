"use client";
import {useEffect,useState} from 'react';
import {workspaceRequest} from '@/lib/api';
import type {Team} from '@/lib/types';
export function ChangeSummary({before,after,teams}:{before:string;after:string;teams:Team[]}) {
 const [value,setValue]=useState<any>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;setValue(null);setError('');workspaceRequest(`/compare?before=${before}&after=${after}`).then(v=>{if(active)setValue(v);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[before,after]);
 if(error)return <p role="alert">Could not load result changes: {error}</p>;
 if(!value)return <p role="status">Comparing tournament changes…</p>;
 return <aside className="workspace"><h3>What changed?</h3><p>{value.removed_matches.length} obsolete pairings replaced · {value.added_matches.length} new pairings. See Method & data → Changes & scenarios for full standings.</p>{value.odds.slice(0,5).map((r:any)=><p key={r.team_id}><b>{teams.find(t=>t.id===r.team_id)?.name||r.team_id}</b> · title chance {(r.before*100).toFixed(1)}% → {(r.after*100).toFixed(1)}% ({r.change>=0?'+':''}{(r.change*100).toFixed(1)} points)</p>)}<small>{value.note}</small></aside>;
}
