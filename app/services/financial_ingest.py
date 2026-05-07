"""CSV 데이터 인제스트: 개인CB, 기업CB, 금융상품."""
import csv
import os
from collections import defaultdict
from datetime import datetime, timezone
import aiosqlite
from app.config import settings


def _safe_float(v: str) -> float | None:
    try:
        f = float(v)
        return None if abs(f) > 1e14 else f
    except (ValueError, TypeError):
        return None


def _safe_int(v: str) -> int | None:
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ── 개인 CB ──────────────────────────────────────────────────────────
async def ingest_personal_cb(db: aiosqlite.Connection, log: list[str]) -> int:
    data_dir = os.path.join(settings.DATA_DIR, "09.개인 CB정보")
    if not os.path.isdir(data_dir):
        log.append("[WARN] 개인CB 디렉토리 없음")
        return 0

    await db.execute("DELETE FROM personal_cb_stats")
    await db.commit()

    total_files, total_rows = 0, 0

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".csv"):
            continue
        fpath = os.path.join(data_dir, fname)
        log.append(f"[개인CB] {fname} 처리 중...")

        # Aggregate: key=(stdt, gender, age_band)
        agg: dict[tuple, dict] = defaultdict(lambda: {"cnt": 0, "sum_s": 0.0, "sum_s6": 0.0,
                                                       "sum_p1": 0.0, "sum_p2": 0.0})
        rows_in_file = 0

        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader)
            # Find column indices
            h = {c: i for i, c in enumerate(header)}
            idx = {
                "stdt": h.get("STDT", 0),
                "gender": h.get("GENDER", 2),
                "age_band": h.get("AGE_BAND", 3),
                "score": h.get("SCORE", len(header) - 6),
                "score_6m": h.get("SCORE_6M", len(header) - 5),
                "perf1": h.get("PERF1", len(header) - 4),
                "perf2": h.get("PERF2", len(header) - 3),
            }

            for row in reader:
                if len(row) < 4:
                    continue
                try:
                    stdt = row[idx["stdt"]]
                    gender = _safe_int(row[idx["gender"]])
                    age_band = _safe_int(row[idx["age_band"]])
                    score = _safe_float(row[idx["score"]])
                    score_6m = _safe_float(row[idx["score_6m"]])
                    perf1 = _safe_float(row[idx["perf1"]])
                    perf2 = _safe_float(row[idx["perf2"]])

                    k = (stdt, gender, age_band)
                    a = agg[k]
                    a["cnt"] += 1
                    if score is not None:
                        a["sum_s"] += score
                    if score_6m is not None:
                        a["sum_s6"] += score_6m
                    if perf1 is not None:
                        a["sum_p1"] += perf1
                    if perf2 is not None:
                        a["sum_p2"] += perf2
                    rows_in_file += 1
                except (IndexError, ValueError):
                    continue

        # Insert aggregates
        for (stdt, gender, age_band), a in agg.items():
            cnt = a["cnt"]
            if cnt == 0:
                continue
            await db.execute(
                "INSERT INTO personal_cb_stats "
                "(stdt, gender, age_band, cnt, avg_score, avg_score_6m, default_rate_1, default_rate_2) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (stdt, gender, age_band, cnt,
                 round(a["sum_s"] / cnt, 2), round(a["sum_s6"] / cnt, 2),
                 round(a["sum_p1"] / cnt, 6), round(a["sum_p2"] / cnt, 6))
            )
        await db.commit()
        log.append(f"  → {rows_in_file:,}행 처리 / 집계 {len(agg)}건 저장")
        total_rows += rows_in_file
        total_files += 1

    return total_rows


# ── 기업 CB ──────────────────────────────────────────────────────────
async def ingest_corporate_cb(db: aiosqlite.Connection, log: list[str]) -> int:
    data_dir = os.path.join(settings.DATA_DIR, "10.기업 CB정보")
    if not os.path.isdir(data_dir):
        log.append("[WARN] 기업CB 디렉토리 없음")
        return 0

    await db.execute("DELETE FROM corporate_cb_stats")
    await db.commit()

    total_rows = 0

    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".csv"):
            continue
        fpath = os.path.join(data_dir, fname)
        log.append(f"[기업CB] {fname} 처리 중...")

        agg: dict[tuple, dict] = defaultdict(lambda: {"cnt": 0, "sum_g": 0.0, "sum_d": 0.0})
        rows_in_file = 0

        with open(fpath, encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader)
            h = {c: i for i, c in enumerate(header)}
            idx = {
                "bs_dt": h.get("BS_DT", 0),
                "sic_cd": h.get("SIC_CD_3", 2),
                "wg_gb": h.get("WG_GB", 3),
                "corp_grad": h.get("CORP_GRAD", len(header) - 2),
                "perf_12m": h.get("PERF_12M", len(header) - 1),
            }

            for row in reader:
                if len(row) < 4:
                    continue
                try:
                    bs_dt = row[idx["bs_dt"]]
                    sic_cd = row[idx["sic_cd"]]
                    wg_gb = _safe_int(row[idx["wg_gb"]])
                    corp_grad = _safe_float(row[idx["corp_grad"]])
                    perf_12m = _safe_float(row[idx["perf_12m"]])

                    k = (bs_dt, sic_cd, wg_gb)
                    a = agg[k]
                    a["cnt"] += 1
                    if corp_grad is not None and corp_grad < 100:
                        a["sum_g"] += corp_grad
                    if perf_12m is not None:
                        a["sum_d"] += perf_12m
                    rows_in_file += 1
                except (IndexError, ValueError):
                    continue

        for (bs_dt, sic_cd, wg_gb), a in agg.items():
            cnt = a["cnt"]
            if cnt == 0:
                continue
            await db.execute(
                "INSERT INTO corporate_cb_stats "
                "(bs_dt, sic_cd, wg_gb, cnt, avg_corp_grad, default_rate) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (bs_dt, sic_cd, wg_gb, cnt,
                 round(a["sum_g"] / cnt, 3), round(a["sum_d"] / cnt, 6))
            )
        await db.commit()
        log.append(f"  → {rows_in_file:,}행 처리 / 집계 {len(agg)}건 저장")
        total_rows += rows_in_file

    return total_rows


