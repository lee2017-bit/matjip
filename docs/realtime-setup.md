# 실시간 조회 켜기 (선택)

지금 사이트는 미리 모아 둔 가게들 안에서만 검색합니다. 아래 3단계를 마치면 목록에 없는 가게도
"실시간으로 찾아보기" 버튼으로 바로 매출점수를 볼 수 있습니다.

- **비용: 0원.** 카카오 로컬 API와 Cloudflare Workers 무료 플랜 모두 카드 등록이 필요 없습니다.
  Workers 무료 플랜은 하루 10만 요청까지이고, 넘으면 요금이 나가는 대신 그날은 오류가 납니다.
- **걸리는 시간: 15~20분.** 계정 가입이 필요해서 사이트 주인이 직접 해야 합니다.

| 무엇 | 어디에 넣나 | 공개돼도 되나 |
|---|---|---|
| 카카오 **REST API 키** | Cloudflare Worker의 Secret `KAKAO_REST_KEY` | **안 됨** (GitHub·사이트·채팅에 붙여 넣지 마세요) |
| Worker 주소 `https://matjip-lookup.<계정>.workers.dev` | GitHub 저장소 Variable `LOOKUP_API` | 됨 (사이트 HTML에 들어갑니다) |

---

## 1단계. 카카오 REST API 키 만들기 (5분)

1. <https://developers.kakao.com> 에 카카오 계정으로 로그인합니다. 처음이면 개발자 등록 약관에 동의합니다.
2. 위쪽 **내 애플리케이션** → **애플리케이션 추가하기**
   - 앱 이름: `매출맛집`, 회사명: `개인` (아무거나 괜찮습니다) → 저장
3. 만든 앱을 누르고 **앱 키**(또는 플랫폼 키) 화면에서 **REST API 키**를 복사합니다.
   - JavaScript 키, Admin 키가 아니라 **REST API 키**입니다. Admin 키는 절대 쓰지 마세요.
4. 왼쪽 메뉴에 **카카오맵**(제품 설정)이 보이면 들어가서 **사용 설정을 ON** 합니다.
   최근 만든 앱은 이걸 켜야 장소 검색 API가 동작합니다.

## 2단계. Cloudflare Worker 만들기 (10분)

1. <https://dash.cloudflare.com/sign-up> 에서 이메일로 가입하고 메일 인증을 합니다.
   도메인을 추가하라는 화면이 나오면 건너뜁니다.
2. 왼쪽 메뉴 **Workers & Pages**(또는 Compute → Workers) → **Create** → **Create Worker**
   (Hello World 템플릿) → 이름을 `matjip-lookup` 으로 바꾸고 **Deploy**
3. **Edit code** → 왼쪽 코드를 전부 지우고 아래 파일 내용을 통째로 붙여 넣은 뒤 오른쪽 위 **Deploy**
   - <https://raw.githubusercontent.com/lee2017-bit/matjip/main/worker/lookup.js>
4. Worker 화면 → **Settings** → **Variables and Secrets** → **Add**
   - Type: **Secret**, Variable name: `KAKAO_REST_KEY`, Value: 1단계에서 복사한 REST API 키 → **Deploy**(저장)
5. Worker 주소를 확인합니다: `https://matjip-lookup.<계정이름>.workers.dev`
6. 브라우저에서 `https://matjip-lookup.<계정이름>.workers.dev/?q=진주회관` 을 열어
   `"item"` 안에 `merUrl` 이 보이면 성공입니다.
   - `KAKAO_REST_KEY가 설정되지 않았어요` → 4번을 다시 확인
   - `주소 검색에 실패했어요` → 키가 REST API 키인지, 1단계 4번(카카오맵 사용 설정)을 확인

## 3단계. 사이트에 연결하기 (2분)

1. <https://github.com/lee2017-bit/matjip/settings/variables/actions> 를 엽니다.
   (저장소 Settings → Secrets and variables → Actions → **Variables** 탭)
2. **New repository variable**
   - Name: `LOOKUP_API`
   - Value: `https://matjip-lookup.<계정이름>.workers.dev` (끝에 `/` 없이)
3. 저장소 **Actions** 탭 → **Build and deploy site** → **Run workflow** 를 누르면 3~5분 뒤 반영됩니다.
   Worker 주소만 Claude에게 알려 주면 이 단계는 대신 해도 됩니다 (주소는 공개 정보라 괜찮습니다).
4. 사이트 홈에서 목록에 없는 가게를 검색해 **"실시간으로 찾아보기"** 버튼이 나오면 끝입니다.

---

## 동작 방식과 주의할 점

- 방문자가 버튼을 누르면 Worker가 ① 카카오 장소 검색으로 가게 도로명 주소를 찾고(음식점·카페만, 최대 2곳)
  ② 그 주소로 eat.pl 잇플 검색을 불러 ③ 이름이 맞는 가게의 점수와 eat.pl 링크를 돌려줍니다.
- Worker는 eat.pl **검색 API만** 부릅니다. 맛집 상세 페이지(merUrl)는 서버에서 호출하지 않고 그대로 넘겨주기만 하므로,
  방문자가 링크를 눌러야 집계됩니다 (공모전 규칙 준수).
- 다른 사이트에서는 이 Worker를 부를 수 없도록 `https://lee2017-bit.github.io` 에서 온 요청만 허용합니다.
  도메인을 바꾸면 Worker의 Variables에 `ALLOW_ORIGIN` (Text)을 새 주소로 추가하세요.
- 같은 검색어·주소는 7일간 캐시하도록 만들었지만, `workers.dev` 주소에서는 Cloudflare 캐시가 제한될 수 있습니다.
  방문자가 아주 많아져 eat.pl 프록시가 느려지면 알려 주세요 (KV 캐시를 붙이면 됩니다).
- 키가 어딘가에 노출됐다면 카카오 developers에서 REST API 키를 재발급하고 Worker Secret만 바꾸면 됩니다.
- 실시간 조회를 끄려면 GitHub Variable `LOOKUP_API` 를 지우고 Run workflow 하면 됩니다.
