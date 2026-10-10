export type Status = 'pending'|'running'|'passed'|'failed'|'skipped'|'unavailable';
export type Outcome = 'pending'|'measured'|'inconclusive'|'blocked'|'cancelled'|'aborted';
export const phases = ['Discover & readiness','Baseline probes','Plan & predict','Validate experiment','Inject approved fault','Observe impact','Analyze measurements','Save report','Restore & verify cleanup'] as const;
export type Scenario = 'first'|'fallback'|'baseline'|'stop'|'cleanup'|'guard'|'inconclusive'|'readiness';
export interface Service { id:string; name:string; kind:string; health:Status; updatedAt:string; checks:string[]; logs:string[]; tests:string[]; x:number; y:number }
export interface Dependency { source:string; target:string; label:string; sourceRef?:string }
export interface Topology { source:'demo'|'discovery'; services:Service[]; dependencies:Dependency[]; journeys?:Record<string,string[]> }
export interface Parameter { key:string; label:string; value:number; min:number; max:number; step:number }
export interface Fault { id:string; name:string; target:string; parameters:Parameter[]; dummyTested:boolean }
export interface Setup { name:string; environment:string; target:string; faultId:string; parameters:Record<string,number>; duration:number; protectedJourneys:string[]; hypothesis:string; scenario:Scenario }
export interface TestResult { id:string; name:string; status:Status; durationMs:number|null; at:string; period:string; message?:string; evidenceId?:string }
export interface Evidence { id:string; at:string; source:string; kind:string; text:string; attachments?:{name:string; url:string; mimeType:string}[] }
export interface Metric { second:number; latency:number|null; errors:number|null; queue:number|null; accepted:number|null; processed:number|null; gap:number|null; period:string }
export interface Prediction { journey:string; expect:'fail'|'healthy'|'degraded'; confidence:number; observed:'fail'|'healthy'|'degraded'|null; matched:boolean|null }
export interface Plan { producer:'api'|'paste'|'fallback'; model:string|null; reason:string; predictions:Prediction[] }
export interface Finding { id:string; kind:'config-fact'|'measured'; severity:'high'|'medium'|'low'; title:string; evidenceIds:string[]; sourceRef:string; fix:string }
export interface Analysis { errorRate:number|null; p50Ms:number|null; p95Ms:number|null; baselineP95Ms:number|null; recoverySeconds:number|null; asyncGap:number|null; affectedJourneys:string[]; score:number|null; components:{availability:number;recovery:number;asyncCompletion:number;integrity:number;blastRadius:number}|null }
export interface Guard { drill:Status; state:'armed'|'tripped'|'restoring'|'verified'|'unavailable'; reason:string|null; journalEntries:number; undoVerified:boolean; ttlSeconds:number }
export interface Run { id:string; setup:Setup; startedAt:string; updatedAt:string; finishedAt?:string; phase:number; steps:Status[]; status:'active'|'complete'|'cleanup-failed'; outcome:Outcome; cleanup:Status; tests:TestResult[]; journeys:TestResult[]; evidence:Evidence[]; events:Evidence[]; metrics:Metric[]; snapshots:Partial<Record<'baseline'|'failure'|'cleanup',Topology>>; injected:boolean; failureConfirmed:boolean; cancellationRequested:boolean; cleanupRetries:number; markers:{second:number; label:string}[]; plan:Plan|null; guard:Guard; analysis:Analysis|null; findings:Finding[] }
export interface Environment { id:string; name:string; checks:{name:string; status:Status; detail:string}[]; drill:Status }
export interface Bootstrap { contractVersion:2; environments:Environment[]; topology:Topology; faults:Fault[]; findings:Finding[]; journeyNames:string[] }
export interface Adapter { mode:'demo'|'live'; bootstrap():Promise<Bootstrap>; history():Promise<Run[]>; create(setup:Setup):Promise<Run>; get(id:string):Promise<Run>; stop(id:string):Promise<Run>; retryCleanup(id:string):Promise<Run>; reset():Promise<void> }
