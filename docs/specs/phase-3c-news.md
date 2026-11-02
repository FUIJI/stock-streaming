# Phase 3c — ข่าวหุ้น + สรุปด้วย LLM + แจ้งเตือน

> สถานะ: **Approved — กำลังทำ** · Branch: `feat/news` · ตัดสินใจครบจากการ grilling (Q1–Q18)
>
> วิธีทดสอบ: เขียน test ไปพร้อมโค้ด (ส่วนที่มี logic ชัดเจนเขียน test ก่อน)

## เป้าหมาย
ระหว่างวันเปิดหน้าเดียวแล้วรู้ทันทีว่า **หุ้นที่ติดตามมีข่าวสำคัญอะไร ข่าวนั้นดีหรือร้าย และราคาตอบสนองจริงไหม**
และ pipeline ต้องดีพอโชว์ในพอร์ต: ข้อมูลไม่หายเมื่อ LLM ล่ม และวัดคุณภาพของ LLM ได้

## ภาพรวม
```
Alpaca News REST ──(ทุก 60 วินาที; ตอนเริ่มระบบดึงย้อนหลัง 3 วัน)──► ตาราง news (ข่าวดิบ, ai_status = pending)
                                                              │
         LLM worker: รวมเป็นชุด ≤10 ข่าว → Gemini (structured JSON, temp 0) → Pydantic → กฎในโค้ด
                                                              ▼
                                       news_ai + news_symbols  (ai_status = done / failed)
                                                              │
   GET /news ── + ราคาขยับหลังข่าว 5m/30m (คำนวณจาก ohlcv_1m) ──► UI: แผงข่าว · หมุดบนกราฟ · toast/กระดิ่ง
```
ทุกอย่างรันใน API process เดิมแบบ background task (เหมือน reconcile) ไม่เพิ่ม container

## Decisions (สรุปจาก grilling)
| # | เรื่อง | ตัดสินใจ |
|---|---|---|
| Q1 | เป้าหมาย | ใช้ตัดสินใจระหว่างวัน + โชว์ในพอร์ต · ข่าวช้าได้ไม่เกิน 1–2 นาที |
| Q2 | บทบาท LLM | ใช้กับทุกข่าว · ถ้า LLM ใช้ไม่ได้ แสดงหัวข่าวภาษาอังกฤษโดยไม่มีคะแนน |
| Q3 | ขอบเขต | เฉพาะหุ้นที่ติดตาม (`SYMBOLS`) |
| Q4 | ภาษา | สรุปภาษาไทย · หัวข่าวภาษาอังกฤษพร้อมลิงก์ต้นฉบับ |
| — | แหล่งข่าว | Alpaca News `/v1beta1/news` (ยืนยันแล้วว่าใช้กับแผนฟรีได้) · `content` ว่าง ได้แค่ `headline` + `summary` · ต้องแปลง HTML entity |
| Q5 | Provider | Gemini ผ่าน `LLMProvider` interface · `LLM_MODEL=gemini-3.8-flash` (ชื่อจาก AI Studio ของผู้ใช้) · เรียกผ่าน REST `generateContent` ด้วย httpx (ไม่เพิ่ม SDK) · ไม่เปิด Google Search grounding |
| Q6 | Output ต่อข่าว | `summary_th`, `why_it_matters`, `category`, `importance` 1–5, `is_market_wide`, `per_symbol[{symbol, relevance 0–1, sentiment −1..1}]` |
| Q7 | การเรียก LLM | ชุดละ ≤10 ข่าว · ถ้าชุดเสียแตกเป็นทีละข่าว · structured output + Pydantic · temperature ตั้งได้ด้วย `LLM_TEMPERATURE` (เดิมตกลงไว้ที่ 0 แต่ Gemini รุ่น 3 อาจแนะนำให้ใช้ค่าเริ่มต้น ยังยืนยันไม่ได้ ใช้ eval ตัดสิน) · `LLM_THINKING_LEVEL` ตั้งได้ |
| Q8 | Pipeline | เก็บข่าวดิบก่อน · `pending → done / failed` · retry 3 ครั้งแบบ backoff · บันทึก `model` + `prompt_version` · ประมวลผลใหม่เมื่อ `updated_at` เปลี่ยน · ดึงย้อนหลัง 3 วันตอนเริ่ม |
| Q9 | วัดคุณภาพ | ชุดทดสอบ 20 ข่าวจริง (ผู้ใช้ติดป้าย) + `python -m src.news.eval` |
| Q10 | importance | เกณฑ์ 1–5 อยู่ใน prompt (ตารางด้านล่าง) + กฎในโค้ด: relevance สูงสุด < 0.5 หรือ `market_roundup` → importance สูงสุด 2 |
| Q11 | ช่องทางแจ้งเตือน | ในหน้าเว็บ (toast + กระดิ่ง) · มี interface `Notifier` ไว้เพิ่ม Telegram ภายหลัง |
| Q12 | คุมโควตา | `LLM_DAILY_CALL_LIMIT=200` + จำกัดต่อนาที · เกินแล้วค้าง pending (แสดงภาษาอังกฤษไปก่อน) · ข่าวใหม่ได้คิวก่อน · สถานะใน `/health` |
| Q13 | แผงข่าว | ข้างกราฟ (มือถืออยู่ใต้กราฟ) · ข่าวของหุ้นที่เลือก · ตัวกรอง ทั้งหมด/สำคัญ (≥4) · ไม่มีรูป · ป้าย "AI summary · <model>" · ข่าว relevance < 0.5 ซ่อนไว้หลังปุ่ม |
| Q14 | ราคาหลังข่าว | % เปลี่ยนแปลงหลังข่าว 5 และ 30 นาที คำนวณจาก `ohlcv_1m` · ถ้ายังไม่มีราคาแสดง "waiting for market" · ไม่นำไปปรับ importance |
| Q15 | เงื่อนไขแจ้งเตือน | importance ≥ 4 ของทุกหุ้นที่ติดตาม · แจ้งครั้งเดียวต่อข่าว · ข่าวช่วงที่ปิดเว็บนับในกระดิ่งแต่ไม่ขึ้น toast ย้อนหลัง · ข่าว backfill ไม่แจ้ง |
| Q16 | หมุดบนกราฟ | importance ≥ 3 ของหุ้นที่เลือก · สีตาม sentiment · หมุดใหญ่เมื่อ importance = 5 · ชี้แล้วแสดง tooltip · กดแล้วเลื่อนแผงข่าวไปที่ข่าวนั้น · ในกราฟ `1d` รวมเป็นหมุดเดียวต่อวัน |
| Q17 | อายุการเก็บ | เก็บตลอด (~20,000 ข่าวต่อปี) |
| Q18 | หน้าเว็บรับข่าวใหม่ | poll `GET /news` ทุก 60 วินาที |

