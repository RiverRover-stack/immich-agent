import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.clock.install();await page.goto('http://127.0.0.1:5173');
 async function start(scenario){await page.getByRole('button',{name:'Start Run',exact:true}).click();await page.getByLabel('Deterministic demo scenario').selectOption(scenario);await page.getByRole('button',{name:'Start approved experiment'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});}
 async function progress(){for(let i=0;i<20;i++){await page.clock.runFor(2500);if(await page.getByText('Run complete',{exact:true}).count())break;}}
 await start('first');await progress();await page.getByText('Run complete',{exact:true}).waitFor();
 await mkdir('test-results',{recursive:true});await page.screenshot({path:'test-results/desktop.png',fullPage:true});
 await page.getByLabel('Metric',{exact:true}).selectOption('queue');if(!await page.locator('.recharts-area').count())throw Error('Queue chart is missing');
 await page.getByRole('tab',{name:'Predictions',exact:true}).click();await page.getByText('5 / 5',{exact:false}).first().waitFor();
 await page.getByRole('button',{name:'Dependency Map',exact:true}).click();await page.getByLabel('Topology snapshot').selectOption('failure');await page.getByText('Observed failure',{exact:false}).first().waitFor();
 await page.getByLabel('Journey overlay').selectOption('upload');if(await page.locator('.service-node.predicted').count()!==6)throw Error('Upload overlay must include ML');
 await page.getByRole('button',{name:'History & Reports',exact:true}).click();await page.getByRole('button',{name:'Open',exact:true}).click();const download=page.waitForEvent('download');await page.getByRole('button',{name:'JSON report'}).click();if(!(await download).suggestedFilename().endsWith('.json'))throw Error('Missing JSON download');await page.getByRole('button',{name:'Close dialog'}).click();
 await page.getByRole('button',{name:'Run Console',exact:true}).click();
 for(const scenario of ['fallback','baseline','cleanup','guard','inconclusive','readiness','stop']){
  await start(scenario);if(scenario==='stop'){await page.clock.runFor(14000);await page.getByRole('button',{name:'Stop & Restore',exact:true}).click();}await progress();
  if(scenario==='cleanup'){await page.getByRole('button',{name:'Retry cleanup verification'}).click();await progress();}
  await page.getByText('Run complete',{exact:true}).waitFor();
  const latest=await page.evaluate(()=>JSON.parse(localStorage.getItem('shutterstorm-demo-v2'))[0]);
  const expected={fallback:'measured',baseline:'blocked',cleanup:'measured',guard:'aborted',inconclusive:'inconclusive',readiness:'blocked',stop:'cancelled'}[scenario];
  if(latest.outcome!==expected||latest.cleanup!=='passed'||latest.guard.journalEntries!==0)throw Error(`Incorrect ${scenario} result: ${JSON.stringify({outcome:latest.outcome,cleanup:latest.cleanup,scenario:latest.setup.scenario,status:latest.status})}`);
  console.log(`PASS browser scenario: ${scenario}`);
 }
 await page.getByRole('button',{name:'Start Run',exact:true}).click();await page.getByLabel('Approved fault').selectOption('cpu_throttle');await page.getByLabel('CPU allocation').fill('0.1');await page.getByRole('button',{name:'Start approved experiment'}).click();if(!await page.getByRole('dialog').count())throw Error('Unsafe CPU value accepted');await page.keyboard.press('Escape');
 await page.setViewportSize({width:390,height:844});await page.clock.runFor(1500);
 const graph=page.locator('.graph-canvas');await graph.scrollIntoViewIfNeeded();await page.clock.runFor(1000);
 const visible=await page.locator('.react-flow__node').evaluateAll(nodes=>nodes.every(n=>{const a=n.getBoundingClientRect(),b=n.closest('.graph-canvas').getBoundingClientRect();return a.width>5&&a.left>=b.left-1&&a.right<=b.right+1&&a.top>=b.top-1&&a.bottom<=b.bottom+1;}));
 if(!visible||await page.locator('.react-flow__node').count()!==6)throw Error('Mobile graph nodes not contained in canvas');
 await page.screenshot({path:'test-results/mobile.png',fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
 if(errors.length||overflow)throw Error(JSON.stringify({errors,overflow}));
 console.log('PASS: eight scenarios, predictions, queue chart, upload overlay, reports, CPU bounds, mobile graph visibility, no runtime errors');
}finally{await browser.close();}
