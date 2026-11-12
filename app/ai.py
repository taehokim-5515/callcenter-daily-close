"""브랜드 클레임 → Claude 분석 리포트.

담당자가 기입한 제품명·불만유형·수량·방향성에 DB 이력(같은 제품·브랜드의 과거 클레임, 이번 달 클레임 문의 추이,
반품 사유 추이)을 붙여 보내고, 정해진 목차의 한국어 마크다운 리포트를 받는다.
"""
from __future__ import annotations

import os

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM = """당신은 반려동물 사료·간식 제조사(브랜드: 더리얼, 밥이보약, 가맛시)의 고객 클레임 대응을 돕는 품질·CS 분석가입니다.
콜센터 담당자가 접수한 클레임 한 건과 회사 데이터(과거 클레임 기록, 문의·반품 통계)를 받아, 담당자와 품질팀이 바로 실행할 수 있는 처리 방향을 제시합니다.

원칙
- 반려동물 건강이 걸린 유형(구토/설사, 섭취거부, 이물/혼입, 벌레/곰팡이)은 안전을 먼저 본다. 증상이 이어지면 수의사 진료를 권하고, 진료비 처리 기준은 회사 규정 확인 사항으로 남긴다.
- 주어진 데이터에 없는 수치·로트·규정은 지어내지 않는다. 모르는 것은 '확인 필요'로 적고 무엇을 확인할지 쓴다.
- 담당자가 적은 방향성을 먼저 평가한다: 타당한 점, 빠진 점, 바꿀 점. 더 나은 방향이 있으면 근거와 함께 제안한다.
- 같은 제품·유형이 반복되면 단건 보상이 아니라 로트 추적·생산 점검·출고 보류 같은 재발 방지로 올린다.
- 짧고 구체적으로. 문장은 존댓말 평서문, 각 항목은 누가·무엇을·언제까지.

출력 형식(마크다운, 이 목차 그대로)
## 한 줄 판단
## 상황 요약
## 데이터에서 보이는 것
## 담당자 방향성 검토
## 권장 처리 방향
(고객 응대 / 제품·품질 확인 / 물류·유통 / 재발 방지 로 나눠 번호 목록)
## 우선순위와 기한
## 고객 안내 문구 초안
## 확인이 필요한 정보
"""


class AiError(RuntimeError):
    pass


def get_api_key(secrets=None) -> str | None:
    if secrets is not None:
        try:
            k = secrets.get("ANTHROPIC_API_KEY")
            if k:
                return k
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get("ANTHROPIC_API_KEY")


def build_prompt(claim: dict, context: str) -> str:
    lines = [
        "# 접수된 클레임",
        f"- 접수일: {claim.get('claim_date')}",
        f"- 브랜드: {claim.get('brand')}",
        f"- 제품명: {claim.get('product')}",
        f"- 불만 유형: {claim.get('claim_type')}",
        f"- 수량: {claim.get('qty') if claim.get('qty') not in (None, '') else '미기입'}",
        f"- 접수 채널(마켓): {claim.get('market') or '미기입'}",
        f"- 상세 내용: {claim.get('detail') or '없음'}",
        f"- 담당자 방향성: {claim.get('direction') or '미기입'}",
        "",
        "# 회사 데이터",
        context,
        "",
        "위 클레임에 대한 분석 리포트를 지정한 목차로 작성하세요.",
    ]
    return "\n".join(lines)


def analyze(claim: dict, context: str, api_key: str, model: str = MODEL) -> tuple[str, str]:
    """(리포트 마크다운, 실제 응답 모델) 을 돌려준다."""
    client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=300.0)
    try:
        raw = client.beta.messages.with_raw_response.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(claim, context)}],
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default", "output_config": {"effort": "high"}},
        )
    except anthropic.AuthenticationError as e:
        raise AiError("Claude API 키가 맞지 않습니다. Secrets 의 ANTHROPIC_API_KEY 를 확인하세요.") from e
    except anthropic.RateLimitError as e:
        raise AiError("요청이 몰려 잠시 막혔습니다. 1분 뒤 다시 눌러 주세요.") from e
    except anthropic.APIStatusError as e:
        raise AiError(f"Claude API 오류({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AiError("Claude API 에 연결하지 못했습니다. 네트워크를 확인하세요.") from e

    msg = raw.http_response.json()
    if msg.get("stop_reason") == "refusal":
        raise AiError("모델이 이 요청에 답하지 않았습니다. 상세 내용을 다듬어 다시 시도하세요.")
    text = "\n".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise AiError("빈 응답을 받았습니다. 다시 시도하세요.")
    if msg.get("stop_reason") == "max_tokens":
        text += "\n\n_(응답이 길어 중간에 끊겼습니다.)_"
    return text, msg.get("model", model)
