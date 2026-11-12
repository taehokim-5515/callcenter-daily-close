# 콜센터 일마감

매일 갱신되는 SCM 월 엑셀(예: `11월 SCM.xlsx`)을 올려 일일 유형별 콜, 월간 추이, 월별 반품 누계,
브랜드 클레임을 대시보드로 보고, 클레임 건마다 담당자 방향성을 적으면 Claude 가 처리 방향 리포트를 만듭니다.
모든 기록은 Supabase 에 누적됩니다.

## 화면
- 일일 마감: 마감일 합계, 전 영업일 대비, 월 누계, 유형별 막대, 마켓 x 대분류 표, 그날 반품 처리건수
- 월간 추이: 일자별 대분류 누적 막대, 대분류(또는 세부유형) x 일자 표, 엑셀 받기
- 반품 누계: 연도별 월 x 사유. 일자 기록이 있는 달은 일자 시트 반품 처리표의 합, 없는 달은 월별반품누계 시트 값
- 브랜드 클레임: 브랜드 x 클레임 유형(일자 시트 브랜드 칸 합), 클레임 기록 + AI 리포트(상태 접수/진행/완료, .md 받기)
- 데이터 올리기: 엑셀을 올리면 일자 시트(2, 3, 4 …)를 날짜별로 덮어씀. 여러 번 올려도 중복 없음

## 엑셀 읽는 규칙
- 이름이 숫자인 시트 = 그 달의 일. 2행 대분류, 3행 세부유형, 4행부터 마켓, '합계(유형별)' 행에서 끝
- 오른쪽 '처리건수' 표 = 그날 반품 처리(취소, 교환, 반품 사유 7종). 포장지파손→포장지불량, 배송사→택배파업으로 맞춤
- 요약 시트(문의유형별 건수, 반품, 브랜드별 클레임)는 일자 시트의 합이라 읽지 않음

## 실행
- 로컬: `콜센터마감 실행.bat` → http://localhost:8517 (`.streamlit/secrets.toml` 이 없으면 data/callcenter.db SQLite)

## Supabase
기존 프로젝트(raw-material-db)에 `cc_` 접두어 테이블 5개. 스키마는 `sql/schema.sql` (SQL Editor 에서 Run, 재실행 안전).

## Streamlit Community Cloud 배포
1. 이 폴더를 GitHub **비공개** 저장소로 올림 (`.gitignore` 가 secrets.toml, data 를 뺌)
2. share.streamlit.io → Create app → main file `app/streamlit_app.py`, Python 3.12
3. Advanced settings > Secrets 에 `.streamlit/secrets.toml.example` 의 4개 값 입력
   (SUPABASE_URL, SUPABASE_KEY, ANTHROPIC_API_KEY, APP_PASSWORD)
4. 받는 사람에게 앱 주소와 APP_PASSWORD 만 전달
