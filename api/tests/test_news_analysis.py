import pytest
from pydantic import ValidationError

from src.news.analysis import NewsAnalysis, NewsItem, finalize

ITEM = NewsItem(id=1, headline="h", summary="s", tags=["AAPL", "MSFT", "SPY"],
                tracked=["AAPL", "MSFT"])


def analysis(**overrides) -> NewsAnalysis:
    base = {
        "id": 1, "headline_th": "หัวข่าว", "category": "earnings",
        "importance": 5, "is_market_wide": False,
        "per_symbol": [{"symbol": "AAPL", "relevance": 0.9, "sentiment": 0.6}],
    }
    return NewsAnalysis.model_validate({**base, **overrides})


def test_relevant_company_news_keeps_its_importance():
    final = finalize(analysis(), ITEM)
    assert final.importance == 5
    assert [(a.symbol, a.relevance) for a in final.per_symbol] == [("AAPL", 0.9), ("MSFT", 0)]


def test_incidental_tags_cap_importance():
    final = finalize(analysis(per_symbol=[{"symbol": "AAPL", "relevance": 0.2, "sentiment": 0}]),
                     ITEM)
    assert final.importance == 2 and final.analysis.importance == 5  # raw value kept


def test_market_roundups_are_capped_even_when_relevant():
    assert finalize(analysis(category="market_roundup"), ITEM).importance == 2


def test_symbols_outside_the_articles_tracked_tags_are_dropped():
    final = finalize(analysis(per_symbol=[
        {"symbol": "aapl", "relevance": 0.9, "sentiment": 0.5},
        {"symbol": "SPY", "relevance": 1, "sentiment": 1},   # tagged but not tracked
        {"symbol": "TSLA", "relevance": 1, "sentiment": -1},  # invented by the model
    ]), ITEM)
    assert [a.symbol for a in final.per_symbol] == ["AAPL", "MSFT"]


@pytest.mark.parametrize("bad", [
    {"importance": 6}, {"importance": 0}, {"category": "rumour"}, {"id": None},
    {"per_symbol": [{"symbol": "AAPL", "relevance": 1.5, "sentiment": 0}]},
    {"per_symbol": [{"symbol": "AAPL", "relevance": 0.5, "sentiment": -2}]},
])
def test_out_of_range_output_is_rejected(bad):
    with pytest.raises(ValidationError):
        analysis(**bad)


@pytest.mark.parametrize("text", [
    # Seen on the VM with gemma-4-26b-a4b-it:
    "มุมมองข้ามผ่าน โอกาส-อุตดารณ์โอกาส-อุตดารณ์โอกาส-อุตดารณ์โอกาส-อุตดารณ์โอกาส-อุตดาร0.1",
    "ผู้ใช้ iPhone สามารถรับเงินคืนจาก Apple. ,,, ,,, ,,, ,,, ,,, ,,, ,,,",
    "ช่วยให้ค 进进进进进进进(",
    "หุ้น Apple, Palantir -----------------------",
    "นักลงทุนสนใจหุ้น AAPL, PLTR เนื่องจากปัจจัยพื้นฐานและปัจthought: [thought] {",
    "อาจส่งผลต่อค่าใช้จ่ายหรือความเสan์เสียชื่อเสียงของ Apple.",
    "ราคาหุ้นปรับตัวขึ้นหลังจากtheประกาศ",
])
def test_degenerate_text_is_detected(text):
    from src.news.analysis import degeneration

    assert degeneration(text) is not None
    # D-Q2: the headline is dropped, the article's scores are still usable
    assert analysis(headline_th=text).headline_th is None


@pytest.mark.parametrize("text", [
    "ผู้ใช้ iPhone สามารถรับเงินคืนสูงสุด $95 จาก Apple ผ่านโปรแกรม Siri Promises",
    "รายได้ 1,000,000,000,000 ดอลลาร์ เพิ่มขึ้น 12% จากปีก่อน",  # digit groups repeat legitimately
    "ราคาขยับขึ้นลงเล็กน้อย ๆ ตามตลาด",
    "Jim Cramer มองว่ากระแสเชิงลบต่อ Apple, Palantir และ Marvell นั้นรุนแรงเกินไป",
    "หุ้นAAPL และ iPhone รุ่นใหม่ ขายดีในไตรมาส Q3",
])
def test_normal_text_is_accepted(text):
    from src.news.analysis import degeneration

    assert degeneration(text) is None
    assert analysis(headline_th=text).headline_th == text


def test_headline_th_is_trimmed_and_length_capped():
    assert analysis(headline_th="หัวข่าว.  ").headline_th == "หัวข่าว."
    assert analysis(headline_th="ก" * 101).headline_th is None
    assert analysis(headline_th="").headline_th is None
    assert analysis(headline_th=None).headline_th is None
    assert analysis(headline_th=42).headline_th is None
