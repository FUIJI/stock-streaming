# Phase 3d — คัดข่าว + บทสรุปข่าวด้วย AI (รองรับหุ้นจำนวนมาก)

> สถานะ: **Approved — ยังไม่เริ่ม** · ต่อจาก Phase 3c (`docs/specs/phase-3c-news.md`)
>
> ที่มา: ทดลองใช้แผงข่าว N7 จริงแล้วพบว่า **ข่าวเยอะเกิน** และการสรุปภาษาไทยเต็มทุกชิ้นใช้ token ตามจำนวนข่าว
> ซึ่งจะบวมขึ้นเรื่อย ๆ เมื่อเพิ่มหุ้น · ผู้ใช้เลือกแนวทาง "ให้คะแนนทุกข่าวแบบประหยัด + AI เขียนบทสรุปเฉพาะที่สำคัญ"
>
> วิธีทดสอบ: เขียน test ไปพร้อมโค้ด (เหมือน 3c)

## เป้าหมาย
1. เปิดแผงข่าวแล้ว **อ่านบทสรุป 3–5 ข้อ** ก็รู้ว่าหุ้นตัวนี้มีอะไรเกิดขึ้น · ข่าวสำคัญเรียงให้ด้านล่าง · ข่าวที่เหลือพับเก็บ
2. ค่าใช้ AI ต่อวัน **ไม่โตตามจำนวนข่าว** · เพิ่มเป็น 50 หุ้นแล้วยังอยู่ในแผนฟรีของ Gemma
3. ทุกอย่างที่ AI เขียน **ตรวจย้อนกลับได้**: ทุกข้อในบทสรุปต้องอ้างข่าวต้นทาง

## ภาพรวม
```
Alpaca News ─► news (pending)                                   [poller เดิม ไม่เปลี่ยน]
                 │
   ชั้น 1a  กฎในโค้ด (ไม่ใช้ AI) ── ไม่ผ่าน ─► ai_status = skipped (เก็บไว้ ไม่ส่ง AI)
                 │
   ชั้น 1b  AI ให้คะแนนจากหัวข่าว ชุดละ ≤25 ข่าว ─► news_ai (importance, sentiment, relevance,
                 │                                          category, headline_th 1 บรรทัด)
                 │      └─► แจ้งเตือน (importance ≥ 4) · หมุดบนกราฟ (≥ 3)   [แบบทันที]
                 │
   ชั้น 2   บทสรุปรายหุ้น วันละ 3 รอบ เฉพาะหุ้นที่มีข่าว ≥ 3 ใหม่ ─► news_digest (อ้าง id ข่าว)
                 │
   ชั้น 3   บทสรุปทั้งพอร์ต (ทำเมื่อหุ้น > 10 ตัว) ─► news_digest scope = watchlist
                 ▼
   GET /news · GET /digest ─► UI: บทสรุปบนสุด · ข่าวสำคัญ · ข่าวอื่น (พับ)
```

