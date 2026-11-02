# Phase 2 — Deploy lite stack ขึ้น production

> สถานะ: **Approved — กำลังทำ** · Base branch: `feat/lite-mode` (ยังไม่ merge)

## เป้าหมาย

เปิด dashboard ราคาหุ้น real-time ให้คนภายนอกเข้าได้ผ่าน HTTPS โดย **ไม่มีค่าใช้จ่าย**
และ deploy ใหม่ได้อัตโนมัติเมื่อ push ขึ้น `master`

**Definition of Done ของทั้ง phase**
- เปิด dashboard บน Vercel แล้วเห็นกราฟแท่งเทียนที่อัปเดตสดจาก Alpaca ระหว่างเวลาตลาดเปิด
- `https://fuiji-stock.duckdns.org` ยังแสดงเว็บเรซูเม่ตามเดิม
- push ขึ้น `master` แล้วระบบบน VM อัปเดตเองภายในไม่กี่นาที โดยไม่ต้อง SSH เข้าไปทำมือ

## สภาพแวดล้อมที่มีอยู่ (ข้อเท็จจริง)

| รายการ | ค่า |
|---|---|
| VM | Oracle Always Free, x86_64, 2 vCPU, RAM 956 MiB (ว่าง ~370 MiB), ไม่มี swap, disk ว่าง 40 GB |
| Public IP | `140.245.108.27` |
| โดเมน | `fuiji-stock.duckdns.org` (DuckDNS ชี้ sub-subdomain ทุกชื่อมาที่ IP เดียวกันอัตโนมัติ) |
| ของที่รันอยู่ | `~/docker/lab`: `caddy-proxy` (พอร์ต 80/443) + `resume-site` (nginx) — Caddyfile ใช้ `:80` ยังไม่มี HTTPS |
| Docker | Compose v5.5.0, user `ubuntu` อยู่ในกลุ่ม `docker` |
| ข้อมูลราคา | Alpaca paper account, feed `iex` — ทดสอบ `curl` แล้วได้ข้อมูล |

## สถาปัตยกรรมเป้าหมาย

```
                      ┌──────────────────────── Oracle VM (1 GB) ─────────────────────────┐
Browser ──HTTPS──►    │ caddy-proxy (:443, Let's Encrypt)                                  │
                      │   fuiji-stock.duckdns.org       → resume-site:80                   │
                      │   api.fuiji-stock.duckdns.org   → api:8000   (/internal/* → 404)   │
                      │                                                                    │
                      │ ~/docker/stock-streaming (compose project แยก)                     │
                      │   api ◄──WS /internal/ingest── ingestion ◄── Alpaca WS (IEX)       │
                      │    │                                                               │
                      │    └──► postgres  (ดู Decision D1)                                 │
                      │                                                                    │
                      │ Docker network `web` (external) เชื่อม caddy-proxy ↔ api           │
                      └────────────────────────────────────────────────────────────────────┘
Browser ──HTTPS──► Vercel (Next.js) ──fetch / wss──► api.fuiji-stock.duckdns.org

GitHub push master ─► Actions: test → build image → GHCR ─► SSH เข้า VM → compose pull && up -d
```

## Decisions (ตกลงแล้ว)

| # | เรื่อง | ตัดสินใจ | เหตุผล |
|---|---|---|---|
| D1 | ฐานข้อมูล | **Postgres container บน VM** (~50 MB) | Phase 3 จะย้ายไป Supabase ซึ่งเป็น Postgres เหมือนกัน · Redis และ SQLite ตกไปเพราะ RAM จำกัดและเส้นทางย้ายไป Supabase ยาวกว่า |
| D2 | image บน GHCR | **public** | VM pull ได้โดยไม่ต้อง login, ใน image ไม่มี secret |
| D3 | secret ของแอป | **`.env` บน VM** (สร้างมือครั้งเดียว) | secret ไม่ต้องผ่าน CI · GitHub Secrets เก็บแค่ข้อมูล SSH |
| D4 | frontend | **Vercel** | ไม่กิน RAM ของ VM และได้ CDN ฟรี |
| D5 | log rotation | ตั้งต่อ service ใน compose แทน `daemon.json` | แก้ `daemon.json` ต้อง restart dockerd ซึ่งจะทำให้เว็บเรซูเม่ดับชั่วคราว |
| D6 | TDD | ไม่ใช้ใน Phase นี้ | งานส่วนใหญ่เป็น infra · ตรวจด้วย `shellcheck`, `caddy validate`, `docker compose config` และรันจริงบน VM |

