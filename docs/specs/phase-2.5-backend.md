# Phase 2.5 — Backend & data hardening (ก่อนทำ UI)

> สถานะ: **Approved — กำลังทำ** · Base: `master` (`bbfd135`) · Branch: `feat/phase-2.5-backend`

## เป้าหมาย

ทำให้ข้อมูลถูกต้อง ไม่หาย และมี API ครบสำหรับงาน UI รอบถัดไป (ป้าย Live/ตลาดปิด, การ์ดราคา,
กราฟรายวัน, ชื่อบริษัท) พร้อมป้องกันขั้นพื้นฐานก่อนแชร์ลิงก์ให้คนอื่นใช้

## Decisions

| # | เรื่อง | ตัดสินใจ | เหตุผล |
|---|---|---|---|
| E1 | ช่วงเวลาซื้อขาย | **รวม pre-market และ after-hours** (04:00–20:00 ET) | ผู้ใช้ต้องการ · API รายงาน `session` = `pre` / `regular` / `post` / `closed` |
| E2 | แหล่งข้อมูลย้อนหลัง | backfill และ reconcile ใช้ **SIP** (`ALPACA_HISTORY_FEED=sip`) ส่วนข้อมูลสดยังใช้ IEX | แผนฟรีของ Alpaca ให้ดึง SIP ย้อนหลังได้ ยกเว้น 15 นาทีล่าสุด SIP ครอบคลุมทุกตลาดและทุกช่วงเวลาครบกว่า IEX มาก · ถ้า SIP ถูกปฏิเสธจะ fallback ไป IEX อัตโนมัติ |
| E3 | ตาราง schema ใหม่ | **migration ที่ API รันเองตอน start** (`api/src/migrations/*.sql` + ตาราง `schema_migrations`) | `001_schema.sql` รันแค่ตอนสร้าง volume ครั้งแรก ฐานข้อมูลบน VM ที่มีอยู่แล้วจะไม่ได้ตารางใหม่ |
| E4 | ตัวตั้งเวลา reconcile | **background task ใน API** (`RECONCILE_AT_UTC`) | ไม่ต้องเพิ่ม cron หรือ container · log อยู่ที่เดียวกับ API |
| E5 | Backup (A2 เดิม) | **เลื่อนไป Phase 3** | ข้อมูลตอนนี้ทั้งหมดเป็นข้อมูลตลาดจาก Alpaca กู้คืนได้ 100% ด้วย backfill · backup จะจำเป็นเมื่อมีข้อมูลผู้ใช้ (watchlist) ใน Phase 3 |

**ข้อจำกัดที่ยอมรับ**
- ข้อมูลสดมาจาก IEX ซึ่งมี volume เพียงไม่กี่ % ของทั้งตลาด และมี trade น้อยมากในช่วง pre/post
  แท่งของวันนี้จึงมี volume ต่ำและบางนาทีไม่มีแท่ง จนกว่า reconcile ตอนกลางคืนจะเขียนทับด้วยข้อมูล SIP
- แท่งรายวัน (`1d`) ของวันนี้คำนวณจากแท่ง 1 นาที (IEX) ส่วนวันก่อนหน้ามาจาก Alpaca (SIP)

---

## Tickets

ลำดับ: **M0 → A3 → B1 → A1 → B2 → B4 → B3 → C1 → C2**

### M0 — Schema migrations ที่ API รันเอง
- `api/src/migrations/NNNN_name.sql` รันตามลำดับชื่อไฟล์ แต่ละไฟล์รันใน transaction และบันทึกใน `schema_migrations`
- ใช้ `pg_advisory_lock` กันไม่ให้ API กับ job รัน migration พร้อมกัน
- รันตอน API start และก่อนเริ่ม job ทุกตัว
- ✅ รันซ้ำแล้วไม่มีอะไรเปลี่ยน · ใช้ได้ทั้งกับ Postgres ธรรมดาและ TimescaleDB · ฐานข้อมูลบน VM ที่มีอยู่แล้วได้ตารางใหม่โดยไม่ต้องทำอะไรเพิ่ม

### A3 — Health ที่บอกความสดของข้อมูล + uptime monitor
- `/health` ตอบ 200 เสมอ (ใช้ตรวจว่า process ยังรันอยู่ และ deploy ใช้ตรวจ `version`) และเพิ่มส่วน `feed`: จำนวน connection ของ ingestion, เวลา trade ล่าสุด, จำนวน trade ทั้งหมด
- `/health/feed` ตอบ **503** เมื่อไม่มี ingestion เชื่อมต่ออยู่ หรือเมื่ออยู่ในช่วง `regular` แต่ไม่มี trade เข้ามาเกิน `FEED_STALE_SEC` (ค่าเริ่มต้น 600)
  (ช่วง pre/post ไม่เช็ก เพราะ IEX อาจเงียบนานเป็นปกติ)
- runbook: ตั้ง UptimeRobot (ฟรี) ให้เช็ก `/health/feed` ทุก 5 นาที
- ✅ ปิด ingestion แล้ว `/health/feed` ได้ 503 ภายในไม่กี่วินาที · เปิดกลับมาแล้วได้ 200

