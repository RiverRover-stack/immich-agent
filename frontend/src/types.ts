export type Status = 'pending'|'running'|'passed'|'failed'|'skipped'|'unavailable';
export type Outcome = 'pending'|'verified'|'unresolved'|'inconclusive'|'blocked'|'cancelled';
export const phases = ['Environment & checkpoint','Baseline tests','Inject approved fault','Confirm failure','Agent repair','Scope & protected tests','Save verified patch','Save unresolved result','Restore & verify cleanup'] as const;
export type Scenario = 'first'|'retry'|'exhausted'|'baseline'|'stop'|'cleanup'|'scope'|'inconclusive'|'readiness';
export interface Service { id:string; name:string; kind:string; health:Status; updatedAt:string; checks:string[]; logs:string[]; tests:string[]; x:number; y:number }
export interface Dependency { source:string; target:string; label:string }
export interface Topology { source:'demo'|'discovery'; services:Service[]; dependencies:Dependency[] }
export interface Fault { id:string; name:string; target:string; parameters:{key:string; label:string; value:string}[] }
export interface Setup { name:string; environment:string; target:string; faultId:string; parameters:Record<string,string>; duration:number; maxAttempts:number; allowedPaths:string[]; protectedPaths:string[]; checkpoint:string; hypothesis:string; scenario:Scenario }
export interface TestResult { id:string; name:string; status:Status; durationMs:number|null; at:string; period:string; message?:string; evidenceId?:string }
export interface Evidence { id:string; at:string; source:string; kind:string; text:string; attachments?:{name:string; url:string; mimeType:string}[] }
export interface Patch { path:string; added:number; deleted:number; inScope:boolean; diff:string; verified:boolean }
export interface Attempt { number:number; status:Status; summary:string; scopeValid:boolean; requiredChecksPassed:boolean; files:string[] }
export interface Metric { second:number; latency:number|null; errors:number|null; cpu:number|null; memory:number|null; queue:number|null; period:string }
export interface Run { id:string; setup:Setup; startedAt:string; updatedAt:string; finishedAt?:string; phase:number; steps:Status[]; status:'active'|'complete'|'cleanup-failed'; outcome:Outcome; cleanup:Status; attempts:Attempt[]; tests:TestResult[]; journeys:TestResult[]; evidence:Evidence[]; events:Evidence[]; metrics:Metric[]; patches:Patch[]; snapshots:Partial<Record<'baseline'|'failure'|'cleanup',Topology>>; injected:boolean; failureConfirmed:boolean; cancellationRequested:boolean; cleanupRetries:number; markers:{second:number; label:string}[] }
export interface Environment { id:string; name:string; checks:{name:string; status:Status; detail:string}[] }
export interface Bootstrap { environments:Environment[]; topology:Topology; faults:Fault[] }
export interface Adapter { mode:'demo'|'live'; bootstrap():Promise<Bootstrap>; history():Promise<Run[]>; create(setup:Setup):Promise<Run>; get(id:string):Promise<Run>; stop(id:string):Promise<Run>; retryCleanup(id:string):Promise<Run>; reset():Promise<void> }