---

## Tickets

ลำดับแนะนำ: T1 → T2 → T3 → T4 → T5 → T6 → T7 (T4 ทำคู่กับ T1–T3 ได้)

### T1 — VM bootstrap script

**ทำไม:** VM ไม่มี swap และ RAM ว่างแค่ ~370 MiB ถ้า RAM เต็มกะทันหัน OOM killer จะฆ่า container
และตอนนี้ยังไม่มี Docker network กลางให้ Caddy คุยกับ API

**ขอบเขต:** `deploy/bootstrap.sh` (รันบน VM ด้วยมือหนึ่งครั้ง)
- สร้าง swapfile 2 GB และตั้งให้ mount อัตโนมัติหลัง reboot (`/etc/fstab`) ตั้ง `vm.swappiness=10`
- สร้าง Docker network `web` ถ้ายังไม่มี
- สร้างโฟลเดอร์ `~/docker/stock-streaming` ถ้ายังไม่มี

**Acceptance criteria**
- [ ] รันซ้ำกี่ครั้งก็ได้ผลเหมือนเดิม (idempotent): ไม่สร้าง swap ซ้ำ ไม่เพิ่มบรรทัดซ้ำใน `/etc/fstab`
- [ ] หลังรันเสร็จ `free -h` แสดง Swap 2.0Gi และยังอยู่หลัง reboot
- [ ] `docker network ls` มี `web`
- [ ] ตลอดการรัน `caddy-proxy` และ `resume-site` ไม่ถูก restart
- [ ] `shellcheck deploy/bootstrap.sh` ผ่าน และไฟล์เป็น LF

### T2 — Caddy: HTTPS + route ไป API

**ทำไม:** หน้าเว็บบน Vercel เป็น HTTPS ทำให้ browser บล็อกการเรียก `http://` และ `ws://` (mixed content)
API จึงต้องมี HTTPS ส่วน `/internal/ingest` ต้องไม่เปิดสู่อินเทอร์เน็ต

**ขอบเขต:** `deploy/caddy/Caddyfile` (เวอร์ชันใหม่ของไฟล์ใน `~/docker/lab`) + วิธีต่อ `caddy-proxy` เข้า network `web`
- `fuiji-stock.duckdns.org` → `resume-site:80`
- `api.fuiji-stock.duckdns.org` → `api:8000` และ `/internal/*` ตอบ 404
- ระบุอีเมลสำหรับ Let's Encrypt (global option `email`)

**Acceptance criteria**
- [ ] `curl -I https://fuiji-stock.duckdns.org` ได้ 200 และใบรับรองถูกต้อง
- [ ] `curl -I http://fuiji-stock.duckdns.org` redirect ไป HTTPS
- [ ] `curl https://api.fuiji-stock.duckdns.org/health` ได้ `{"status":"ok"}`
- [ ] `curl -i https://api.fuiji-stock.duckdns.org/internal/ingest` ได้ 404 (ไม่ใช่ 403 จาก API แปลว่าถูกบล็อกตั้งแต่ Caddy)
- [ ] ต่อ WebSocket `wss://api.fuiji-stock.duckdns.org/ws/AAPL` ได้
- [ ] `caddy validate` ผ่าน
- [ ] มีขั้นตอน rollback ไปใช้ Caddyfile เดิมเขียนไว้ใน runbook (T7)

### T3 — Compose สำหรับ VM

**ทำไม:** `docker-compose.prod.yml` ตอนนี้ build image เองและเปิดพอร์ต 8000 ที่ host
บน VM ต้อง pull image จาก GHCR (VM RAM 1 GB build ไม่ไหว) และให้ Caddy เข้าถึง API ผ่าน network `web` เท่านั้น

