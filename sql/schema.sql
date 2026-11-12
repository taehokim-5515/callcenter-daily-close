-- 콜센터 일마감 Supabase 스키마 (cc_ 접두어: 같은 프로젝트의 다른 앱 테이블과 공존)
-- Supabase 대시보드 > SQL Editor 에 붙여넣고 Run. 여러 번 실행해도 안전하다.
-- 앱은 service_role(sb_secret) 키로 서버에서 접속하므로 RLS 를 켜고 정책은 만들지 않는다.

-- 일자 x 마켓 x 문의유형 건수 (SCM 일자 시트)
create table if not exists cc_calls (
  call_date  date not null,
  market     text not null,
  category   text not null,   -- 배송 | 상품 | 단순문의 | 취소/반품/교환 | 더리얼 | 밥이보약 | 가맛시
  type       text not null,
  cnt        int  not null default 0,
  primary key (call_date, market, category, type)
);

-- 일자별 반품 처리건수 (일자 시트 오른쪽 처리표)
create table if not exists cc_returns (
  ret_date  date not null,
  kind      text not null,             -- 취소 | 교환 | 반품
  reason    text not null default '',  -- 반품 사유(취소·교환은 '')
  cnt       int  not null default 0,
  primary key (ret_date, kind, reason)
);

-- 월별 반품 누계 (월별반품누계 시트 값: 일자 기록이 없는 달의 출처)
create table if not exists cc_monthly_returns (
  year    int  not null,
  month   int  not null,
  reason  text not null,
  cnt     int  not null default 0,
  primary key (year, month, reason)
);

-- 업로드 이력
create table if not exists cc_uploads (
  id           bigint generated always as identity primary key,
  year         int not null,
  month        int not null,
  filename     text,
  days         text,      -- 반영한 일자 목록(쉼표)
  call_rows    int,
  uploaded_at  timestamptz not null default now()
);

-- 브랜드 클레임 기록 + AI 분석 리포트
create table if not exists cc_claims (
  id           bigint generated always as identity primary key,
  claim_date   date not null,
  brand        text not null,
  product      text not null,
  claim_type   text not null,
  qty          numeric,
  market       text,
  direction    text,      -- 담당자가 기입한 처리 방향성
  detail       text,      -- 고객 진술, 로트, 사진 유무 등
  ai_report    text,
  ai_model     text,
  status       text not null default '접수',   -- 접수 | 진행 | 완료
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists cc_claims_product on cc_claims (brand, product);

alter table cc_calls enable row level security;
alter table cc_returns enable row level security;
alter table cc_monthly_returns enable row level security;
alter table cc_uploads enable row level security;
alter table cc_claims enable row level security;
