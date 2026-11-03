# Runbook — Lite stack บน Oracle VM

คู่มือ deploy และดูแลระบบ production ของ lite stack
(สถาปัตยกรรมและเหตุผลการตัดสินใจอยู่ใน [`docs/specs/phase-2-deploy.md`](specs/phase-2-deploy.md))

| รายการ | ค่า |
|---|---|
| VM | Oracle Always Free x86_64, user `ubuntu`, IP `140.245.108.27` |
| เว็บเรซูเม่ | https://fuiji-stock.duckdns.org (`~/docker/lab`) |
| API | https://api.fuiji-stock.duckdns.org (`~/docker/stock-streaming`) |
| Dashboard | Vercel (root directory `frontend/`) |
| Images | `ghcr.io/fuiji/stock-streaming-{api,ingestion}:sha-xxxxxxx` |

คำสั่ง compose บน VM ทั้งหมดในเอกสารนี้รันใน `~/docker/stock-streaming` และใช้ alias นี้:

```bash
alias dc='docker compose -f docker-compose.prod.yml -f deploy/compose.vm.yml'
```

---

## 1. ตั้งค่าครั้งแรก

ทำตามลำดับ แต่ละขั้นมีวิธีตรวจผลอยู่ท้ายขั้น

### 1.1 เตรียม VM

```bash
git clone https://github.com/FUIJI/stock-streaming.git ~/src/stock-streaming
cd ~/src/stock-streaming
bash deploy/bootstrap.sh
```

✅ `free -h` แสดง Swap 2.0Gi และ `docker network ls` มี `web`

### 1.2 Caddy: เปิด HTTPS และ route ไป API

**ผลกระทบ:** เว็บเรซูเม่จะดับไม่กี่วินาทีระหว่าง recreate container ของ Caddy

```bash
cd ~/docker/lab
cp Caddyfile Caddyfile.bak && cp docker-compose.yml docker-compose.yml.bak
cp ~/src/stock-streaming/deploy/caddy/Caddyfile ~/src/stock-streaming/deploy/caddy/docker-compose.yml .
echo "ACME_EMAIL=<อีเมลของคุณ>" > .env    # Let's Encrypt ใช้แจ้งเตือนเรื่องใบรับรอง
docker compose up -d
docker logs caddy-proxy --tail 30             # ควรเห็น "certificate obtained successfully"
```

✅ ตรวจผล:
- `curl -I https://fuiji-stock.duckdns.org` ได้ 200
- `curl -I http://fuiji-stock.duckdns.org` ถูก redirect ไป HTTPS
- `https://api.fuiji-stock.duckdns.org` จะได้ 502 จนกว่าจะ deploy API ในขั้น 1.6 ซึ่งเป็นเรื่องปกติ

**Rollback:** `mv Caddyfile.bak Caddyfile && mv docker-compose.yml.bak docker-compose.yml && docker compose up -d`

> พอร์ต 443/UDP (HTTP/3) ไม่จำเป็นต้องเปิดใน Security List ถ้าไม่เปิด browser จะใช้ HTTP/2 ผ่าน TCP แทน

### 1.3 ไฟล์ `.env` ของแอป (secret อยู่บน VM เท่านั้น)

```bash
cp ~/src/stock-streaming/deploy/env.vm.example ~/docker/stock-streaming/.env
chmod 600 ~/docker/stock-streaming/.env
openssl rand -hex 32   # -> INGEST_TOKEN
openssl rand -hex 24   # -> POSTGRES_PASSWORD
nano ~/docker/stock-streaming/.env   # แทนค่า change-me ทั้งหมด (CORS_ORIGINS ใส่หลังขั้น 1.8 ได้)
```

⚠️ `POSTGRES_PASSWORD` มีผลแค่ตอนสร้าง volume ครั้งแรก ถ้าเปลี่ยนภายหลังต้องเปลี่ยนในฐานข้อมูลด้วย (ดูหัวข้อ 2)

