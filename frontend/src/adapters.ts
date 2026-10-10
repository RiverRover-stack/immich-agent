import type { Adapter, Bootstrap, Run, Setup } from './types';
import { advance, bootstrap, createRun, requestStop, retryCleanup } from './demo';
const key='shutterstorm-demo-v1';
export class DemoAdapter implements Adapter {
 mode='demo' as const; private runs:Run[]=[]; private ticks=new Map<string,number>();
 constructor(){try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))this.runs=saved.filter(r=>r?.id&&Array.isArray(r.steps)&&r.setup&&Array.isArray(r.events));}catch{/* Storage may be unavailable. */}}
 private save(){try{localStorage.setItem(key,JSON.stringify(this.runs.slice(0,40)));}catch{/* Session still works without persistence. */}}
 private find(id:string){const r=this.runs.find(r=>r.id===id);if(!r)throw Error('Run not found.');return r;}
 private put(r:Run){this.runs=this.runs.map(old=>old.id===r.id?r:old);this.save();return structuredClone(r);}
 async bootstrap(){return structuredClone(bootstrap);}
 async history(){return structuredClone(this.runs);}
 async create(s:Setup){if(this.runs.some(r=>r.status!=='complete'))throw Error('Restore the existing run before starting another.');const r=createRun(s);this.runs.unshift(r);this.ticks.set(r.id,Date.now());this.save();return structuredClone(r);}
 async get(id:string){let r=this.find(id);if(Date.now()-(this.ticks.get(id)||0)>2200){r=advance(r);this.ticks.set(id,Date.now());return this.put(r);}return structuredClone(r);}
 async stop(id:string){return this.put(requestStop(this.find(id)));}
 async retryCleanup(id:string){return this.put(retryCleanup(this.find(id)));}
 async reset(){this.runs=[];this.ticks.clear();this.save();}
}
export class LiveAdapter implements Adapter {
 mode='live' as const;
 constructor(private base:string){}
 private async request<T>(path:string,method='GET',body?:unknown):Promise<T>{const response=await fetch(this.base.replace(/\/$/,'')+path,{method,headers:{'Accept':'application/json',...(body?{'Content-Type':'application/json'}:{})},credentials:'same-origin',body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(10000)});if(!response.ok){const message=(await response.text()).slice(0,400);throw Error(`Backend rejected request (${response.status}): ${message}`);}if(response.status===204)return undefined as T;return response.json();}
 async bootstrap(){const b=await this.request<Bootstrap>('/bootstrap');if(!Array.isArray(b.environments)||!Array.isArray(b.faults)||b.topology?.source!=='discovery')throw Error('Invalid live bootstrap: discovery topology required.');return b;}
 async history(){return this.request<Run[]>('/runs');}
 async create(setup:Setup){return this.request<Run>('/runs','POST',setup);}
 async get(id:string){return this.request<Run>(`/runs/${encodeURIComponent(id)}`);}
 async stop(id:string){return this.request<Run>(`/runs/${encodeURIComponent(id)}/stop`,'POST',{});}
 async retryCleanup(id:string){return this.request<Run>(`/runs/${encodeURIComponent(id)}/cleanup/retry`,'POST',{});}
 async reset(){throw Error('Demo reset is unavailable in Live Mode.');}
}