## Decisions
| # | เรื่อง | ตัดสินใจ |
|---|---|---|
| D-Q1 | AI ต่อข่าว | **เลิกสรุปไทยเต็มทุกชิ้น** · เหลือแค่ให้คะแนน: `category`, `importance`, `is_market_wide`, `per_symbol[{symbol, relevance, sentiment}]` + `headline_th` (หัวข่าวไทย 1 บรรทัด ≤ 100 ตัวอักษร) |
| D-Q2 | `headline_th` เสีย | ไม่ทำให้ทั้งข่าวเสีย: ถ้าไม่ผ่านตัวตรวจ (`degeneration()`) เก็บเป็น `null` แล้ว UI ใช้หัวข่าวอังกฤษ · คะแนนยังใช้ได้ |
| D-Q3 | ชุดละกี่ข่าว | `LLM_TRIAGE_BATCH=25` · ถ้า output เสียแบ่งครึ่งแล้วลองใหม่ (ไม่ใช่แตกทีละข่าว) · ส่งแค่ `headline` + `summary` ตัดที่ 300 ตัวอักษร |
| D-Q4 | ความเร็ว | รอรวมชุดไม่เกิน `LLM_TRIAGE_WAIT_SEC=45` หรือจนครบ 25 ข่าว · ข่าวยังช้าไม่เกิน 1–2 นาทีตาม Q1 เดิม |
| D-Q5 | กฎกรอง (ชั้น 1a) | แท็กหุ้น > `NEWS_MAX_TAGS` (5) · หัวข่าวตรงรูปแบบข่าวใส่ของ (รายการ regex ในโค้ด: `stocks? to watch`, `why these \d+ stocks`, `unusual options activity`, `top (gainers\|losers\|movers)`, `whale`, `\d+ stocks? (to\|that)`) · ข่าวที่ไม่ผ่าน = `skipped` + `skip_reason` · regex มี test ทั้งข่าวที่ต้องโดนและต้องไม่โดน |
| D-Q6 | รอบบทสรุป | `DIGEST_TIMES_ET=08:45,12:30,16:30` (ก่อนเปิด, กลางวัน, หลังปิด) · ทุกวันรวมเสาร์-อาทิตย์ แต่**ข้ามหุ้นที่ไม่มีข่าว importance ≥ 3 ใหม่** ตั้งแต่บทสรุปล่าสุด |
| D-Q7 | ข้อมูลเข้าบทสรุป | ข่าว importance ≥ 3 ตั้งแต่บทสรุปล่าสุด (ย้อนได้ไม่เกิน 72 ชม. — ครอบคลุมเสาร์-อาทิตย์) สูงสุด 15 ข่าว เรียงคะแนน · ใส่ `headline`, `summary`, `category`, `sentiment`, `reaction.m30` |
| D-Q8 | รูปบทสรุป | `title_th` (≤ 80) · `bullets[2..5]{text ≤ 160, news_ids[≥1]}` · `tone` (`positive`/`negative`/`mixed`/`neutral`) · **ทุก `news_ids` ต้องอยู่ในข่าวที่ส่งไป** ไม่งั้นถือว่า output เสีย · ผ่านตัวตรวจข้อความเสียทุกช่อง |
| D-Q9 | บทสรุปพัง | retry 3 ครั้ง (1, 5, 30 นาที) แล้วข้ามรอบนั้น · UI แสดงบทสรุปล่าสุดที่มีพร้อมเวลา หรือไม่มีการ์ดบทสรุปเลย (เหลือรายการข่าว) |
| D-Q10 | ลำดับงานของ worker | ให้คะแนนข่าว **ก่อนเสมอ** (แจ้งเตือนต้องเร็ว) → บทสรุป → ประมวลผลย้อนหลัง · โควตาใช้ร่วมกัน `LLM_DAILY_CALL_LIMIT` · กันไว้ให้บทสรุป `LLM_DIGEST_RESERVE=60` call |
| D-Q11 | ของเก่าจาก 3c | `summary_th`/`why_it_matters` เดิมเก็บไว้ (ไม่ลบ) แต่ UI ไม่ใช้แล้ว · ข่าว 3 วันล่าสุดให้คะแนนใหม่ด้วย prompt `t1` ผ่าน `reprocess --older-prompt t1` |
| D-Q12 | ชั้น 3 ทั้งพอร์ต | ออกแบบ table ไว้รองรับแล้ว (`scope`) แต่ **ยังไม่ทำ** จนกว่าหุ้น > 10 ตัว (ticket D9) |
| D-Q13 | วัดคุณภาพ | N6 เดิมย้ายมาวัด **การให้คะแนน** (ตัวเลข เทียบได้) · บทสรุปวัดอัตโนมัติแค่ "อ้าง id ถูก + ผ่านตัวตรวจ" ที่เหลือผู้ใช้ตรวจเองจาก checklist |

