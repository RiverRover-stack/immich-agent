import { describe, expect, it, vi } from 'vitest';
import { advance, createRun, defaultSetup, requestStop, retryCleanup } from './demo';
import { LiveAdapter } from './adapters';
import type { Run, Scenario } from './types';
function scenario(s:Scenario,maxAttempts=2){return createRun({...defaultSetup,scenario:s,maxAttempts});}
function finish(r:Run){for(let i=0;i<40&&r.status==='active';i++)r=advance(r);return r;}
describe('authoritative experiment transitions',()=>{
 it('baseline failure blocks injection and still verifies cleanup',()=>{const r=finish(scenario('baseline'));expect(r.injected).toBe(false);expect(r.steps[2]).toBe('skipped');expect(r.outcome).toBe('blocked');expect(r.cleanup).toBe('passed');});
 it('readiness failure blocks injection',()=>{const r=finish(scenario('readiness'));expect(r.injected).toBe(false);expect(r.outcome).toBe('blocked');});
 it('retries verification only within the attempt budget',()=>{const success=finish(scenario('retry'));expect(success.attempts.map(a=>a.status)).toEqual(['failed','passed']);expect(success.outcome).toBe('verified');const exhausted=finish(scenario('retry',1));expect(exhausted.attempts).toHaveLength(1);expect(exhausted.outcome).toBe('unresolved');});
 it('scope violations block acceptance even when tests pass',()=>{const r=finish(scenario('scope'));expect(r.tests.slice(-6).every(t=>t.status==='passed')).toBe(true);expect(r.outcome).toBe('unresolved');expect(r.patches[0].verified).toBe(false);});
 it.each(['first','exhausted'] as Scenario[])('%s outcome reaches verified cleanup',s=>{const r=finish(scenario(s));expect(r.status).toBe('complete');expect(r.cleanup).toBe('passed');expect(r.snapshots.cleanup).toBeDefined();});
 it('stop waits for restoration acknowledgement and verification',()=>{let r=advance(advance(advance(scenario('stop'))));r=requestStop(r);expect(r.cleanup).toBe('pending');expect(r.status).toBe('active');r=advance(r);expect(r.cleanup).toBe('running');expect(r.status).toBe('active');r=advance(r);expect(r.cleanup).toBe('passed');expect(r.outcome).toBe('cancelled');});
 it('cleanup failure prevents completion; retry remains pending until verified',()=>{let r=finish(scenario('cleanup'));expect(r.status).toBe('cleanup-failed');expect(r.finishedAt).toBeUndefined();expect(r.outcome).toBe('verified');r=retryCleanup(r);expect(r.status).toBe('active');expect(r.cleanup).toBe('running');r=advance(r);expect(r.status).toBe('complete');});
 it('distinguishes injection acknowledgement from confirmed failure',()=>{let r=scenario('first');r=advance(advance(advance(r)));expect(r.injected).toBe(true);expect(r.failureConfirmed).toBe(false);r=advance(r);expect(r.failureConfirmed).toBe(true);});
 it('no confirmed failure produces an inconclusive result and cleanup',()=>{const r=finish(scenario('inconclusive'));expect(r.outcome).toBe('inconclusive');expect(r.failureConfirmed).toBe(false);expect(r.attempts).toHaveLength(0);expect(r.cleanup).toBe('passed');});
 it('rejects arbitrary fault parameters',()=>{expect(()=>createRun({...defaultSetup,parameters:{endpoint:'arbitrary'}})).toThrow('approved');});
});
describe('live API isolation',()=>{
 it('never reports success when disconnected',async()=>{vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('Network unreachable')));const api=new LiveAdapter('/api/v1');await expect(api.bootstrap()).rejects.toThrow('Network unreachable');await expect(api.create(defaultSetup)).rejects.toThrow();expect(fetch).toHaveBeenCalledTimes(2);vi.unstubAllGlobals();});
 it('surfaces rejection and never retries mutations',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('Scope rejected',{status:409})));await expect(new LiveAdapter('/api').create(defaultSetup)).rejects.toThrow('Scope rejected');expect(fetch).toHaveBeenCalledTimes(1);vi.unstubAllGlobals();});
 it('rejects demo topology in live discovery',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({environments:[],faults:[],topology:{source:'demo'}}))));await expect(new LiveAdapter('/api').bootstrap()).rejects.toThrow('discovery');vi.unstubAllGlobals();});
});
