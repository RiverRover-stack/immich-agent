import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
const page=await browser.newPage({viewport:{width:1440,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.clock.install();
await page.goto('http://127.0.0.1:5173');
async function start(scenario){
 await page.getByRole('button',{name:'Start Run',exact:true}).click();
 await page.getByLabel('Deterministic demo scenario').selectOption(scenario);
 await page.getByRole('button',{name:'Start approved experiment'}).click();
}
async function progress(){for(let i=0;i<22;i++){await page.clock.runFor(2500);if(await page.getByText('Run complete',{exact:true}).count())break;}}
await start('first');await progress();
await page.getByText('Run complete',{exact:true}).waitFor();
await mkdir('test-results',{recursive:true});
await page.screenshot({path:'test-results/desktop.png',fullPage:true});
await page.getByRole('tab',{name:'Changed Files',exact:false}).click();
await page.getByText('In scope',{exact:false}).waitFor();
await page.getByRole('button',{name:'Dependency Map',exact:true}).click();
await page.getByLabel('Topology snapshot').selectOption('failure');
await page.getByText('Observed failure',{exact:false}).first().waitFor();
await page.getByRole('button',{name:'History & Reports',exact:true}).click();
await page.getByRole('button',{name:'Open',exact:false}).click();
await page.getByRole('button',{name:'JSON report'}).waitFor();
const download=page.waitForEvent('download');await page.getByRole('button',{name:'JSON report'}).click();
if(!(await download).suggestedFilename().endsWith('.json'))throw Error('Report download missing');
await page.getByRole('button',{name:'Close dialog'}).click();
await page.getByRole('button',{name:'Run Console',exact:true}).click();
for(const scenario of ['retry','exhausted','baseline','cleanup','scope','inconclusive','readiness','stop']){
 await start(scenario);
 if(scenario==='stop'){await page.clock.runFor(7500);await page.getByRole('button',{name:'Stop & Restore',exact:true}).click();}
 await progress();
 if(scenario==='cleanup'){await page.getByRole('button',{name:'Retry cleanup verification'}).click();await progress();}
 await page.getByText('Run complete',{exact:true}).waitFor();
 const latest=await page.evaluate(()=>JSON.parse(localStorage.getItem('shutterstorm-demo-v1'))[0]);
 const expected={retry:'verified',exhausted:'unresolved',baseline:'blocked',cleanup:'verified',scope:'unresolved',inconclusive:'inconclusive',readiness:'blocked',stop:'cancelled'}[scenario];
 if(latest.outcome!==expected||latest.cleanup!=='passed')throw Error(`Incorrect ${scenario} outcome`);
 console.log(`PASS browser scenario: ${scenario}`);
}
await page.setViewportSize({width:390,height:844});
await page.getByRole('button',{name:'Run Console',exact:true}).click();
await page.clock.runFor(1000);
await page.screenshot({path:'test-results/mobile.png',fullPage:true});
const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
if(errors.length||overflow)throw Error(JSON.stringify({errors,overflow}));
console.log('PASS: nine scenarios, graph snapshots, history/report download, mobile overflow, no runtime errors');
}finally{await browser.close();}