**ขอบเขต:**
- เพิ่ม `image: ghcr.io/fuiji/stock-streaming-{api,ingestion}:${IMAGE_TAG:-latest}` ใน `docker-compose.prod.yml` (ยัง build ในเครื่องได้เหมือนเดิม)
- `deploy/compose.vm.yml` (override): ต่อ `api` เข้า network `web`, ไม่ publish พอร์ตของ API ออก host, เปิด Postgres ตาม D1 พร้อม `mem_limit`
- ตั้ง `shared_buffers` และ `max_connections` ของ Postgres ให้เหมาะกับ RAM น้อย
- จำกัดขนาด log ของทุก service (json-file `max-size`/`max-file`) ตาม D5
- `.env.example` ระบุตัวแปรที่ต้องตั้งบน VM (`CORS_ORIGINS`, `TRADE_SOURCE=alpaca`, `INGEST_TOKEN` แบบสุ่ม ฯลฯ)

**Acceptance criteria**
- [ ] `docker compose -f docker-compose.prod.yml -f deploy/compose.vm.yml config` ผ่าน
- [ ] `make lite-up` บนเครื่อง dev ยังทำงานเหมือนเดิม (ไม่ต้องมี network `web`)
- [ ] บน VM: `docker port <api container>` ไม่มีพอร์ตที่เปิดออก host
- [ ] รวม `mem_limit` ของทุก service บน VM ไม่เกิน 512 MB
- [ ] `docker stats --no-stream` หลังรัน 10 นาทีระหว่างตลาดเปิด: ใช้ RAM รวมต่ำกว่า 300 MB

### T4 — CI: lint + test ทุก PR

**ทำไม:** กัน regression ก่อน merge และเป็นด่านก่อน build/deploy

**ขอบเขต:** `.github/workflows/ci.yml` — trigger บน `pull_request` และ `push` ไป `master`
- job แยกสำหรับ `ingestion` และ `api`: `uv sync --frozen` → `ruff check` → `pytest`
- ใช้ uv 0.6.3 ให้ตรงกับ Dockerfile (ดู CLAUDE.md)
- (optional) `docker compose -f docker-compose.prod.yml config` เพื่อ validate compose

**Acceptance criteria**
- [ ] PR ที่ test พังขึ้นสถานะ ❌ และ PR ที่ผ่านขึ้น ✅
- [ ] รันเสร็จภายใน 3 นาที
- [ ] ไม่ต้องใช้ secret ใด ๆ (PR จาก fork ก็รันได้)

### T5 — Build & publish images ไป GHCR

**ทำไม:** VM ต้อง pull image ที่ build เสร็จแล้ว

**ขอบเขต:** `.github/workflows/deploy.yml` (job แรก) — trigger เมื่อ push ไป `master` หลัง CI ผ่าน และ `workflow_dispatch`
- build `api` และ `ingestion` สำหรับ `linux/amd64`
- tag: `sha-<short sha>` และ `latest`
- ใช้ `GITHUB_TOKEN` (`packages: write`) ไม่ต้องสร้าง PAT
- ตั้ง package เป็น public ตาม D2 (ทำครั้งเดียวในหน้า GitHub)

**Acceptance criteria**
- [ ] หลัง merge เข้า `master` มี image ทั้งสองตัวบน `ghcr.io/fuiji/...` พร้อม tag `sha-…` และ `latest`
- [ ] `docker pull ghcr.io/fuiji/stock-streaming-api:latest` บน VM ทำได้โดยไม่ต้อง login
- [ ] image เป็น `linux/amd64`
- [ ] ใช้ build cache (เช่น `type=gha`) ทำให้รอบที่สองเร็วกว่ารอบแรก

### T6 — Deploy ไป VM อัตโนมัติ + health check + rollback

**ทำไม:** push แล้วขึ้น production เองโดยไม่ต้อง SSH ทำมือ

**ขอบเขต:** `deploy.yml` (job ที่สอง ทำต่อจาก T5)
- SSH เข้า VM ด้วย key ที่สร้างไว้ deploy โดยเฉพาะ (GitHub Secrets: `VM_HOST`, `VM_USER`, `VM_SSH_KEY`)
- บน VM: `IMAGE_TAG=sha-… docker compose … pull && up -d`
- health check: ลองเรียก `https://api.fuiji-stock.duckdns.org/health` ซ้ำได้สูงสุด ~60 วินาที ถ้าไม่ผ่าน job ต้อง fail
- `workflow_dispatch` รับ input `image_tag` สำหรับ rollback ไปเวอร์ชันก่อนหน้า
- ใช้ `concurrency` กันไม่ให้ deploy สองรอบทับกัน
- ลบ image เก่าบน VM (`docker image prune`) กัน disk เต็ม

