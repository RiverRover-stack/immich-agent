import type { Adapter, Bootstrap, Run, Setup } from './types';
import { advance, bootstrap, createRun, requestStop, retryCleanup } from './demo';
const key='shutterstorm-demo-v2';
function checkRun(r:Run,live=false):Run {
 if(!r||typeof r.id!=='string'||!r.setup||!Array.isArray(r.steps)||r.steps.length!==9||!Number.isInteger(r.phase)||r.phase<0||r.phase>8||!r.guard||!r.snapshots||!Array.isArray(r.metrics)||!Array.isArray(r.findings)||!Array.isArray(r.tests)||!Array.isArray(r.journeys)||!Array.isArray(r.events)||!Array.isArray(r.evidence)||!Array.isArray(r.markers))throw Error('Invalid run response: contract v2 experiment fields required.');
 if(r.status==='complete'&&(r.cleanup!=='passed'||!r.guard.undoVerified||r.guard.journalEntries!==0))throw Error('Invalid completion: verified undo and an empty journal are required.');
 if(live&&Object.values(r.snapshots).some(t=>t?.source!=='discovery'))throw Error('Invalid live snapshot: discovery required.');
 return r;
}
export class DemoAdapter implements Adapter {
 mode='demo' as const;private runs:Run[]=[];private ticks=new Map<string,number>();
 constructor(){try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))this.runs=saved.flatMap(r=>{try{return [checkRun(r)];}catch{return [];}});}catch{/* Storage may be unavailable. */}}
 private save(){try{localStorage.setItem(key,JSON.stringify(this.runs.slice(0,40)));}catch{/* Session still works without persistence. */}}
 private find(id:string){const r=this.runs.find(r=>r.id===id);if(!r)throw Error('Run not found.');return r;}
 private put(r:Run){this.runs=this.runs.map(old=>old.id===r.id?r:old);this.save();return structuredClone(r);}
 async bootstrap(){return structuredClone(bootstrap);}
 async history(){return structuredClone(this.runs);}
 async create(s:Setup){if(this.runs.some(r=>r.status!=='complete'))throw Error('Restore the existing run before starting another.');const r=createRun(s);this.runs.unshift(r);this.ticks.set(r.id,Date.now());this.save();return structuredClone(r);}
 async get(id:string){let r=this.find(id);if(Date.now()-(this.ticks.get(id)||0)>2200){r=advance(r);this.ticks.set(id,Date.now());return this.put(r);}return structuredClone(r);}
 async stop(id:string){return this.put(requestStop(this.find(id)));}
 async retryCleanup(id:string){return this.put(retryCleanup(this.find(id)));}
 async reset(){if(this.runs.some(r=>r.status!=='complete'))throw Error('Restore the existing run before resetting.');this.runs=[];this.ticks.clear();this.save();}
}
export class LiveAdapter implements Adapter {
 mode='live' as const;
 constructor(private base:string){}
 private async request<T>(path:string,method='GET',body?:unknown):Promise<T>{const response=await fetch(this.base.replace(/\/$/,'')+path,{method,headers:{Accept:'application/json',...(body?{'Content-Type':'application/json'}:{})},credentials:'same-origin',body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(10000)});if(!response.ok)throw Error(`Backend rejected request (${response.status}): ${(await response.text()).slice(0,400)}`);return response.json();}
 async bootstrap(){const b=await this.request<Bootstrap>('/bootstrap');if(!b||b.contractVersion!==2||!Array.isArray(b.environments)||!Array.isArray(b.faults)||!Array.isArray(b.findings)||!Array.isArray(b.journeyNames)||b.topology?.source!=='discovery'||!Array.isArray(b.topology.services)||!Array.isArray(b.topology.dependencies))throw Error('Invalid live bootstrap: contract v2 and discovery topology required.');return b;}
 async history(){const runs=await this.request<Run[]>('/runs');if(!Array.isArray(runs))throw Error('Invalid run history response.');return runs.map(r=>checkRun(r,true));}
 async create(setup:Setup){const {scenario,...body}=setup;void scenario;return checkRun(await this.request<Run>('/runs','POST',body),true);}
 async get(id:string){return checkRun(await this.request<Run>(`/runs/${encodeURIComponent(id)}`),true);}
 async stop(id:string){return checkRun(await this.request<Run>(`/runs/${encodeURIComponent(id)}/stop`,'POST',{}),true);}
 async retryCleanup(id:string){return checkRun(await this.request<Run>(`/runs/${encodeURIComponent(id)}/cleanup/retry`,'POST',{}),true);}
 async reset(){throw Error('Demo reset is unavailable in Live Mode.');}
}