### เกณฑ์ importance (ใส่ใน prompt)
| คะแนน | ความหมาย | ตัวอย่าง |
|---|---|---|
| 5 | น่าจะทำให้ราคาขยับแรงทันที | งบออกมาต่างจากที่คาดมาก, ปรับ guidance, ควบรวมกิจการ, ถูกสอบสวน/ฟ้องร้องครั้งใหญ่, CEO ลาออก |
| 4 | สำคัญกับบริษัทโดยตรง | อัปเกรด/ดาวน์เกรดจากโบรกใหญ่, เปิดตัวสินค้าหลัก, ได้/เสียสัญญาใหญ่ |
| 3 | เกี่ยวข้องแต่ไม่น่าขยับราคามาก | ข่าวอุตสาหกรรม, คู่แข่งเคลื่อนไหว |
| 2 | กล่าวถึงผ่าน ๆ | ข่าวสรุปตลาดที่มีชื่อบริษัท |
| 1 | แทบไม่เกี่ยว | listicle, แท็กหุ้นมาแบบผิด ๆ |

`category`: `earnings`, `guidance`, `analyst`, `product`, `legal_regulatory`, `m_and_a`, `management`, `macro`, `market_roundup`, `other`

## Data model (migration `0003_news.sql`)
| ตาราง | คอลัมน์หลัก |
|---|---|
| `news` | `id` (id ของ Alpaca, PK), `headline`, `summary`, `author`, `source`, `url`, `symbols text[]`, `created_at`, `updated_at`, `fetched_at`, `backfilled bool`, `ai_status` (`pending`/`done`/`failed`), `ai_attempts`, `ai_next_attempt_at`, `ai_error` |
| `news_ai` | `news_id` (PK, FK), `summary_th`, `why_it_matters`, `category`, `importance` (หลังใช้กฎในโค้ด), `importance_raw` (ค่าจาก LLM), `is_market_wide`, `model`, `prompt_version`, `processed_at` |
| `news_symbols` | (`news_id`, `symbol`) PK, `relevance`, `sentiment` · มีเฉพาะหุ้นที่เราติดตามซึ่งอยู่ในแท็กของข่าว |

