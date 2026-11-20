const { chromium }=require('playwright');
const assert=require('node:assert/strict');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
(async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'shadow-browser-'));
 const fixture=path.join(folder,'gameplay.mp4');
 let browser, server, page;
 try{
  execFileSync('ffmpeg',['-y','-loglevel','error','-f','lavfi','-i','testsrc2=size=640x360:rate=24:duration=2','-c:v','libx264',fixture]);
  server=spawn(process.env.PYTHON || 'python',['browser_acceptance_server.py',folder],{stdio:'inherit'});
  const url='http://127.0.0.1:8878';
  let running=false;
  for(let attempt=0;attempt<90;attempt++){
   try{if((await fetch(url+'/api/health')).ok){running=true;break}}catch{}
   await new Promise(resolve=>setTimeout(resolve,400));
  }
  assert(running,'Fixture app did not start');
  browser=await chromium.launch({headless:true,channel:process.env.SHADOW_BROWSER_CHANNEL || undefined,executablePath:process.env.SHADOW_CHROMIUM || undefined});
  page=await browser.newPage({viewport:{width:1440,height:1050},acceptDownloads:true});
  const issues=[];
  page.on('pageerror',error=>issues.push(error.message));
  await page.goto(url);
  await page.waitForFunction(()=>document.querySelector('#voiceAvailability').textContent.includes('Unavailable') || document.querySelector('#voiceAvailability').textContent.includes('neural voices'));
  await page.getByLabel('Story idea').fill('My girlfriend invited her ex to our wedding at the Lake House on Saturday.');
  await page.getByLabel('Story genre').selectOption('drama');
  await page.getByRole('button',{name:'Generate story',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('#lines textarea').length>4);
  let title=await page.locator('#title').inputValue();
  await page.getByRole('button',{name:'Regenerate',exact:true}).click();
  await page.waitForFunction(previous=>document.querySelector('#title').value && document.querySelector('#title').value!==previous,title);
  const script=await page.locator('#lines textarea').evaluateAll(lines=>lines.map(line=>line.value));
  assert(script.some(line=>line.includes('Lake House on Saturday')));
  assert(await page.locator('#lines input[type=number]').evaluateAll(inputs=>inputs.every(input=>input.checkValidity())),'Generated timing is invalid in the editor');
  await page.getByRole('button',{name:'Save project',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent.startsWith('Project saved:'));
  const saved=await page.locator('#projects').inputValue();
  await page.locator('#projects').selectOption('');
  await page.locator('#projects').selectOption(saved);
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='Loaded saved project');
  await page.getByRole('button',{name:'Continue to video setup'}).click();
  await page.locator('#gameplayFile').setInputFiles(fixture);
  await page.locator('#gameplayTag').fill('Acceptance footage');
  await page.getByRole('button',{name:'Save gameplay'}).click();
  await page.waitForFunction(()=>document.querySelector('#gameplayStatus').textContent.startsWith('Saved Acceptance footage'));
  await page.getByLabel('Gameplay height').selectOption('60');
  await page.getByLabel('Gameplay crop').selectOption('top');
  await page.getByLabel('Loop start (seconds)').fill('0.25');
  await page.getByLabel('Loop end (seconds)').fill('1.25');
  await page.getByRole('button',{name:'Preview layout'}).click();
  await page.locator('#layoutImage').waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('#layoutImage').naturalWidth===1080);
  await page.getByRole('button',{name:'Next line',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#layoutStatus').textContent.startsWith('Line 2'));
  await page.locator('#layoutImage').evaluate(image=>image.decode());
  await page.screenshot({path:process.env.SHADOW_ARTIFACT_DIR && path.join(process.env.SHADOW_ARTIFACT_DIR,'setup.png')});
  await page.getByRole('button',{name:'Render MP4',exact:true}).click();
  await page.getByRole('button',{name:'Review export'}).waitFor({state:'visible',timeout:100000});
  await page.waitForFunction(()=>document.querySelector('#preview').videoWidth===1080);
  if(process.env.SHADOW_ARTIFACT_DIR){
   const videoUrl=await page.locator('#preview').evaluate(video=>video.src);
   const response=await page.request.get(videoUrl);
   assert(response.ok());
   fs.writeFileSync(path.join(process.env.SHADOW_ARTIFACT_DIR,'short-preview.mp4'),await response.body());
  }
  await page.getByRole('button',{name:'Review export'}).click();
  await page.getByRole('button',{name:'Run video quality check',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#qualityPanel').textContent.includes('Technical export: OK'));
  await page.locator('#reviewState').selectOption('approved');
  await page.getByRole('button',{name:'Save review decision'}).click();
  await page.getByRole('link',{name:'Download publishing kit',exact:true}).waitFor({state:'visible'});
  const pending=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download publishing kit',exact:true}).click();
  const download=await pending;
  assert(download.suggestedFilename().endsWith('.zip'));
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('button',{name:'Test connection',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#handoffDiagnostics').textContent.includes('SHADOW_S3_BUCKET'));
  await page.getByRole('button',{name:'Publishing',exact:true}).click();
  assert(await page.getByRole('link',{name:'Open YouTube Studio'}).isVisible());
  await page.getByRole('button',{name:'Save publishing draft',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#draftFeedback').textContent.startsWith('Saved local draft'));
  await page.getByRole('button',{name:'Render Queue',exact:true}).click();
  await page.getByRole('button',{name:'Refresh jobs',exact:true}).click();
  await page.getByRole('button',{name:'Review video',exact:true}).waitFor({state:'visible'});
  const licensedClip=path.join(folder,'synthetic-clip.mp4');
  execFileSync('ffmpeg',['-y','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=4',
                        '-f','lavfi','-i','sine=f=440:d=4','-c:v','libx264','-c:a','aac',licensedClip]);
  await page.getByRole('button',{name:'Media Tools',exact:true}).click();
  await page.getByLabel('Source video',{exact:true}).filter({visible:true}).setInputFiles(licensedClip);
  await page.getByLabel('Creator',{exact:true}).fill('Synthetic creator');
  await page.getByLabel('Source link',{exact:true}).fill('https://www.twitch.tv/synthetic');
  await page.getByLabel('Permission record or agreement reference').fill('Synthetic acceptance fixture');
  await page.getByLabel('Permission scope').selectOption('edit-and-repost');
  await page.getByLabel('Horizontal crop').selectOption('left');
  await page.locator('#streamerEnd').fill('3.5');
  await page.getByRole('button',{name:'Render clip preview'}).click();
  await page.waitForFunction(()=>document.querySelector('#streamerPreviewOutput video')?.videoWidth===1080);
  if(process.env.SHADOW_ARTIFACT_DIR){
   const video=page.locator('#streamerPreviewOutput video');
   const response=await page.request.get(await video.evaluate(element=>element.src));
   assert(response.ok());
   fs.writeFileSync(path.join(process.env.SHADOW_ARTIFACT_DIR,'streamer-preview.mp4'),await response.body());
   await page.locator('#streamerPreviewOutput').screenshot({path:path.join(process.env.SHADOW_ARTIFACT_DIR,'streamer-preview.png')});
  }
  await page.getByRole('button',{name:'Daily Production',exact:true}).click();
  const clipReviews=page.locator('#streamerReviews');
  await clipReviews.getByRole('button',{name:'Preview clip'}).click();
  await page.waitForFunction(()=>document.querySelector('#streamerReviewPlayer video')?.videoWidth===1080);
  await clipReviews.getByRole('button',{name:'Approve',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#streamerReviews').textContent.includes('approved'));
  await page.reload();
  await page.getByRole('button',{name:'Daily Production',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#streamerReviews').textContent.includes('approved'));
  assert.deepEqual(issues,[]);
  const mobile=await browser.newPage({viewport:{width:390,height:844}});
  await mobile.goto(url);
  assert(await mobile.getByRole('button',{name:'Generate story',exact:true}).isVisible());
  assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth),390);
  await mobile.screenshot({path:process.env.SHADOW_ARTIFACT_DIR && path.join(process.env.SHADOW_ARTIFACT_DIR,'mobile.png')});
  console.log('PASS Chromium desktop and mobile workflow: generation, regeneration, project, gameplay, preview, 1080p render, quality, approve, publishing ZIP and setup');
 }catch(error){
  if(page){
   const status=await page.locator('#status').textContent({timeout:3000}).catch(()=>null);
   console.error('Workspace status:',status);
   if(process.env.SHADOW_ARTIFACT_DIR){
    await page.screenshot({path:path.join(process.env.SHADOW_ARTIFACT_DIR,'failure.png')}).catch(()=>{});
   }
  }
  throw error;
 }finally{
  if(browser)await browser.close();
  if(server && server.exitCode===null)await new Promise(resolve=>{server.once('exit',resolve);server.kill()});
  fs.rmSync(folder,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exit(1)});