### 1.4 SSH key สำหรับ GitHub Actions

บน VM:

```bash
ssh-keygen -t ed25519 -f ~/gh_deploy -N "" -C "github-actions-deploy"
cat ~/gh_deploy.pub >> ~/.ssh/authorized_keys
cat ~/gh_deploy          # copy ทั้งหมด (รวมบรรทัด BEGIN/END) ไปใส่ secret VM_SSH_KEY
rm ~/gh_deploy           # ลบ private key ออกจาก VM หลัง copy แล้ว
```

Host key ของ VM สำหรับ secret `VM_KNOWN_HOSTS` (อ่านจาก VM เองโดยตรง จึงเชื่อถือได้ที่สุด):

```bash
for f in /etc/ssh/ssh_host_*_key.pub; do echo "140.245.108.27,fuiji-stock.duckdns.org $(cut -d' ' -f1,2 "$f")"; done
```

copy ผลลัพธ์ทุกบรรทัดไปใส่ `VM_KNOWN_HOSTS` แต่ละบรรทัดใช้ได้ทั้งกับ IP และโดเมน
ค่า `VM_HOST` ต้องเป็นหนึ่งในสองชื่อนี้ (ถ้าไม่ตรง deploy จะ fail ด้วย `Host key verification failed`)

### 1.5 GitHub repository settings

1. **Settings → Environments → New environment** ตั้งชื่อ `production`
2. ใน environment `production` เพิ่ม **Environment secrets**:

| Secret | ค่า |
|---|---|
| `VM_HOST` | `140.245.108.27` |
| `VM_USER` | `ubuntu` |
| `VM_SSH_KEY` | private key จากขั้น 1.4 |
| `VM_KNOWN_HOSTS` | ผลลัพธ์จากคำสั่งในขั้น 1.4 (ไม่ใส่ก็ได้ ระบบจะ scan เองตอน deploy แต่ปลอดภัยน้อยกว่า) |

### 1.6 Deploy ครั้งแรก

1. Merge PR เข้า `master` → workflow **CI** รัน → ถ้าผ่าน **Deploy** จะรันต่ออัตโนมัติ
2. **ครั้งแรก job `deploy` จะ fail ตอน pull image** เพราะ GHCR สร้าง package เป็น private เป็นค่าเริ่มต้น (decision D2: เปลี่ยนเป็น public):
   - GitHub → รูปโปรไฟล์ → **Your profile → Packages** → `stock-streaming-api` → **Package settings** → **Change visibility → Public**
   - ทำแบบเดียวกันกับ `stock-streaming-ingestion`
   - กลับไปที่ run ของ Deploy ที่ fail แล้วกด **Re-run failed jobs**

✅ job `Health check` ผ่าน และ `curl https://api.fuiji-stock.duckdns.org/health` ได้ `{"status":"ok","version":"sha-…"}`

### 1.7 ดึงข้อมูลย้อนหลัง (ทำครั้งเดียว)

```bash
cd ~/docker/stock-streaming
dc run --rm api python -m src.backfill --days 5
```

(ดึงแท่ง 1 นาทีย้อนหลัง 5 วัน และแท่งรายวันย้อนหลัง 5 ปีด้วย `--daily-days 1825` ซึ่งเป็นค่าเริ่มต้น)

✅ `curl "https://api.fuiji-stock.duckdns.org/candles?symbol=AAPL&limit=3"` ได้แท่งเทียนกลับมา

### 1.8 Frontend บน Vercel

1. https://vercel.com/new → Import repo `FUIJI/stock-streaming`
2. **Root Directory:** `frontend` (Vercel ตรวจเจอว่าเป็น Next.js เอง)
3. **Environment Variables:** `NEXT_PUBLIC_API_URL` = `https://api.fuiji-stock.duckdns.org`
   (ไม่ต้องตั้ง WS URL เพราะ `useTradeStream.ts` แปลง `https` เป็น `wss` ให้อยู่แล้ว)
