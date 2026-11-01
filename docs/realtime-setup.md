# 실시간 조회 켜기 (선택)

> **현재 상태:** 켜져 있음 — `https://matjip-lookup.jaewon-lee2017.workers.dev` (Cloudflare가 이 저장소 main의 `worker/` 폴더를 자동 배포, 주소는 `.github/workflows/pages.yml` 기본값)

지금 사이트는 미리 모아 둔 가게들 안에서만 검색합니다. 아래 2단계를 마치면 목록에 없는 가게도
"실시간으로 찾아보기" 버튼으로 바로 매출점수를 볼 수 있습니다.

- **필요한 것: Cloudflare 계정 하나.** 카카오 키는 필요 없습니다 (eat.pl 검색이 가게 이름만으로 전국 조회를 지원).
- **비용: 0원.** Workers 무료 플랜은 카드 등록 없이 하루 10만 요청까지이고, 넘으면 요금이 나가는 대신 그날은 오류가 납니다.
- **걸리는 시간: 10분.**

| 무엇 | 어디에 넣나 | 공개돼도 되나 |
|---|---|---|
| `worker/lookup.js` 코드 | Cloudflare Worker 편집기 | 됨 (비밀 값 없음) |
| Worker 주소 `https://matjip-lookup.<계정>.workers.dev` | GitHub 저장소 Variable `LOOKUP_API` (또는 Claude에게 전달) | 됨 (사이트 HTML에 들어갑니다) |

---

## 1단계. Cloudflare Worker 만들기 (7분)

1. <https://dash.cloudflare.com/sign-up> 에서 이메일로 가입하고 메일 인증을 합니다.
   도메인을 추가하라는 화면이 나오면 건너뜁니다.
2. 왼쪽 메뉴 **Workers & Pages**(또는 Compute → Workers) → **Create** → **Create Worker**
   (Hello World 템플릿) → 이름을 `matjip-lookup` 으로 바꾸고 **Deploy**
3. 코드 넣기 (둘 중 하나)
   - **추천 (휴대폰도 가능):** Worker → **Settings** → **Build** → Git repository **Connect** → GitHub `lee2017-bit/matjip`,
     Branch `main`, **Advanced settings → Root directory `worker`**, Deploy command `npx wrangler deploy` → Connect.
     이후 main의 `worker/` 가 바뀌면 자동 재배포됩니다.
   - PC: **Edit code** → 코드를 전부 지우고 <https://raw.githubusercontent.com/lee2017-bit/matjip/main/worker/lookup.js> 내용을 붙여 넣고 **Deploy**
4. Worker 주소를 확인합니다: `https://matjip-lookup.<계정이름>.workers.dev`
5. 브라우저에서 `https://matjip-lookup.<계정이름>.workers.dev/?q=진주회관` 을 열어
   `"merUrl"` 이 들어 있는 결과가 보이면 성공입니다.

## 2단계. 사이트에 연결하기 (3분)

1. <https://github.com/lee2017-bit/matjip/settings/variables/actions> 를 엽니다.
   (저장소 Settings → Secrets and variables → Actions → **Variables** 탭)
2. **New repository variable**
   - Name: `LOOKUP_API`
   - Value: `https://matjip-lookup.<계정이름>.workers.dev` (끝에 `/` 없이)
3. 저장소 **Actions** 탭 → **Build and deploy site** → **Run workflow** 를 누르면 3~5분 뒤 반영됩니다.

   **더 쉬운 방법:** 2단계를 건너뛰고 Worker 주소만 Claude에게 알려 주면, 배포 설정 파일에 넣어 대신 배포합니다
   (주소는 공개돼도 되는 값입니다).
4. 사이트 홈에서 목록에 없는 가게를 검색해 **"실시간으로 찾아보기"** 버튼이 나오면 끝입니다.

---

## 동작 방식과 주의할 점

- 방문자가 버튼을 누르면 Worker가 eat.pl 잇플 가맹점 이름 검색을 한 번(지역+이름 조합까지 최대 3번, 1.5초 간격) 부르고
  이름이 맞는 가게의 점수와 eat.pl 링크를 돌려줍니다.
