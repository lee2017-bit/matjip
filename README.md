# 매출맛집 — 맛집 매출점수 검색, 점심·회식 맛집 순위

별점 대신 BC카드 최근 1개월 결제 데이터(eat.pl 잇플)의 매출 순위로 가게마다 0~100점 **매출점수**를 매기고,
가게 검색·종류별 순위·동네별 순위를 보여 주는 정적 사이트입니다. <https://lee2017-bit.github.io/matjip/>

- `areas.json` — 동네 목록과 검색 주소 키워드 (도로명 또는 '구 동' 단위)
- `build.py` — 동네별 순위를 받아 `site/`에 HTML과 검색 색인(`assets/places.json`)을 만든다 (Python 표준 라이브러리만 사용)
- `data/cache/` — 동네별 eat.pl 응답 캐시 (157개 동네. 매주 월요일 1/4씩 돌아가며 갱신 `--rotate`, 새 동네만 처음에 받음)
- `data/picks.json` — "주인장 픽": 사이트 주인이 카카오맵에 모아 온 맛집 중 eat.pl에 있는 곳 (`tools/picks.py`로 생성)
- `worker/lookup.js` — 목록에 없는 가게를 이름으로, 또는 지도 '내 위치' 주변 동네를 실시간 조회하는 Cloudflare Worker (키 불필요, main의 `worker/`가 바뀌면 Cloudflare가 자동 배포, [docs/realtime-setup.md](docs/realtime-setup.md))
- `worker/chat.js` + `src/ask.js` — '물어보기'(AI 채팅). Cloudflare Workers AI 무료 할당량, 키 불필요. AI는 질문 분석과 후보 중 고르기만 하고 가게·링크는 데이터 그대로 ([docs/realtime-setup.md](docs/realtime-setup.md#물어보기-ai-채팅-cloudflare-workers-ai))
- `src/map.js` — 지도 페이지(Leaflet + OpenStreetMap). eat.pl 좌표(TM128)는 빌드 때 WGS84로 변환
- `data/tags.json` — 주인장이 확인한 '룸 있음'·'고급' 가게와 식당이 아니라서 뺄 곳(`exclude`) (merUrl 또는 가게 이름)
- `data/kakao_check.json` — 카카오맵 교차검증 결과 (`tools/kakao_check.py`). 카카오 업종이 음식점·카페면 업종 그룹이 다를 때 카카오를 따르고(예: 기타외국식→카페) 카드에 카카오 업종 이름을 보여 준다. 카카오에 회사·판매점으로만 나오면 추천 태그를 붙이지 않는다. 매주 새로 눈에 띄는 가게 200곳까지 자동 확인
- `data/taste_model.json` — '주인장 취향' 모델. `tools/taste_train.py`(scikit-learn 필요)가 주인장 픽 vs 같은 건물의 다른 가게로 학습한 로지스틱 회귀 계수. 매출은 입력에서 뺌, 교차검증 AUC 0.78. 빌드는 계수만 읽어 표준 라이브러리로 계산
- `.github/workflows/pages.yml` — GitHub Pages 배포, 매주 월요일 데이터 갱신

로컬 실행: `python3 build.py` → `site/index.html` (검색은 `python3 -m http.server -d site` 로 열어야 동작)

## 매출점수

eat.pl의 백분위(같은 업종 안 매출 순위, 낮을수록 상위)를 `전국 75% + 구 안 25%`로 섞어 0~100점으로 바꾼다.
변환표는 `build.py`의 `SCORE_ANCHORS` (app.js에도 같은 값). 맛 평가가 아니라 판매량 기준이다.

추정 표시(가격·소음 정보는 데이터에 없어 규칙으로 추정, `build.py`와 `app.js`에 같은 규칙):
- 아주 붐비는 집 `is_busy` — 같은 업종 매출 상위 0.5%(회식 적합이면 1%) 이내
- 대화하기 좋은 곳 `is_talk` — 양식·일식·바 등, 회식 결제 없음, 최상위 1%·체인·쇼핑몰 제외, 저녁 영업, 20~30대 손님 (+ 고급 추정, 룸 있음)
- 고급 추정 `is_premium` — 오마카세·다이닝·비스트로·한우 등 이름/프랑스음식/단독 스시집, 체인 제외, 점심~저녁 영업, 상위 70% 안

## 공모전 집계 관련

- 맛집 링크는 받은 merUrl 그대로 `target="_blank" rel="noopener"` 로 연다 (`noreferrer` 금지).
- referrer 정책 `strict-origin-when-cross-origin` 유지. 추적 파라미터·단축 URL·중간 페이지 없음.
- 서버(빌드·Worker)는 eat.pl 검색 API만 부르고 merUrl은 호출하지 않는다.

데이터: eat.pl 잇플 · BC카드 결제 데이터 기반 ([k-skill 잇플 스킬](https://github.com/NomaDamas/k-skill/tree/main/bccard-eatpl-search) 경유). 이 사이트는 BC카드나 eat.pl의 공식 서비스가 아닙니다.
