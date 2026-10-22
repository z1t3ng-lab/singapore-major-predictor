import React from 'react';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {Workspace} from '@/components/Workspace';
import {workspaceRequest} from '@/lib/api';
vi.mock('@/lib/api',()=>({workspaceRequest:vi.fn()}));
const api=vi.mocked(workspaceRequest);
afterEach(()=>{cleanup();vi.restoreAllMocks();});
beforeEach(()=>{api.mockReset();api.mockImplementation(async(path:string)=>{
 if(path==='/state')return {version:3};
 if(path==='/performance')return {count:0,accuracy:null,brier:null,log_loss:null,bins:[],matches:[],note:'Only forecasts recorded before the match are scored.'};
 if(path==='/preview')return {counts:{teams:1},warnings:[],state_version:3};
 return [];
});});
it('previews a record before saving and includes the preview version',async()=>{
 const onChanged=vi.fn();render(<Workspace teams={[]} result={null} onChanged={onChanged} onRun={vi.fn()}/>);
 await waitFor(()=>expect(screen.queryByText('Working…')).toBeNull());
 fireEvent.change(screen.getByLabelText('id'),{target:{value:'real-01'}});
 fireEvent.change(screen.getByLabelText('name'),{target:{value:'Imported team'}});
 fireEvent.click(screen.getByRole('button',{name:'Preview changes'}));
 await screen.findByRole('button',{name:'Confirm and save'});
 expect(api.mock.calls.some(([path])=>path.startsWith('/import'))).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Confirm and save'}));
 await waitFor(()=>expect(onChanged).toHaveBeenCalledOnce());
 expect(api.mock.calls.some(([path])=>path==='/import/json?expected_state_version=3')).toBe(true);
});
it('shows missing performance data without inventing accuracy',async()=>{
 render(<Workspace teams={[]} result={null} onChanged={vi.fn()} onRun={vi.fn()}/>);
 await waitFor(()=>expect(screen.queryByText('Working…')).toBeNull());
 fireEvent.click(screen.getByRole('button',{name:'Performance'}));
 expect(screen.getByText(/0 evaluated matches · Accuracy —/)).toBeTruthy();
 expect(screen.getByText('Only forecasts recorded before the match are scored.')).toBeTruthy();
});
it('requires confirmation before a backup restore',async()=>{
 api.mockImplementation(async(path:string)=>path==='/backups'?[{id:'snapshot.sqlite3',created_at:'Today',reason:'manual'}]:path==='/state'?{version:3}:[]);
 vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<Workspace teams={[]} result={null} onChanged={vi.fn()} onRun={vi.fn()}/>);
 await waitFor(()=>expect(screen.queryByText('Working…')).toBeNull());
 fireEvent.click(screen.getByRole('button',{name:'Backups & updates'}));
 fireEvent.click(screen.getByRole('button',{name:'Restore'}));
 await waitFor(()=>expect(window.confirm).toHaveBeenCalledOnce());
 expect(api.mock.calls.some(([path])=>path.endsWith('/restore'))).toBe(false);
});
