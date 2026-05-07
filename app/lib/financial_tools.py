"""SQL-based financial data query tools for the ReAct agent."""
import aiosqlite
from typing import Any

GENDER_MAP = {1: "남성", 2: "여성"}
AGE_MAP = {
    1: "10대이하", 2: "20대", 3: "30대",
    4: "40대", 5: "50대", 6: "60대이상",
}
SIZE_MAP = {1: "대기업", 2: "중견기업", 3: "중소기업"}
INDUSTRY_MAP = {
    "A": "농업/임업/어업", "B": "광업", "C": "제조업",
    "D": "전기/가스", "E": "수도/환경", "F": "건설업",
    "G": "도소매업", "H": "운수/창고", "I": "숙박/음식",
    "J": "정보통신업", "K": "금융/보험", "L": "부동산업",
    "M": "전문/과학/기술", "N": "사업지원", "O": "공공행정",
    "P": "교육서비스", "Q": "보건/사회복지", "R": "예술/스포츠",
    "S": "기타서비스",
}


def _industry_label(sic_cd: str | None) -> str:
    if not sic_cd:
        return "미분류"
    return INDUSTRY_MAP.get(sic_cd[0], sic_cd[0]) if sic_cd else "미분류"


async def query_personal_cb(db: aiosqlite.Connection, args: dict[str, Any]) -> str:
    """개인 CB 신용 통계 조회."""
    conditions, params = [], []

    if p := args.get("period"):
        conditions.append("stdt = ?")
        params.append(str(p))
    if g := args.get("gender"):
        conditions.append("gender = ?")
        params.append(int(g))
    if a := args.get("age_band"):
        conditions.append("age_band = ?")
        params.append(int(a))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    group_by = args.get("group_by", "stdt,gender,age_band")

    sql = f"""
        SELECT stdt, gender, age_band,
               SUM(cnt) as total,
               ROUND(AVG(avg_score), 1) as avg_score,
               ROUND(AVG(avg_score_6m), 1) as avg_score_6m,
               ROUND(AVG(default_rate_1) * 100, 2) as default_pct
        FROM personal_cb_stats
        {where}
        GROUP BY {group_by}
        ORDER BY stdt DESC, age_band
        LIMIT 50
    """
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    if not rows:
        return "조회된 개인 CB 데이터가 없습니다. 먼저 데이터를 인제스트해주세요."

    lines = ["[개인 CB 신용 통계]"]
    for r in rows:
        g_label = GENDER_MAP.get(r["gender"], str(r["gender"]))
        a_label = AGE_MAP.get(r["age_band"], str(r["age_band"]))
        lines.append(
            f"기준월:{r['stdt']} | {g_label}/{a_label} | "
            f"인원:{r['total']:,}명 | 평균신용점수:{r['avg_score']} | "
            f"6개월전:{r['avg_score_6m']} | 연체율:{r['default_pct']}%"
        )
    return "\n".join(lines)


async def query_corporate_cb(db: aiosqlite.Connection, args: dict[str, Any]) -> str:
    """기업 CB 신용 통계 조회."""
    conditions, params = [], []

    if p := args.get("period"):
        conditions.append("bs_dt LIKE ?")
        params.append(f"{p}%")
    if s := args.get("sic_cd"):
        conditions.append("sic_cd LIKE ?")
        params.append(f"{s}%")
    if w := args.get("wg_gb"):
        conditions.append("wg_gb = ?")
        params.append(int(w))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    group_by = args.get("group_by", "bs_dt,sic_cd,wg_gb")

    sql = f"""
        SELECT bs_dt, sic_cd, wg_gb,
               SUM(cnt) as total,
               ROUND(AVG(avg_corp_grad), 2) as avg_grade,
               ROUND(AVG(default_rate) * 100, 2) as default_pct
        FROM corporate_cb_stats
        {where}
        GROUP BY {group_by}
        ORDER BY bs_dt DESC, sic_cd
        LIMIT 50
    """
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    if not rows:
        return "조회된 기업 CB 데이터가 없습니다. 먼저 데이터를 인제스트해주세요."

    lines = ["[기업 CB 신용 통계]"]
    for r in rows:
        w_label = SIZE_MAP.get(r["wg_gb"], str(r["wg_gb"]))
        ind_label = _industry_label(r["sic_cd"])
        lines.append(
            f"기준일:{r['bs_dt']} | {w_label}/{ind_label} | "
            f"기업수:{r['total']:,}개 | 평균신용등급:{r['avg_grade']} | "
            f"연체율:{r['default_pct']}%"
        )
    return "\n".join(lines)


