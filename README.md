# LullabAI Twins — Smart Baby & Breathing Band Monitor

ระบบติดตามการนอนหลับและความปลอดภัยของเด็กทารก (รองรับการขยายเป็นแฝด 2 เตียง) ที่ใช้สถาปัตยกรรม **System 1 (Edge Reflex)** ร่วมกับ **System 2 (Cloud Run + Vertex AI Gemini)** และรองรับการตรวจจับการหายใจผ่าน **แผ่นพันตัวลายเรขาคณิตขาว-ดำ (Nanit-style Breathing Band)** โดยไม่ต้องใช้เซ็นเซอร์ติดแบตเตอรี่บนตัวเด็ก

---

## จุดเด่นของระบบ (Key Features)

1. **Nanit-style Breathing Band Computer Vision (`edge/detector/src/breathing_band.py`)**
   - ตรวจจับกลุ่มลวดลายเรขาคณิตขาว-ดำ (สี่เหลี่ยมจัตุรัสและข้าวหลามตัด) ด้วยตัวกรอง **Center-Surround Difference-of-Boxes (DoB)** ที่ทนต่อแสงน้อยและกล้องอินฟราเรดกลางคืน
   - คำนวณอัตราการหายใจ (**Breathing Rate: RPM / BPM**) แบบไร้สัมผัสด้วยการวัดการขยับระดับ Sub-pixel ของจุดมาร์กเกอร์บนแผ่นพันตัว
   - ตรวจจับการพลิกตัวคว่ำหน้า (**Rollover Detection**) ทันทีเมื่อลายแผ่นพันตัวหายไปจากกรอบเตียง
2. **โหมดทดสอบกับสุนัข (`PET_TEST` Mode)**
   - ตั้งค่า `subject_mode: "PET_TEST"` ใน `edge/detector/config.yaml` เพื่อทดสอบแผ่นพันตัวและการตรวจจับเสียงร้อง/เสียงครางกับน้องหมาก่อนติดตั้งกล้องเตียงเด็กจริง
3. **การแจ้งเตือน 2 จังหวะ (Two-Stage Fast-Path + AI Update)**
   - **Stage 1 (Fast-Path < 1–2 วินาที):** ทันทีที่ Edge ตรวจเจอเสียงร้องหรือแผ่นพันตัวหายไป จะเขียนลง Firestore และยิง FCM Web Push ทันทีพร้อมตัดคลิปสั้นจาก **In-Memory Ring Buffer** (ย้อนหลัง 4 วินาที + หลังเกิดเหตุ 3 วินาที) ส่งขึ้น Cloud Storage
   - **Stage 2 (System 2 Deep Analysis ~5–8 วินาที):** Eventarc ปลุก Cloud Run ให้ส่งคลิปไปวิเคราะห์ด้วย **Vertex AI Gemini** (Safety Check, Posture, Face Covered, Cry Reasoner) แล้วอัปเดตทับ Notification เดิมบนหน้าจอมือถือด้วย `tag: event_id` เดียวกัน
4. **Periodic Snapshot ทุก 5 นาที**
   - ส่งภาพนิ่งความละเอียดสูงไปเช็คความปลอดภัยและอัปเดตสถานะการนอนทุก 5 นาทีแบบเงียบๆ (แจ้งเตือนเฉพาะเมื่อพบความเสี่ยง เช่น พลิกคว่ำหรือผ้าคลุมหน้า)
5. **การดูกล้องสดนอกบ้านผ่าน Cloudflare Tunnel + TURN / MSE Fallback**
   - `go2rtc` รองรับการสตรีมผ่าน WebRTC (<0.2s) และสลับเป็น MSE ผ่าน WebSocket อัตโนมัติเมื่อดูผ่าน 4G/5G นอกบ้านผ่าน Cloudflare Tunnel

---

## การเชื่อมต่อกล้องจริง TP-Link Tapo C260 (TP260)

กล้อง **TP260 (Tapo C260)** รองรับ RTSP ทั้งภาพความละเอียดสูงและเสียงไมโครโฟนผ่าน 2 สตรีม:
- `stream1` (4K / 2K Main Stream) — สำหรับดูภาพสดผ่าน WebRTC บนหน้าแอป PWA
- `stream2` (360p Sub Stream) — สำหรับให้ Python AI (System 1) ประมวลผลตรวจจับแผ่นพันตัว การหายใจ และเสียงร้องโดยกิน CPU ต่ำ

