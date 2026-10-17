const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

async function main(){
  const root=path.resolve(__dirname,'..');
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'film-annotator-smoke-'));
  const python=process.env.ANNOTATOR_PYTHON||'python';
  const port='18765';
  const child=spawn(python,['-m','app.server'],{cwd:root,env:{...process.env,ANNOTATOR_DATA_DIR:data,ANNOTATOR_PORT:port},stdio:'ignore'});
  let browser;
  try{
    let ready=false;
    for(let i=0;i<50;i++){
      try{const res=await fetch(`http://127.0.0.1:${port}/api/bootstrap`);if(res.ok){ready=true;break;}}catch(_){}
      await new Promise(r=>setTimeout(r,100));
    }
    if(!ready)throw Error('Server did not start');
    browser=await chromium.launch({headless:true,executablePath:process.env.ANNOTATOR_BROWSER||'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'});
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    await page.goto(`http://127.0.0.1:${port}/`);
    await page.getByRole('heading',{name:'دیالوگ‌ها و توضیحات'}).waitFor();
    await page.getByRole('button',{name:'+ نوبت دیالوگ'}).click();
    await page.locator('#start-ms').fill('1000');
    await page.locator('#end-ms').fill('2500');
    await page.locator('#speaker').fill('Test speaker');
    await page.locator('#text-fa').fill('سلام');
    await page.locator('#text-en').fill('Hello');
    await page.locator('#translation-status').selectOption('approved');
    await page.getByRole('button',{name:'ذخیرهٔ متن و ترجمه'}).click();
    await page.getByText('سلام',{exact:true}).first().waitFor();
    await page.screenshot({path:path.join(data,'screenshot.png'),fullPage:true});
    console.log('Browser smoke passed; screenshot:',path.join(data,'screenshot.png'));
  }finally{
    if(browser)await browser.close();
    child.kill();
  }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