### งบ LLM ต่อวัน (ประมาณ — วัดจริงหลัง D2)
| | 4 หุ้น (ตอนนี้) | 50 หุ้น |
|---|---|---|
| ข่าวต่อวัน | ~50–100 | ~500–1,000 |
| ผ่านกฎชั้น 1a (คาด ~70%) | ~35–70 | ~350–700 |
| call ให้คะแนน (ชุดละ 25 + เผื่อแบ่งครึ่ง) | ~5–10 | ~20–40 |
| call บทสรุป (≤ หุ้น × 3 รอบ เฉพาะที่มีข่าว) | ≤ 12 | ≤ 150 (จริงน่าจะ ~60) |
| **รวม** | **≤ ~25** | **≤ ~190** — ไม่เกิน `LLM_DAILY_CALL_LIMIT=200` |

ข้อจำกัดที่ต้องระวังของ Gemma แผนฟรีคือ **16K token/นาที**: ชุดให้คะแนน ~25 × 120 token เข้า + ~25 × 60 ออก + prompt ~1K ≈ 5.5K token/call · ตั้ง `LLM_RPM_LIMIT` ให้ไม่ชนเพดาน (≤ 2 ชุดต่อนาที)

## Data model (migration `0004_news_triage.sql`)
| ตาราง | เปลี่ยนอะไร |
|---|---|
| `news` | `ai_status` เพิ่มค่า `skipped` · เพิ่ม `skip_reason TEXT` |
| `news_ai` | เพิ่ม `headline_th TEXT NULL` · `summary_th`, `why_it_matters` เป็น `NULL` ได้ |
| `news_digest` (ใหม่) | `id` PK · `scope` (`symbol`/`watchlist`) · `symbol` (null เมื่อ watchlist) · `period_start`, `period_end` · `title_th`, `bullets jsonb`, `tone` · `news_ids bigint[]` · `model`, `prompt_version` · `created_at` · index (`scope`, `symbol`, `period_end DESC`) |
| `digest_jobs` (ใหม่) | คิวบทสรุป: (`scope`, `symbol`, `due_at`) · `status`, `attempts`, `next_attempt_at`, `error` — ใช้ retry/backoff แบบเดียวกับ `news` |

## Tickets
ลำดับ: **D1 → D2 → D3** (ชั้น 1 ให้นิ่งก่อน) **→ D4 → D5 → D6** (บทสรุป + UI) **→ D7, D8** · **D10** ทำคู่ขนาน · **D9** รอหุ้น > 10

### D1 — กฎกรองข่าว + สถานะ `skipped`
> **เสร็จ** · เพิ่มจาก spec: รูปแบบข่าวอัตโนมัติของ Benzinga (`Price Over Earnings Overview`, `P/E Ratio Insights`, `How Much You Would Have Made`, `If You Invested $`) · ส่งกลับเข้าคิวด้วย `reprocess --rerun-rules` (ใช้กฎใหม่ทั้งสองทาง) แทน `--status skipped` · `reprocess --status all` ไม่แตะข่าว `skipped` · `/news` รับ `include_skipped` แล้ว (ย้ายมาจาก D5)
- migration `0004` (ตาม Data model) · `src/news/rules.py`: `skip_reason(article) -> str | None`
- poller ใส่ `skipped` ตั้งแต่ตอนบันทึก · worker ไม่หยิบ `skipped` · `reprocess --status skipped` ส่งกลับเข้าคิวได้ (ถ้ากฎเปลี่ยน)
- ✅ test regex: หัวข่าวจริงที่ต้องโดน ("Why These 5 Stocks Are on Investors' Radars", "Unusual Options Activity") และที่ต้องไม่โดน (ข่าวงบ, ข่าวดาวน์เกรด) · ข่าวแท็ก 6 หุ้นโดนข้าม