**Acceptance criteria**
- [ ] merge PR → เว็บใช้เวอร์ชันใหม่ภายใน ~5 นาที
- [ ] ถ้า health check ไม่ผ่าน job ขึ้น ❌
- [ ] สั่ง rollback ด้วย `workflow_dispatch` + `image_tag=sha-<เก่า>` แล้ว API กลับไปใช้ image นั้น
- [ ] SSH key ของ deploy ใช้กับ user ที่ไม่มีสิทธิ์ `sudo` หรือจำกัดสิทธิ์ได้ (บันทึกข้อจำกัดไว้ถ้าทำไม่ได้)
- [ ] `resume-site` ไม่ถูกแตะระหว่าง deploy

### T7 — Frontend บน Vercel + runbook

**ทำไม:** ผู้ใช้เข้าผ่านหน้าเว็บนี้ และต้องมีคู่มือสำหรับดูแลระบบในอนาคต

**ขอบเขต:**
- ตั้ง Vercel project: root directory `frontend/`, env `NEXT_PUBLIC_API_URL=https://api.fuiji-stock.duckdns.org`
  (WS URL แปลง `https` → `wss` ให้อยู่แล้วใน `useTradeStream.ts`)
- ตั้ง `CORS_ORIGINS` บน VM ให้ตรงกับโดเมน Vercel
- `docs/runbook.md`: วิธี deploy/rollback, ดู log, หมุน `INGEST_TOKEN`/Alpaca key, backfill, เปลี่ยน IP ใน DuckDNS ถ้า IP ของ VM เปลี่ยน, กู้คืนเมื่อ Oracle เรียกคืน instance
- อัปเดต README: ลิงก์ live demo + roadmap

**Acceptance criteria**
- [ ] เปิด URL ของ Vercel ระหว่างตลาดเปิดแล้วเห็นแท่งเทียนขยับสด
- [ ] DevTools ไม่มี error เรื่อง CORS หรือ mixed content
- [ ] นอกเวลาตลาด หน้าเว็บยังแสดงแท่งเทียนล่าสุด (จาก backfill หรือข้อมูลที่เก็บไว้) ไม่ใช่กราฟว่าง
- [ ] คนที่ไม่เคยเห็นโปรเจคทำตาม runbook แล้ว deploy ใหม่บน VM เปล่าได้

---

## นอกขอบเขต (ไว้ Phase 3+)

- Supabase Auth, watchlist รายผู้ใช้, subscribe symbol แบบ dynamic
- ข่าว + สรุปด้วย LLM + แจ้งเตือน
- Monitoring/alerting เต็มรูปแบบ (Phase นี้ใช้แค่ health check ตอน deploy)
- Rate limit / auth ของ API สาธารณะ (ควรทำก่อนเปิดให้คนอื่นใช้จริงจัง)

## ความเสี่ยง

| ความเสี่ยง | ผลกระทบ | การรับมือ |
|---|---|---|
| Oracle เรียกคืน instance ที่ idle | เว็บล่มทั้งหมด | runbook กู้คืน + bootstrap script, ทางเลือก: อัปเกรดเป็น Pay-As-You-Go + budget alert |
| Alpaca free จำกัด 1 WS connection | ingestion สองตัวพร้อมกัน (เช่น ตอน deploy) ทำให้ตัวใหม่ได้ error 406 | `restart: unless-stopped` + `compose up -d` หยุดตัวเก่าก่อนสร้างตัวใหม่ ตรวจใน T6 |
| Let's Encrypt rate limit บน duckdns.org | ออกใบรับรองไม่ได้ชั่วคราว | Caddy retry อัตโนมัติ; ทดสอบด้วย staging CA ก่อนถ้าจำเป็น |
| RAM เต็ม | container ถูก OOM kill | swap (T1) + `mem_limit` (T3) |