Index: `news(created_at DESC)`, `news_symbols(symbol)`, `news(ai_status, ai_next_attempt_at)` สำหรับคิวของ worker

## Tickets
ลำดับ: **N1 → N2 → N3 → N4 → N5 → N6** (backend) แล้ว **N7 → N8 → N9** (UI) · **N10** ทำคู่ขนานได้

### N1 — เก็บข่าว
- migration `0003_news.sql` · `AlpacaClient.news(symbols, start, page_token)`
- poller ทุก `NEWS_POLL_SEC` (60): ดึงข่าวตั้งแต่ `created_at` ล่าสุดที่เก็บไว้ ลบ 5 นาทีเผื่อข่าวมาช้า
- ตอนเริ่มระบบถ้าตารางว่าง ดึงย้อนหลัง `NEWS_BACKFILL_DAYS` (3) และติด `backfilled=true`
- แปลง HTML entity · upsert ตาม `id` · ถ้า `updated_at` เปลี่ยน ให้กลับไปเป็น `pending`
- ✅ ข่าวชิ้นเดิมไม่ถูกเก็บซ้ำ · ข่าวที่ถูกแก้ถูกส่งประมวลผลใหม่ · poller พังแล้ว API ยังรันต่อ

### N2 — LLM provider + prompt
- `LLMProvider` protocol: `async summarize(batch) -> list[NewsAnalysis]` · `GeminiProvider` เรียก REST `generateContent` พร้อม `responseSchema`
- prompt v1: บทบาท, เกณฑ์ importance, category, ภาษาไทย, ใช้เฉพาะข้อมูลในข่าว ห้ามเดา
- ตรวจผลด้วย Pydantic: `symbol` ที่ตอบกลับต้องอยู่ในแท็กของข่าวและเป็นหุ้นที่เราติดตาม · ค่าทุกตัวต้องอยู่ในช่วง · ใช้กฎในโค้ดกับ importance
- ✅ ทดสอบด้วย HTTP จำลอง: ชุดที่ output ถูก, JSON เสีย → แตกทีละข่าว, `symbol` นอกแท็ก → ตัดทิ้ง, กฎ cap importance

### N3 — Worker + โควตา
- ดึงคิว `pending` ที่ถึงเวลา เรียงข่าวใหม่ก่อน (ข่าวประมวลผลใหม่ย้อนหลังไว้ท้ายสุด) ส่งเป็นชุดละ ≤10
- retry แบบ backoff (1, 5, 30 นาที) ครบ 3 ครั้งเป็น `failed`
- `LLM_DAILY_CALL_LIMIT`, `LLM_RPM_LIMIT` · ถ้าเกินให้รอ ไม่ error
- `python -m src.news.reprocess --since … [--prompt-version …]`
- `/health` เพิ่ม `news: {last_poll_at, pending, failed}` และ `llm: {provider, model, calls_today}`
- ✅ เมื่อโควตาหมด ข่าวยังเข้ามาและแสดงได้ · เริ่มวันใหม่แล้วกลับมาประมวลผลต่อ

### N4 — ราคาหลังข่าว
- คำนวณตอน query: ราคาอ้างอิง = close ของแท่ง 1 นาทีล่าสุด ณ เวลาข่าวออก · เทียบกับ close หลัง 5 และ 30 นาที
- ถ้ายังไม่มีแท่งหลังข่าว (ตลาดปิด หรือยังไม่ถึงเวลา) → `null`
- ✅ ทดสอบกับ Postgres จริง: ข่าวระหว่างตลาดเปิด, ข่าวตอนตลาดปิด, ข่าวที่ยังไม่ครบ 30 นาที

### N5 — API
- `GET /news?symbol=&min_importance=&include_hidden=false&since=&before=&limit=50`
  แต่ละรายการมีข้อมูลข่าว + ผลจาก AI (หรือ `null`) + `per_symbol` + `reaction{m5, m30}` + `backfilled`
- ข่าวที่ซ่อน = relevance สูงสุดของหุ้นที่ติดตาม < 0.5 · ใช้ rate limit เดิม · รับเฉพาะหุ้นที่ติดตาม
- ✅ ตัวกรองถูกต้อง · ข่าวที่ยังไม่ผ่าน LLM ก็คืนค่าได้