async def search_bank_products(db: aiosqlite.Connection, args: dict[str, Any]) -> str:
    """은행 수신상품 검색."""
    conditions, params = [], []
    limit = min(int(args.get("limit", 10)), 20)

    if min_rate := args.get("min_rate"):
        conditions.append("base_rate >= ?")
        params.append(float(min_rate))
    if bank := args.get("bank_name"):
        conditions.append("bank_name LIKE ?")
        params.append(f"%{bank}%")
    if dtype := args.get("deposit_type"):
        conditions.append("deposit_type LIKE ?")
        params.append(f"%{dtype}%")
    if pg := args.get("product_group"):
        conditions.append("product_group LIKE ?")
        params.append(f"%{pg}%")
    if keyword := args.get("keyword"):
        conditions.append("(product_name LIKE ? OR product_summary LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT bank_name, product_name, product_group, min_period, max_period,
               base_rate, max_rate, deposit_type, deposit_protection, product_summary
        FROM bank_products
        {where}
        ORDER BY base_rate DESC NULLS LAST
        LIMIT ?
    """
    params.append(limit)
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    if not rows:
        return "조건에 맞는 은행 수신상품이 없습니다."

    lines = [f"[은행 수신상품 검색 결과 - {len(rows)}건]"]
    for r in rows:
        lines.append(
            f"■ {r['bank_name']} | {r['product_name']} ({r['product_group']})\n"
            f"  기간:{r['min_period']}~{r['max_period']} | "
            f"기본금리:{r['base_rate']}% | 최대금리:{r['max_rate']}% | "
            f"예금자보호:{r['deposit_protection']} | "
            f"상품유형:{r['deposit_type']}"
        )
    return "\n".join(lines)


async def search_funds(db: aiosqlite.Connection, args: dict[str, Any]) -> str:
    """공모펀드 검색."""
    conditions, params = [], []
    limit = min(int(args.get("limit", 10)), 20)

    if mt := args.get("main_type"):
        conditions.append("main_type LIKE ?")
        params.append(f"%{mt}%")
    if rg := args.get("max_risk_grade"):
        conditions.append("risk_grade <= ?")
        params.append(int(rg))
    if mr := args.get("min_return_1y"):
        conditions.append("return_1y >= ?")
        params.append(float(mr))
    if args.get("is_retirement"):
        conditions.append("is_retirement = 1")
    if args.get("is_esg"):
        conditions.append("is_esg = 1")
    if keyword := args.get("keyword"):
        conditions.append("(fund_name LIKE ? OR company_name LIKE ? OR strategy LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT fund_name, company_name, main_type, mid_type,
               risk_grade, return_1y, expense_ratio, aum, is_retirement, is_esg
        FROM fund_products
        {where}
        ORDER BY return_1y DESC NULLS LAST
        LIMIT ?
    """
    params.append(limit)
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()

    if not rows:
        return "조건에 맞는 펀드 상품이 없습니다."

    lines = [f"[공모펀드 검색 결과 - {len(rows)}건]"]
    for r in rows:
        retire = "✓퇴직연금" if r["is_retirement"] else ""
        esg = "✓ESG" if r["is_esg"] else ""
        lines.append(
            f"■ {r['fund_name']} ({r['company_name']})\n"
            f"  유형:{r['main_type']}/{r['mid_type']} | "
            f"위험등급:{r['risk_grade']} | 1년수익률:{r['return_1y']}% | "
            f"운용보수:{r['expense_ratio']}% | 순자산:{r['aum']:,.0f}원 {retire}{esg}"
        )
    return "\n".join(lines)
