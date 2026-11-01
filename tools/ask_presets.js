// 예시 질문의 답을 미리 만들어 data/ask_presets.json 에 저장한다 (사이트의 예시 버튼은 AI를 부르지 않고 이 파일로 바로 답한다).
//
//   node tools/ask_presets.js [https://lee2017-bit.github.io/matjip/] [--only 잠실,광화문]   (--only: 이 말이 들어간 질문만 다시)
//
// 배포된 사이트의 '물어보기'를 실제로 돌려(질문 분석 → 후보 → AI 답) 화면이 남긴 결과(window.__askResult)를 모은다.
// 질문 목록은 사이트 ask.html 의 예시 버튼. Playwright 필요. 질문 1개 = Worker AI 호출 2번 (IP당 하루 제한 안에서).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const oi = args.indexOf('--only');
const ONLY = oi !== -1 ? args.splice(oi, 2)[1].split(',').map(x => x.trim()).filter(Boolean) : null;
const SITE = (args[0] || 'https://lee2017-bit.github.io/matjip/').replace(/\/?$/, '/');
const OUT = path.join(__dirname, '..', 'data', 'ask_presets.json');
const qkey = q => String(q || '').toLowerCase().replace(/[^0-9a-z가-힣]/g, '');

(async () => {
  const old = fs.existsSync(OUT) ? JSON.parse(fs.readFileSync(OUT, 'utf8')) : { answers: {} };
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(SITE + 'ask.html');
  const questions = await page.$$eval('.ask-ex button', bs => bs.map(b => b.textContent.trim()));
  const answers = {};
  for (const q of questions) {
    if (ONLY && !ONLY.some(w => q.includes(w))) { if (old.answers[qkey(q)]) answers[qkey(q)] = old.answers[qkey(q)]; continue; }
    await page.goto('about:blank');
    // #fresh: 미리 만든 답을 쓰지 않고 새로 묻는다
    await page.goto(SITE + 'ask.html#fresh&q=' + encodeURIComponent(q));
    try {
      await page.waitForFunction(() => window.__askResult || document.querySelector('.ask-answer, .ask-out .hint'), null, { timeout: 70000 });
    } catch (e) { /* 시간 초과 */ }
    const r = await page.evaluate(() => window.__askResult || null);
    if (r) {
      answers[qkey(q)] = r;
      console.log('ok  ', q, '→', r.picks.map(p => p.name).join(', '));
    } else {
      const msg = await page.$eval('#ask-out', e => e.innerText.slice(0, 120)).catch(() => '');
      if (old.answers[qkey(q)]) answers[qkey(q)] = old.answers[qkey(q)];
      console.log('FAIL', q, '|', msg.replace(/\s+/g, ' '), old.answers[qkey(q)] ? '(이전 답 유지)' : '');
    }
    await page.waitForTimeout(3000);
  }
  await browser.close();
  const made = new Date().toISOString().slice(0, 10);
  fs.writeFileSync(OUT, JSON.stringify({ made, answers }, null, 0) + '\n');
  console.log(`saved ${Object.keys(answers).length}/${questions.length} → ${OUT}`);
})();