### N6 — ชุดทดสอบคุณภาพ (eval)
- `python -m src.news.export_eval --limit 20` ดึงข่าวจริงจากฐานข้อมูลเป็น `api/tests/news_eval/cases.json` · ผู้ใช้ติดป้ายคำตอบ
- `python -m src.news.eval` เรียก provider จริง แล้วรายงาน: importance ต่างไม่เกิน ±1, ทิศ sentiment ตรง, relevance ตัดที่ 0.5 ตรง, JSON ถูกต้องทุกข่าว
- ไม่รันใน CI (ต้องใช้ API key และโควตา) · รันเองทุกครั้งที่แก้ prompt

### N7 — แผงข่าว (UI)
- desktop: กราฟกว้าง 2/3 แผงข่าว 1/3 · มือถือ: อยู่ใต้กราฟ
- ข่าว 1 รายการ: เวลา (ICT), ป้าย importance, จุดสี sentiment, `summary_th`, `why_it_matters`, หัวข่าวเป็นลิงก์, % ราคาหลังข่าว, ป้าย AI summary · ถ้ายังไม่ผ่าน LLM แสดงหัวข่าว + summary ภาษาอังกฤษ พร้อมป้าย "pending"
- ตัวกรอง ทั้งหมด/สำคัญ · ปุ่มแสดงข่าวที่เกี่ยวข้องน้อย · poll ทุก 60 วินาที

### N8 — แจ้งเตือน (UI)
- toast เมื่อมีข่าว importance ≥ 4 **ใหม่** ที่ไม่ใช่ backfill ระหว่างเปิดหน้าเว็บ
- กระดิ่งแสดงจำนวนข่าวสำคัญที่ยังไม่ได้อ่าน (`lastSeenNewsId` เก็บใน localStorage) · เปิดหน้าเว็บมาครั้งแรกไม่ขึ้น toast ย้อนหลัง
- ฝั่ง backend: interface `Notifier` (ตอนนี้ไม่ทำอะไร) ไว้เสียบ Telegram

### N9 — หมุดบนกราฟ (UI)
- marker ของ lightweight-charts v5 (`createSeriesMarkers`) · ข่าว importance ≥ 3 · สีตาม sentiment · ขนาดตาม importance
- tooltip เขียนเอง โดยดูจาก crosshair ว่าตรงกับแท่งที่มีหมุด · กดแล้วเลื่อนแผงข่าวไปที่ข่าวนั้น · ในกราฟ `1d` รวมต่อวัน

### N10 — Docs + config
- runbook: สร้าง Gemini key ที่ aistudio.google.com, env ที่ต้องตั้ง, คำสั่ง reprocess/eval, วิธีอ่านสถานะ `news`/`llm` ใน `/health`
- `.env.example` / `deploy/env.vm.example`: `GEMINI_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_DAILY_CALL_LIMIT`, `LLM_RPM_LIMIT`, `NEWS_POLL_SEC`, `NEWS_BACKFILL_DAYS`

## ความเสี่ยงและเรื่องที่ยังไม่ได้ยืนยัน
| เรื่อง | การรับมือ |
|---|---|
| ชื่อโมเดลและโควตาของ Gemini แผนฟรี **ยังไม่ได้ยืนยัน** (sandbox เข้าเอกสารไม่ได้) | ตั้ง `LLM_MODEL` ได้ด้วย env · ตรวจชื่อโมเดลใน AI Studio ก่อน deploy · จำกัดโควตาฝั่งเราไว้ต่ำกว่าแผนฟรีอยู่แล้ว |
| Sandbox เรียก Alpaca และ Gemini ไม่ได้ | ทดสอบด้วย HTTP จำลองที่ใช้รูปแบบจริงจาก curl · eval (N6) ต้องรันบน VM |
| คุณภาพภาษาไทย | วัดด้วย eval (N6) · เปลี่ยน provider ได้ด้วย env |
| Prompt injection จากเนื้อหาข่าว | LLM ไม่มี tool · output ผ่าน schema · `symbol` ต้องอยู่ในแท็กของข่าว |
| แผนฟรีของ Gemini อาจนำข้อมูลไปปรับปรุงโมเดล | ข้อมูลที่ส่งเป็นข่าวสาธารณะเท่านั้น ไม่มีข้อมูลผู้ใช้ |
| RAM | ใช้ API process เดิม · worker ประมวลผลทีละชุด ≤10 ข่าว |

## นอกขอบเขต
Telegram/LINE, ข่าวตลาดภาพรวมที่ไม่ผูกกับหุ้นที่ติดตาม, การนำราคาหลังข่าวไปปรับ importance, watchlist รายผู้ใช้ (Phase 3b)