### B1 — `GET /market/status`
- ดึง calendar จาก Alpaca Trading API (`/v2/calendar`) แล้ว cache ไว้ 6 ชั่วโมง คำนวณ session ตามเวลาปัจจุบันใน `America/New_York`
- ตอบ `session`, `next_session`, `next_change_at` (UTC) และเวลาเปิดปิดของวันนี้ (`pre_open`, `open`, `close`, `post_close`)
- ✅ ถูกต้องทุกกรณี: วันหยุด, เสาร์อาทิตย์, วันที่ตลาดปิดเร็ว (early close), และช่วงเปลี่ยนเวลา daylight saving (ทดสอบด้วยวันที่ตายตัว)

### A1 — Reconcile + เติมช่องว่างอัตโนมัติ
- แยกโค้ดเรียก Alpaca ออกเป็น `api/src/alpaca.py` (bars, snapshots, calendar, assets) ให้ backfill และ API ใช้ร่วมกัน
- `python -m src.jobs reconcile --days N`: sync ข้อมูล symbols → แท่ง 1m ย้อนหลัง N วัน → แท่ง 1d ย้อนหลัง N วัน (feed ตาม E2)
- API รัน reconcile อัตโนมัติ:
  - ทุกวันเวลา `RECONCILE_AT_UTC` (ค่าเริ่มต้น `01:30` ซึ่งอยู่หลัง after-hours ปิดทั้งช่วง EDT และ EST)
  - หนึ่งครั้งหลัง API start (`--days 1`) เพื่อเติมช่วงที่ API หรือ VM ล่ม
- SIP ดึงได้ถึงแค่ 16 นาทีก่อนปัจจุบัน · ถ้าได้ 403 จะลองใหม่ด้วย IEX
- ✅ ลบแท่งช่วงหนึ่งทิ้งแล้วรัน reconcile ข้อมูลช่วงนั้นกลับมา · ข้อมูลที่ backfill เขียนไม่ทับแท่งที่ aggregator กำลังสร้าง

### B2 — `GET /quotes?symbols=...`
- จาก Alpaca snapshots (cache 15 วินาที) แล้วใช้ราคา trade สดล่าสุดจาก ingestion แทนถ้าใหม่กว่า
- `prev_close` = ราคาปิดของวันทำการก่อนหน้า (ช่วง pre-market ต้องเทียบกับราคาปิดของเมื่อวาน)
- ตอบเฉพาะหุ้นที่ระบบติดตาม (ป้องกันไม่ให้ใช้โควตา Alpaca หมด)
- ✅ คำนวณ change/% ถูกต้องทั้งช่วง pre, regular และ post (ทดสอบด้วย snapshot ตัวอย่าง)

### B4 — ตาราง `symbols`
- migration: `symbols(symbol PK, name, exchange, active, updated_at)`
- API start: เพิ่มหุ้นจาก `SYMBOLS` ลงตาราง · reconcile: เติมชื่อบริษัทและตลาดจาก Assets API
- `/symbols` อ่านจากตาราง (คืนค่าเป็น `string[]` เหมือนเดิม) และเพิ่ม `/symbols/details`
- ✅ `/symbols` ไม่ต้องอ่านตาราง `ohlcv_1m` อีกต่อไป

### B3 — interval `1d`
- migration: ตาราง `ohlcv_1d`
- `python -m src.backfill --daily-days 1825` ดึงแท่งรายวันย้อนหลัง 5 ปี
- `/candles?interval=1d`: วันก่อนหน้ามาจาก `ohlcv_1d` ส่วนวันนี้คำนวณจาก `ohlcv_1m` (ตัดวันตามเวลา ET)
- ✅ แท่งของวันนี้แสดงทันทีโดยไม่ต้องรอ reconcile ตอนกลางคืน

### C1 — จำกัด connection และ rate limit
- WebSocket: จำกัดจำนวนรวม (`WS_MAX_CONNECTIONS`, ค่าเริ่มต้น 200) และต่อ IP (`WS_MAX_PER_IP`, ค่าเริ่มต้น 10) · `/ws/{symbol}` รับเฉพาะหุ้นที่ระบบติดตาม
- REST: จำกัดต่อ IP แบบ token bucket (`RATE_LIMIT_PER_MIN`, ค่าเริ่มต้น 120) ถ้าเกินตอบ 429 (ยกเว้น `/health*`)
- อ่าน IP จริงของ client จาก header ที่ Caddy ส่งมา (`FORWARDED_ALLOW_IPS=*` บน VM ซึ่งปลอดภัยเพราะ API ไม่ได้เปิดพอร์ตสู่ภายนอก)
- ✅ IP เดียวเปิด WS ครั้งที่ 11 ถูกปฏิเสธ · ยิง REST เกินโควตาได้ 429 · symbol ที่ไม่รู้จักถูกปฏิเสธ

### C2 — จำกัดปริมาณข้อมูลที่ query อ่าน
- การคำนวณแท่ง 5m/1h อ่านแท่ง 1m ล่าสุดไม่เกิน `(limit + 1) × นาทีต่อแท่ง` แถว (ใช้ index ของ primary key) แล้วตัดแท่งแรกที่อาจไม่ครบทิ้ง
- จำกัด `limit` ของการคำนวณแท่งที่ 1000
- ✅ จำนวนแถวที่อ่านขึ้นกับ `limit` ไม่ขึ้นกับขนาดประวัติทั้งหมด

---

## นอกขอบเขต
- Watchlist และการ subscribe หุ้นแบบ dynamic, Supabase + Auth, backup (Phase 3)
- งาน frontend ทั้งหมด (รอบถัดไป)