4. Deploy แล้วจด URL ไว้ เช่น `https://stock-streaming-xxx.vercel.app`
5. บน VM เพิ่ม URL นั้นใน `CORS_ORIGINS` ของ `.env` แล้วรัน `dc up -d api`
   - ลิงก์ preview ของ Vercel (เช่น `stock-streaming-xxxx-fuiji.vercel.app`) เปลี่ยนทุกครั้งที่ deploy และจะโดน CORS บล็อก
     ถ้าต้องการให้ใช้ได้ ให้ตั้ง `CORS_ORIGIN_REGEX=https://stock-streaming-[a-z0-9]+-fuiji\.vercel\.app` เพิ่ม
   - อาการ CORS: REST ทุกตัว (กราฟ ราคา สถานะตลาด) โหลดไม่ได้ แต่ป้าย Live ยังเขียวอยู่ เพราะ WebSocket ไม่ถูกตรวจ CORS

✅ เปิด URL ของ Vercel แล้วกราฟโหลดได้ DevTools ไม่มี error เรื่อง CORS หรือ mixed content
และระหว่างตลาดเปิด (20:30–03:00 น. หรือ 21:30–04:00 น. ตามเวลาไทย ขึ้นกับ daylight saving ของสหรัฐฯ) แท่งล่าสุดจะขยับเอง

> `NEXT_PUBLIC_*` ถูกฝังตอน build ถ้าเปลี่ยนค่าต้อง Redeploy บน Vercel

### 1.9 แจ้งเตือนเมื่อข้อมูลหยุดไหล (UptimeRobot ฟรี)

1. https://uptimerobot.com → สมัคร → **Add New Monitor**
2. Monitor Type **HTTP(s)**, URL `https://api.fuiji-stock.duckdns.org/health/feed`, interval 5 นาที
3. ใส่อีเมลหรือ LINE/Telegram ที่ต้องการให้แจ้งเตือน

`/health/feed` ตอบ **503** เมื่อไม่มี ingestion เชื่อมต่ออยู่ หรือไม่มี trade เข้ามาเกิน 10 นาทีระหว่างตลาดเปิดช่วง regular
(ช่วง pre/post ไม่นับ เพราะ IEX เงียบนานได้เป็นปกติ) ดูรายละเอียดได้จาก body ของ response

---

## 2. งานประจำ

| งาน | วิธี |
|---|---|
| Deploy | merge เข้า `master` (อัตโนมัติ) หรือ Actions → **Deploy** → Run workflow โดยเว้น `image_tag` ว่าง |
| Rollback | Actions → **Deploy** → Run workflow → `image_tag` = tag เก่า เช่น `sha-1a2b3c4` (ดูได้จากหน้า Packages หรือ log ของ run ก่อนหน้า) |
| ดู log | `dc logs -f --tail=100 api ingestion` |
| ดูสถานะ / RAM | `dc ps` · `docker stats --no-stream` · `free -h` |
| Restart | `dc restart api ingestion` |
| Backfill เพิ่ม | `dc run --rm api python -m src.backfill --days 10` (ค่าเริ่มต้นใช้ SIP ถ้าแผนไม่อนุญาตจะใช้ IEX แทนอัตโนมัติ) |
| Reconcile เอง | `dc run --rm api python -m src.jobs reconcile --days 2` (ปกติ API รันให้เองทุกคืนเวลา `RECONCILE_AT_UTC` และหลัง start) |
| เพิ่มหรือลดหุ้น | แก้ `SYMBOLS` ใน `.env` แล้วรัน `dc up -d ingestion` (Alpaca free รับได้ ~30 ตัว) |
| เข้า psql | `dc exec postgres psql -U stock -d stocks` |
| หมุน `INGEST_TOKEN` | แก้ `.env` แล้วรัน `dc up -d api ingestion` (ทั้งสองตัวต้องใช้ค่าเดียวกัน) |
| หมุน Alpaca key | Generate key ใหม่บน Alpaca → แก้ `.env` → `dc up -d ingestion` → ลบ key เก่าบน Alpaca |
| เปลี่ยนรหัส Postgres | `dc exec postgres psql -U stock -d stocks -c "ALTER USER stock PASSWORD '<ใหม่>'"` → แก้ `.env` → `dc up -d` |
| Backup ฐานข้อมูล | `dc exec -T postgres pg_dump -U stock stocks \| gzip > ~/stocks-$(date +%F).sql.gz` |