# ── 금융상품 ─────────────────────────────────────────────────────────
async def ingest_bank_products(db: aiosqlite.Connection, log: list[str]) -> int:
    fpath = os.path.join(settings.DATA_DIR, "12.금융상품정보", "은행수신상품.csv")
    if not os.path.exists(fpath):
        log.append("[WARN] 은행수신상품.csv 없음")
        return 0

    await db.execute("DELETE FROM bank_products")
    await db.commit()
    count = 0

    with open(fpath, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            base = _safe_float(row.get("기본금리", ""))
            max_r = _safe_float(row.get("최대우대금리", ""))
            await db.execute(
                "INSERT INTO bank_products "
                "(bank_code, bank_name, product_code, product_name, product_group, "
                "min_period, max_period, min_amount, max_amount, base_rate, max_rate, "
                "deposit_type, maturity, deposit_protection, product_summary) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.get("은행코드"), row.get("은행명"), row.get("상품코드"), row.get("상품명"),
                    row.get("상품그룹명"), row.get("계약기간개월수_최소구간"),
                    row.get("계약기간개월수_최대구간"), row.get("가입금액_최소구간"),
                    row.get("가입금액_최대구간"), base, max_r,
                    row.get("예금입출금방식"), row.get("만기여부"),
                    row.get("예금자보호대상여부"), (row.get("상품개요_설명") or "")[:500],
                )
            )
            count += 1
            if count % 1000 == 0:
                await db.commit()

    await db.commit()
    log.append(f"[은행상품] {count:,}건 저장")
    return count


async def ingest_fund_products(db: aiosqlite.Connection, log: list[str]) -> int:
    fpath = os.path.join(settings.DATA_DIR, "12.금융상품정보", "공모펀드상품.csv")
    if not os.path.exists(fpath):
        log.append("[WARN] 공모펀드상품.csv 없음")
        return 0

    await db.execute("DELETE FROM fund_products")
    await db.commit()
    count = 0

    with open(fpath, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            await db.execute(
                "INSERT INTO fund_products "
                "(eval_date, fund_code, fund_name, company_name, main_type, mid_type, sub_type, "
                "strategy, aum, risk_grade, nav, return_1y, expense_ratio, is_retirement, is_esg) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.get("평가기준일"), row.get("펀드코드"), row.get("펀드명"),
                    row.get("운용사명"), row.get("대유형"), row.get("중유형"), row.get("소유형"),
                    (row.get("투자전략") or "")[:300],
                    _safe_float(row.get("순자산", "")),
                    _safe_int(row.get("투자위험등급", "")),
                    _safe_float(row.get("펀드기준가", "")),
                    _safe_float(row.get("펀드성과정보_1년", "")),
                    _safe_float(row.get("운용보수", "")),
                    1 if row.get("퇴직연금", "N") == "Y" else 0,
                    1 if row.get("ESG(사회책임투자형)", "N") == "Y" else 0,
                )
            )
            count += 1
            if count % 1000 == 0:
                await db.commit()

    await db.commit()
    log.append(f"[공모펀드] {count:,}건 저장")
    return count


async def run_full_ingest(db: aiosqlite.Connection, log: list[str]) -> dict:
    log.append("=== 금융 데이터 인제스트 시작 ===")
    pcb = await ingest_personal_cb(db, log)
    ccb = await ingest_corporate_cb(db, log)
    bank = await ingest_bank_products(db, log)
    fund = await ingest_fund_products(db, log)
    log.append(f"=== 완료: 개인CB {pcb:,}행, 기업CB {ccb:,}행, 은행상품 {bank:,}건, 펀드 {fund:,}건 ===")
    return {"personal_cb_rows": pcb, "corporate_cb_rows": ccb,
            "bank_products": bank, "fund_products": fund}
