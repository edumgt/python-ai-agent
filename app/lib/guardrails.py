BLOCKED_TERMS = [
    "주가 조작",
    "내부자 거래",
    "시세 조작",
    "불법 매매",
    "미공개 정보",
    "해킹",
    "위조",
    "탈세",
    "사기",
    "자금 세탁",
]

BLOCKED_RESPONSE = (
    "요청하신 내용은 불법적 금융 행위를 포함할 수 있어 도와드릴 수 없습니다.\n"
    "합법적인 투자 정보, 금융상품 비교, 신용 분석 등은 안내해드릴 수 있습니다."
)


def check_guardrails(text: str) -> tuple[bool, str]:
    q = text.lower()
    for term in BLOCKED_TERMS:
        if term.lower() in q:
            return True, BLOCKED_RESPONSE
    return False, ""