---

### ข่าว + LLM (Phase 3c)

ใน `curl -s https://api.fuiji-stock.duckdns.org/health`:
- `news.last_poll_at` = ดึงข่าวจาก Alpaca สำเร็จล่าสุด (ทุก 60 วินาที)
- `llm.pending / done / failed` = คิวสรุปข่าว · `llm.calls_today / daily_limit` = โควตาของเราเอง
- `llm.last_error` = error ล่าสุดของ LLM · `llm.paused_until` = worker หยุดรอ (โควตาหมด หรือ key ผิด)

| งาน | วิธี |
|---|---|
| ลอง LLM กับข่าวล่าสุด (ไม่เขียนฐานข้อมูล) | `dc run --rm api python -m src.news.smoke --limit 25` (1 ชุดเท่าที่ worker ส่งจริง · บรรทัดสุดท้ายบอกจำนวน call, การแบ่งครึ่ง, token, เวลา) |
| Gemma ตอบชุดใหญ่ไม่ไหว (`batch splits` บ่อย) | ลด `LLM_TRIAGE_BATCH` ใน `.env` (เช่น 10) แล้ว `dc up -d api` |
| เปลี่ยนโมเดลหลัก/สำรอง | แก้ `LLM_MODEL` / `LLM_FALLBACK_MODEL` ใน `.env` แล้ว `dc up -d api` |
| สรุปข่าวใหม่หลังแก้ prompt | `dc run --rm api python -m src.news.reprocess --since 2026-11-01 --older-prompt t1` (ใส่เวอร์ชันใหม่ ข่าวที่สรุปด้วยเวอร์ชันนั้นแล้วจะไม่ถูกส่งซ้ำ) |
| ส่งข่าวที่ failed ไปลองใหม่ | `dc run --rm api python -m src.news.reprocess --since 2026-11-01 --status failed` |
| สรุปใหม่เฉพาะข่าวที่ผลเสีย (เพิ่มตัวตรวจใหม่แล้ว) | `dc run --rm api python -m src.news.reprocess --since 2026-11-01 --recheck` (ตรวจสรุปที่เก็บไว้ด้วยกฎปัจจุบัน ส่งเฉพาะข่าวที่ไม่ผ่าน) |
| ข่าวที่ถูกกฎข้าม (`skipped`) | ดูได้ที่ `/news?include_skipped=true` (มี `skip_reason`) · แก้กฎใน `api/src/news/rules.py` หรือ `NEWS_MAX_TAGS` แล้ว `dc run --rm api python -m src.news.reprocess --since 2026-11-01 --rerun-rules` |
| วัดคุณภาพการให้คะแนน (หลังแก้ prompt/เปลี่ยนโมเดล) | ดู `api/tests/news_eval/README.md` · `dc run --rm -T api python -m src.news.eval --runs 2 < api/tests/news_eval/cases.json` |
| ปิดการสรุปข่าวชั่วคราว | `LLM_PROVIDER=off` ใน `.env` แล้ว `dc up -d api` (ข่าวยังเข้ามาและแสดงเป็นภาษาอังกฤษ) |

---

## 3. เหตุฉุกเฉิน