### D2 — ให้คะแนนแบบชุด (prompt `t1`)
> **โค้ดเสร็จ — รอวัดบน VM** · ผลที่ตัดสินระหว่างทำ: item ที่ไม่ผ่าน validation ทิ้งแค่ตัวนั้น (ไม่ทั้งชุด) แล้วส่งที่เหลือเป็นชุดเล็กลง · `summary_th` เดิมของข่าวเก่าไม่ถูกเขียนทับ · แผงข่าว (N7) แสดง `headline_th` เมื่อไม่มีสรุปเดิม · `LLM_RPM_LIMIT` เริ่มต้นเป็น 2 (TPM 16K) · ไม่สรุปไทยเพิ่มให้ข่าวคะแนน 4–5 (grill: ใช้บทสรุปรายหุ้นแทน)
- `TriageItem` / `TriageBatch` (Pydantic) แทน `NewsAnalysis` · `headline_th` เสียแล้วเป็น `null` ได้ (D-Q2) · `finalize()` + cap importance เดิมใช้ต่อ
- analyzer: ชุดละ `LLM_TRIAGE_BATCH` · output เสียแบ่งครึ่ง (D-Q3) · `maxOutputTokens` ตามจำนวนข่าว
- worker: รอรวมชุดตาม D-Q4 · ลำดับงานตาม D-Q10
- `smoke` รองรับโหมดชุด: รายงานเวลา, token ที่ใช้, จำนวนที่แบ่งครึ่ง
- ✅ HTTP จำลอง: ชุดถูก, ชุดเสีย → แบ่งครึ่ง, `headline_th` เสีย → null แต่คะแนนยังเก็บ, id ที่ไม่ได้ส่งไปถูกทิ้ง · ทดสอบจริงบน VM ด้วยชุด 25 ข่าว ถ้า Gemma หลุดให้ลด batch

### D3 — ชุดทดสอบการให้คะแนน (แทน N6)
- `python -m src.news.export_eval --limit 30` → `api/tests/news_eval/cases.json` · ผู้ใช้ติดป้าย importance, sentiment (บวก/ลบ/กลาง), relevance (≥ 0.5 หรือไม่)
- `python -m src.news.eval`: importance ต่างไม่เกิน ±1, ทิศ sentiment ตรง, relevance ตรง, `headline_th` ผ่านตัวตรวจ · รายงานเป็น % และรายการที่ผิด
- ไม่รันใน CI · ✅ เกณฑ์ผ่านก่อนไป D4: importance ±1 ≥ 85%, sentiment ≥ 80%

### D4 — บทสรุปรายหุ้น (prompt `d1`)
- `src/news/digest.py`: เลือกข่าวตาม D-Q7 · `Digest` (Pydantic) ตาม D-Q8 · ตรวจ `news_ids` ⊆ ข่าวที่ส่ง
- ตัวตั้งเวลาสร้าง `digest_jobs` ตาม `DIGEST_TIMES_ET` · worker ทำต่อจากงานให้คะแนน (D-Q10) · retry ตาม D-Q9
- `python -m src.news.digest --symbol AAPL` สั่งทำทันที (ไว้ทดสอบ/ปรับ prompt)
- ✅ test: ไม่มีข่าวใหม่ → ไม่สร้างงาน · อ้าง id ที่ไม่มี → output เสีย · ข่าวช่วงเสาร์-อาทิตย์เข้ารอบเช้าวันจันทร์ · ตัดที่ 15 ข่าว

### D5 — API
- `GET /news` เพิ่ม `headline_th`, `skip_reason` · ข่าว `skipped` ไม่ขึ้นโดยค่าเริ่มต้น (`include_skipped=true` ถึงจะขึ้น)
- `GET /digest?symbol=AAPL&limit=1` บทสรุปล่าสุด (มีข่าวที่อ้างแนบมา: id, headline, url) · `GET /digest?scope=watchlist`
- `/health.llm` เพิ่มจำนวน call แยก triage/digest และ `digest_jobs` ที่ค้าง
- ✅ ตัวกรองถูก · บทสรุปที่อ้างข่าวคืนข่าวครบ

