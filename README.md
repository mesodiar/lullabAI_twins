# LullabAI Twins

LullabAI Twins ทำงานบนเครื่อง Edge และเครือข่ายในบ้าน กล้องส่งภาพและเสียงผ่าน RTSP เครื่อง Edge ตรวจจับเหตุการณ์ และ Dashboard แสดงภาพสดกับประวัติของแต่ละเตียง

## Architecture

```
[Camera (4K RTSP)] 
       │
       ▼ (Local LAN)
[Ubuntu Mini PC (Edge Node)] 
  ├── 1. go2rtc (WebRTC Server) ═══════════════════════════════╗ (Sub-second Live Video/Audio)
  ├── 2. Python Motion / YAMNet Cry Detector (System 1 Reflex) ║
  └── 3. ส่งคลิป/ภาพเฉพาะตอนมี Event                             ║
              │                                                ║
              ▼                                                ║
      [Cloud Storage] (Auto-delete 7 days)                     ║
              │ (Eventarc Trigger)                             ║
              ▼                                                ║
   [Cloud Run + Vertex AI (Gemini & Argon 4)]                  ║
     (System 2: Safety Check, Cry Reasoner & Sleep State)      ║
              │                                                ║
              ├─── (1) บันทึก Insight & Event Log ──> [Cloud Firestore]
              │                                                │ (Real-time Sync)
              └─── (2) ยิง Web Push Alert ─────────> [Firebase Cloud Messaging (FCM)]
                                                               │ (Wake up via Service Worker)
                                                               ▼
                                            📱 [Parent's Mobile PWA (Firebase Hosting)]
                                               (ดูกล้องสด WebRTC + รับ Alert + ดู Dashboard)
```

## Architecture without GCP (Temporary)

![Tapo streams live video through go2rtc to the dashboard. The Edge Detector processes video and audio, saves events and clips locally, and sends event updates to the dashboard for browser notifications.](edge/assets/architecture/Architect-wo-gcp.png)

- **ภาพสด** ใช้ go2rtc โดยหน้าเว็บลอง WebRTC ก่อน แล้วสลับไป MSE หรือ MJPEG เมื่อจำเป็น
- **การตรวจจับ** ใช้ภาพและเสียงจากกล้องที่ Edge เสียงที่ผ่านเกณฑ์แสดงเป็น “ตรวจพบเสียงคล้ายเสียงร้อง” เพราะคะแนนมาจากลักษณะของเสียง
- **ประวัติ** บันทึก JSON ลงโฟลเดอร์ `edge/local_events/` และกู้คืนได้หลังรีสตาร์ต หน้าเว็บแสดง 3 รายการล่าสุดต่อเตียง กด **See more** เพื่อดูเพิ่มได้สูงสุด 15 รายการต่อเตียง
- **คลิป** บันทึกภาพจากบัฟเฟอร์ก่อนและหลังเหตุการณ์ลง `edge/local_events/media/` คลิปเป็นวิดีโอแบบไม่มีเสียง ไฟล์ยังเก็บไว้หลังรีสตาร์ตและโหมดนี้ไม่มีการลบอัตโนมัติ
- **แจ้งเตือน** เหตุการณ์ใหม่ขึ้นในหน้าเว็บ และเบราว์เซอร์ที่รองรับสามารถแสดงแจ้งเตือนของระบบเมื่อเปิดหน้าไว้และอนุญาต ใช้ `localhost` หรือ HTTPS สำหรับสิทธิ์แจ้งเตือน เมื่อปิดหน้าเว็บ เหตุการณ์ยังถูกบันทึกที่ Edge
- โหมดนี้ใช้ผลจาก Edge โดยตรง โค้ด Cloud Run, Vertex AI และ FCM เก็บไว้ในโปรเจกต์สำหรับเปิดใช้งานภายหลัง

## เริ่มใช้งานด้วยกล้อง Tapo

1. ตั้ง Camera Account ในแอป Tapo แล้วคัดลอก `edge/.env.example` เป็น `edge/.env` หากมีไฟล์เดิมอยู่แล้วให้ใช้ค่าที่ตั้งไว้
2. ตั้ง `BABY_1_MAIN_RTSP` เป็น `stream1` สำหรับภาพสด และ `BABY_1_SUB_RTSP` เป็น `stream2` สำหรับตรวจจับภาพและเสียง ค่า `SUBJECT_MODE` รองรับ `BABY` และ `PET_TEST`
3. รัน Docker Compose จากโฟลเดอร์ `edge/` และรันเว็บจากโฟลเดอร์ `web/`

```bash
# Terminal 1
cd edge
docker compose up -d --build

# Terminal 2
cd web
npm ci
npm run dev
```

- Dashboard เปิดที่ `http://localhost:5173` บนเครื่อง Edge หรือ `http://<IP-เครื่อง-Edge>:5173` บนอุปกรณ์ในเครือข่ายเดียวกัน
- go2rtc ใช้พอร์ต 1984 และ Edge API ใช้พอร์ต 8095 โดยดูสถานะได้ที่ `http://localhost:8095/status`
- สามารถตั้ง URL เองด้วย `VITE_GO2RTC_URL`, `VITE_EDGE_BASE_URL`, `VITE_EDGE_STATUS_URL` และ `VITE_MOCK_CAMERA_URL`
- หากเปิด Dashboard ผ่าน HTTPS ให้ตั้ง URL ของบริการที่ใช้ร่วมกันเป็น HTTPS ด้วย พอร์ตกล้องและประวัติใช้สำหรับเครือข่ายภายในบ้าน

## โหมดทดสอบ

ถ้ายังไม่ตั้ง RTSP จริง ระบบใช้ `mock-camera` บนพอร์ต 8090 และสามารถเปิดโหมดจำลองจากเมนูตั้งค่าบน Dashboard ได้ ทดสอบเฉพาะ Edge โดยใช้คำสั่งนี้จากรากโปรเจกต์

```bash
.venv/bin/python -m pytest edge/detector/tests/test_edge_detector.py -v
```