### Deploy fail
- **`Pull images and restart` fail ด้วย `unauthorized`** → package บน GHCR ยังเป็น private ให้ทำตามขั้น 1.6
- **`Pull images and restart` fail ด้วย `Missing .env`** → ยังไม่ได้ทำขั้น 1.3
- **`Health check` fail** → ดู `dc logs --tail=100 api` บน VM ถ้าแก้ไม่ทัน ให้ rollback ไป tag ก่อนหน้า
- **`Configure SSH` fail ด้วย `No host key for VM_HOST`** หรือ **`Host key verification failed`** → สร้าง `VM_KNOWN_HOSTS` ใหม่ตามขั้น 1.4 และเช็กว่า `VM_HOST` ไม่มีช่องว่างหรือขึ้นบรรทัดใหม่ต่อท้าย
- **`Configure SSH` fail ด้วย `Permission denied (publickey)`** → `VM_SSH_KEY` ไม่ตรงกับ public key ใน `~/.ssh/authorized_keys` บน VM
- **`Configure SSH` fail ด้วย timeout** → ตรวจว่า Security List ของ Oracle ยังเปิดพอร์ต 22 ให้เข้าจากภายนอก

### ingestion ขึ้น error 406 (`connection limit exceeded`)
Alpaca free เปิด WebSocket ได้ทีละ 1 connection ตรวจว่าไม่มี ingestion ตัวอื่นที่ใช้ key เดียวกัน
(เช่น `make lite-up` บนเครื่องตัวเองที่ตั้ง `TRADE_SOURCE=alpaca` ไว้) แล้วรัน `dc restart ingestion`

### IP ของ VM เปลี่ยน
เกิดเมื่อ stop แล้ว start instance ที่ใช้ ephemeral IP
1. DuckDNS → ใส่ IP ใหม่ → **update ip**
2. GitHub → secret `VM_HOST` และ `VM_KNOWN_HOSTS` (สร้างใหม่ตามขั้น 1.4 ด้วย IP ใหม่)
3. ป้องกันในอนาคต: Oracle Console → instance → VNIC → เปลี่ยนเป็น **Reserved Public IP**

### Oracle เรียกคืน instance หรือต้องย้ายไป VM ใหม่
1. สร้าง instance ใหม่ ติดตั้ง Docker และเปิดพอร์ต 80/443 (Security List + iptables)
2. ทำหัวข้อ 1.1 ถึง 1.5 ใหม่ (และย้ายไฟล์เว็บเรซูเม่ไปไว้ใน `~/docker/lab/resume`)
3. อัปเดต DuckDNS และ secret `VM_HOST`/`VM_KNOWN_HOSTS`
4. Actions → **Deploy** → Run workflow โดยใส่ `image_tag` ล่าสุด แล้วทำขั้น 1.7 (backfill)
5. ถ้ามี backup ฐานข้อมูล: `gunzip -c stocks-YYYY-MM-DD.sql.gz | dc exec -T postgres psql -U stock -d stocks`

---

## 4. ข้อจำกัดที่รู้อยู่

- **Deploy key มีสิทธิ์เท่า user `ubuntu`:** user นี้อยู่ในกลุ่ม `docker` (เทียบเท่า root) และใช้ `sudo` ได้ ใครได้ `VM_SSH_KEY` ไปจะควบคุม VM ได้ทั้งหมด
  ถ้าต้องการจำกัดสิทธิ์ ให้สร้าง user แยกสำหรับ deploy และจำกัดคำสั่งใน `authorized_keys` (`command=`) ซึ่งยังไม่ได้ทำใน Phase 2
- **API สาธารณะยังไม่มี auth และ rate limit:** ใครก็เปิด `/ws/{symbol}` หรือเรียก `/candles` ได้ไม่จำกัด (มีแผนไว้ใน Phase 3)
- **ข้อมูลระหว่าง API ล่มเกิน ~2 นาทีจะหาย:** เติมคืนได้ด้วย backfill
- **ราคามาจาก IEX feed:** volume ต่ำกว่าข้อมูลรวมทั้งตลาด (consolidated tape) มาก แต่ราคาใกล้เคียงกัน