### D6 — แผงข่าวแบบใหม่ (UI)
- บนสุด: การ์ดบทสรุป (title, จุดสี tone, bullet แต่ละข้อมีป้ายอ้างข่าว กดแล้วเลื่อนไปข่าวนั้น, "AI digest · <model> · <เวลารอบ>")
- กลาง: **ข่าวสำคัญ** (importance ≥ 3) เป็นบรรทัดสั้น: `headline_th` (หรืออังกฤษ) · ป้ายคะแนน · จุด sentiment · % ราคาหลังข่าว · ลิงก์ต้นฉบับ
- ล่าง: "ข่าวอื่น (N)" พับไว้ รวมข่าวคะแนนต่ำ, ข่าวที่เกี่ยวข้องน้อย และข่าวที่ยังไม่ได้ให้คะแนน · ไม่แสดง `skipped`
- ✅ `node --test` สำหรับการจัดกลุ่ม/เรียง · ภาพหน้าจอ desktop + มือถือ

### D7 — แจ้งเตือน (เดิม N8)
- toast + กระดิ่ง: ข่าว importance ≥ 4 ใหม่ ที่ไม่ใช่ backfill · ข้อความ = `headline_th` (หรืออังกฤษ) · `lastSeenNewsId` ใน localStorage · ไม่ขึ้นย้อนหลังตอนเปิดหน้าแรก
- backend: interface `Notifier` (ยังไม่ทำอะไร) ไว้ต่อ Telegram

### D8 — หมุดบนกราฟ (เดิม N9)
- ตาม Q16 ของ 3c · ใช้คะแนนจาก D2 · tooltip แสดง `headline_th`

### D9 — บทสรุปทั้งพอร์ต (ทำเมื่อหุ้น > 10 ตัว)
- 1 call ต่อรอบ: รับบทสรุปรายหุ้นของรอบนั้น + ข่าว importance ≥ 4 → เลือก ≤ 5 เรื่องเด่นทั้งพอร์ต อ้าง symbol + news_ids
- UI: การ์ดบนหน้าแรกเหนือกราฟ กดแล้วสลับไปหุ้นนั้น

### D10 — Docs + config (เดิม N10)
- runbook: env ใหม่ทั้งหมด, `reprocess --status skipped`, `digest --symbol`, eval, อ่าน `/health.llm`
- `.env.example` / `deploy/env.vm.example` / `docker-compose.prod.yml`: `LLM_TRIAGE_BATCH`, `LLM_TRIAGE_WAIT_SEC`, `NEWS_MAX_TAGS`, `DIGEST_TIMES_ET`, `LLM_DIGEST_RESERVE`, `GEMINI_API_KEY` ฯลฯ

## ความเสี่ยง
| เรื่อง | การรับมือ |
|---|---|
| Gemma ตอบชุด 25 ข่าวไม่ไหว (เคยหลุดตอนชุดหลายข่าวใน 3c — แต่ตอนนั้น output เป็นสรุปไทยยาว) | output ชุดนี้สั้นมาก · แบ่งครึ่งเมื่อเสีย · ปรับ `LLM_TRIAGE_BATCH` ได้ · วัดจริงใน D2 ก่อนไปต่อ |
| บทสรุปแต่งเรื่อง/สรุปเกินข่าว | ทุกข้อต้องอ้าง id · prompt ห้ามใช้ข้อมูลนอกข่าว · ผู้ใช้ตรวจจาก checklist ตอนปรับ prompt |
| ภาษาไทยของ Gemma ยังแปลก | ข้อความยาวลดลงมาก (ไม่มีสรุปต่อข่าวแล้ว) · ตัวตรวจข้อความเสียใช้กับทุกช่อง · เปลี่ยนโมเดลได้ด้วย env |
| กฎกรองตัดข่าวสำคัญทิ้ง | ข่าว `skipped` ยังเก็บและดูได้ (`include_skipped`) · กฎมี test ทั้งสองฝั่ง · ส่งกลับเข้าคิวได้ |
| แท็กหุ้นของ Alpaca ผิด | `relevance` จาก AI ยังกรองข่าวที่เกี่ยวข้องน้อยออก (≥ 0.5) |

## นอกขอบเขต
- Telegram/LINE (มีแค่ interface) · ดึงเนื้อข่าวเต็มจากเว็บต้นทาง · บทวิเคราะห์เชิงคำแนะนำซื้อขาย (บทสรุปบอกแค่ "เกิดอะไรขึ้น" ไม่แนะนำให้ซื้อ/ขาย)