### ขั้นตอนการตั้งค่า
1. **เปิดบัญชีกล้อง (Camera Account) และดู IP ในแอป Tapo:**
   - เปิดแอป **Tapo** บนมือถือ ➔ เลือกกล้อง **TP260** ➔ แตะไอคอนฟันเฟืองมุมขวาบน (**Device Settings**)
   - ไปที่ **Device Info** เพื่อดูเลข **IP Address** ของกล้อง (เช่น `192.168.1.120`)
   - ไปที่ **Advanced Settings ➔ Camera Account** เพื่อตั้ง `Username` และ `Password` สำหรับ RTSP (แนะนำให้ใช้ตัวอักษรและตัวเลขธรรมดา หลีกเลี่ยงอักขระพิเศษอย่าง `@` หรือ `:`)
2. **สร้างไฟล์ `edge/.env` จากเทมเพลต `edge/.env.example`:**
   ```bash
   cp edge/.env.example edge/.env
   ```
   แก้ไขค่าใน `edge/.env` ให้ตรงกับ Username, Password และ IP ของกล้องคุณ:
   ```dotenv
   BABY_1_MAIN_RTSP=rtsp://your_user:your_password@192.168.1.120:554/stream1
   BABY_1_SUB_RTSP=rtsp://your_user:your_password@192.168.1.120:554/stream2
   SUBJECT_MODE=PET_TEST
   ```
3. **รันระบบ Edge (go2rtc + Python AI Detector) และหน้าเว็บ PWA:**
   ```bash
   # Terminal 1: รัน go2rtc + AI Detector
   cd edge
   docker compose up --build

   # Terminal 2: รันหน้าเว็บ PWA
   cd web
   npm install
   npm run dev
   ```
   - เช็คภาพสดจาก `go2rtc` ได้ที่ `http://localhost:1984`
   - เช็คค่า AI Telemetry แบบเรียลไทม์ (ตรวจจับแผ่นพันตัว, RPM, เสียงร้อง) ได้ที่ `http://localhost:8095/status`
   - เปิดหน้าแอปบนเบราว์เซอร์ที่ `http://localhost:5173`

---

## การรันทดสอบด้วยภาพตัวอย่าง (Mock Camera + Edge Detector)

แม้ยังไม่ได้ติดตั้งกล้อง RTSP จริง ระบบมี `mock-camera` ที่นำภาพตัวอย่าง (`edge/assets/sample_baby_band.jpg`) มาจำลองการขยับหน้าอกหายใจที่ 32 ครั้ง/นาที พร้อมจำลองเหตุการณ์ต่างๆ ได้ทันที:

### 1. รันด้วย Docker Compose บน Ubuntu Mini PC หรือเครื่อง Mac
```bash
cd edge
docker compose up --build
```
- **Mock Camera Stream:** `http://localhost:8090/stream.mjpeg`
- **go2rtc Web UI & WebRTC Server:** `http://localhost:1984`
- **Edge Detector Telemetry API:** `http://localhost:8095/status`

สามารถยิงคำสั่งจำลองเหตุการณ์ไปยัง Mock Camera ได้ตลอดเวลา:
```bash
# จำลองการนอนหลับและหายใจปกติ (32 RPM)
curl -X POST http://localhost:8090/simulate/NORMAL

# จำลองเสียงร้องไห้ / เสียงคราง + ดิ้น
curl -X POST http://localhost:8090/simulate/CRY

# จำลองการพลิกตัวคว่ำหน้า (ไม่เห็นลายแผ่นพันตัว)
curl -X POST http://localhost:8090/simulate/ROLLOVER

# จำลองการหยุดขยับหายใจ (0 RPM)
curl -X POST http://localhost:8090/simulate/APNEA
```

### 2. รันชุดทดสอบอัตโนมัติ (Unit & Integration Tests)
```bash
# ทดสอบฝั่ง Edge (Breathing Band Detection, Sub-pixel RPM, Rollover, Audio Cry, Motion)
.venv/bin/python -m pytest edge/detector/tests/test_edge_detector.py -v

# ทดสอบฝั่ง Cloud (System 2 Eventarc CloudEvent, Vertex AI Prompts, Two-Stage Notifier)
.venv/bin/python -m pytest cloud/analyzer-service/tests/test_analyzer_service.py -v
```

### 3. รันหน้าเว็บ Parent's Mobile PWA (`web/`)
```bash
cd web
npm install
npm run dev
```
เปิดเบราว์เซอร์ไปที่ `http://localhost:5173` จะมีปุ่มจำลองเหตุการณ์ให้กดทดสอบการทำงานตั้งแต่ต้นจนจบได้จากหน้าจอทันที

### 4. การตั้งค่า Google Cloud & Firebase (`cloud/infra/setup_gcp.sh`)
```bash
export GCP_PROJECT_ID="your-gcp-project-id"
export GCP_LOCATION="asia-southeast1"
bash cloud/infra/setup_gcp.sh
```