- Worker는 eat.pl **검색 API만** 부릅니다. 맛집 상세 페이지(merUrl)는 서버에서 호출하지 않고 그대로 넘겨주기만 하므로,
  방문자가 링크를 눌러야 집계됩니다 (공모전 규칙 준수).
- 버튼을 누른 방문자 한 명의 검색만 대신 해 주는 구조라, 데이터 제공처가 허용하는 "개인의 장소 선택을 위한 제한된 조회"에 맞습니다.
- 다른 사이트에서는 이 Worker를 부를 수 없도록 `https://lee2017-bit.github.io` 에서 온 요청만 허용합니다.
  도메인을 바꾸면 Worker의 Settings → Variables and Secrets에 `ALLOW_ORIGIN` (Text)을 새 주소로 추가하세요.
- 같은 검색어는 하루 동안 캐시하도록 만들었지만, `workers.dev` 주소에서는 Cloudflare Cache API가 동작하지 않습니다 (k-skill 프록시 쪽 5분 캐시만 적용).
- 실시간 조회를 끄려면 GitHub Variable `LOOKUP_API` 를 지우고 Run workflow 하면 됩니다.

---

## 물어보기 (AI 채팅, Cloudflare Workers AI)

`ask.html` — "딥스테이션에서 5시까지 다이빙하고 뭐 먹지?"처럼 물으면 근처 가게 중에서 골라 줍니다.
**API 키는 필요 없습니다.** 같은 Worker에 Workers AI 바인딩(`worker/wrangler.jsonc` 의 `"ai": {"binding": "AI"}`)만 있으면
main에 푸시할 때 Cloudflare가 함께 배포합니다.

- 비용: Workers Free 플랜은 Workers AI **하루 10,000 뉴런 무료**(UTC 0시 초기화). 넘으면 요금이 나가는 대신 그날은 "오늘 AI 무료 사용량을 다 썼어요"가 뜹니다.
  질문 1개 ≈ 30~60 뉴런이라 하루 150~300개 질문 정도입니다. (Workers Paid로 올리면 초과분이 과금되니 무료 플랜을 유지하세요.)
- 모델: `@cf/google/gemma-4-26b-a4b-it` (실패하면 `@cf/qwen/qwen3-30b-a3b-fp8`). `worker/chat.js` 의 `MODELS`.
- 흐름: ① AI가 질문에서 장소·식사 시각·원하는 음식을 뽑고 Worker가 카카오 장소 검색(k-skill 프록시)으로 좌표를 찾음
  ② 사이트가 저장 데이터(주인장 픽·취향 포함)와 그 근처 실시간 eat.pl 가게로 후보 18곳을 만듦(영업시간·프랜차이즈·종류로 거름)
  ③ AI가 후보 번호만 골라 이유를 씀 ④ 카드와 eat.pl 링크는 사이트가 데이터 그대로 그림 — AI가 가게나 링크를 지어낼 수 없습니다.
- 남용 방지: 등록한 사이트(Origin)에서 온 요청만, IP당 하루 질문 20개, 질문 200자, 후보 20곳 제한, 같은 질문(띄어쓰기·문장부호 무시)은 2일 캐시.
  `workers.dev` 주소에서는 Cloudflare Cache API가 동작하지 않아서, 캐시와 횟수는 Durable Object `ChatStore`(Free 플랜의 SQLite DO,
  `wrangler.jsonc` 의 `durable_objects`·`migrations`)에 둡니다. 따로 만들 것 없이 배포 때 함께 생깁니다. IP는 날짜와 섞은 해시로만 세고, 매일 지난 기록을 지웁니다.
- 예시 질문 답은 미리 만들어 둠: `node tools/ask_presets.js` (배포된 사이트에서 실제로 물어 `data/ask_presets.json` 에 저장) → 예시를 누르면 AI를 부르지 않고 바로 답함.
  예시 목록은 `build.py` 의 `ASK_EXAMPLES` (홈에는 4개씩 무작위). 데이터가 많이 바뀌면 다시 돌리면 됩니다.
- 끄려면 `worker/wrangler.jsonc` 에서 `ai` 줄을 지우면 됩니다 (물어보기 화면은 "AI 설정이 아직 안 되어 있어요"를 보여 줌).
