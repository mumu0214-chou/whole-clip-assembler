'use strict';
const fs=require('node:fs');const path=require('node:path');
const readline=require('node:readline');
const {dependencies,bundleFor}=require('./render.cjs');
process.env.TEMP=process.env.TMP=process.platform==='win32' ? 'D:\\GPTData\\temp\\assembler_subtitle_preview' : path.join(process.env.ASSEMBLER_TEMP || require('node:os').tmpdir(), 'assembler_subtitle_preview');
fs.mkdirSync(process.env.TEMP,{recursive:true});
let browser, d, serveUrl;
async function render(req){
  const start=performance.now();
  if(!d){d=dependencies(req.dependencyRoot);serveUrl=await bundleFor(req,d);}
  if(!browser)browser=await d.renderer.openBrowser('chrome',req.browser?{browserExecutable:req.browser,chromeMode:'chrome-for-testing'}:{});
  const opts={serveUrl,inputProps:req.props,puppeteerInstance:browser};
  const composition=await d.renderer.selectComposition({...opts,id:req.composition||'SubtitlePreview'});
  if(req.mode==='video')await d.renderer.renderMedia({...opts,composition,outputLocation:req.output,codec:'h264',concurrency:1,crf:20});
  else await d.renderer.renderStill({...opts,composition,output:req.output,imageFormat:'png',frame:req.frame||0});
  return {output:req.output,seconds:(performance.now()-start)/1000};
}
(async()=>{
  const rl=readline.createInterface({input:process.stdin});
  for await(const line of rl){
    try {const req=JSON.parse(line);const result=await render(req);console.log('RESULT:'+JSON.stringify(result));}
    catch(error){console.log('RESULT:'+JSON.stringify({error:error.stack||String(error)}));}
  }
  if(browser)await browser.close({silent:true});
})().catch(e=>{console.error(e);process.exitCode=1;});
