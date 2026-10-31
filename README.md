# 매출맛집 — 회사 근처 점심·회식 맛집을 카드 매출 순위로

별점 대신 BC카드 최근 1개월 결제 데이터(eat.pl 잇플)의 매출 순위로 동네별 맛집을 보여 주는 정적 사이트입니다.

- `areas.json` — 지역 목록과 검색 주소 키워드
- `build.py` — 지역별 순위를 받아 `site/`에 HTML을 만든다 (Python 표준 라이브러리만 사용)
- `.github/workflows/pages.yml` — GitHub Pages 배포, 매주 월요일 데이터 갱신

로컬 실행: `python3 build.py` → `site/index.html`

데이터: eat.pl 잇플 · BC카드 결제 데이터 기반 ([k-skill 잇플 스킬](https://github.com/NomaDamas/k-skill/tree/main/bccard-eatpl-search) 경유). 이 사이트는 BC카드나 eat.pl의 공식 서비스가 아닙니다.
