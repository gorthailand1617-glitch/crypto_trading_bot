# 🚀 คู่มือการนำบอทขึ้นระบบคลาวด์ออนไลน์ฟรี 24/7 (AlphaScalp V6.0)

คู่มือนี้แนะนำวิธีการนำ **AlphaScalp Trading Bot** ไปเปิดทำงานบน Cloud ตลอด 24 ชั่วโมง 7 วันต่อสัปดาห์ โดยไม่มีค่าใช้จ่าย และไม่ต้องเปิดคอมพิวเตอร์ที่บ้านทิ้งไว้

---

## 🏆 ตัวเลือกที่ 1: Oracle Cloud Always Free (แนะนำสูงสุด ⭐⭐⭐⭐⭐)

Oracle Cloud มีแพ็กเกจ **Always Free** ให้เครื่อง Virtual Machine (VM) ฟรีตลอดชีพ ไม่มีการตัดรอบหรือปิดเครื่อง:
- **สเปก:** Ampere ARM (4 Cores, 24 GB RAM) หรือ AMD (1 Core, 1 GB RAM)
- **Public IP:** ฟรีแบบคงที่ (Static IP) สำหรับเข้าดู Web Dashboard จากมือถือได้ทุกที่

### ขั้นตอนการติดตั้งบน Oracle Cloud (ใช้เวลา 10 นาที):

#### 1. สมัครและสร้าง Instance
1. เข้าไปที่ [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) แล้วกดสมัครบัญชีฟรี (ใช้บัตรเครดิต/เดบิตเพื่อยืนยันตัวตน ไม่มีค่าใช้จ่าย)
2. ไปที่ **Compute > Instances > Create Instance**
3. เลือก Image: **Ubuntu 22.04 LTS** หรือ **Ubuntu 24.04**
4. ดาวน์โหลด SSH Key (`id_rsa`) เก็บไว้ในเครื่อง

#### 2. เชื่อมต่อเข้าไปยัง Server ผ่าน Terminal / PowerShell
```bash
ssh -i /path/to/your-key.key ubuntu@<IP_ADDRESS_OF_ORACLE>
```

#### 3. ติดตั้ง Docker & Docker Compose (คำสั่งเดียว)
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose git
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

#### 4. โคลนโปรเจค หรืออัปโหลดไฟล์มาที่ Server
```bash
mkdir -p ~/crypto-bot && cd ~/crypto-bot
# คัดลอกไฟล์โปรเจคมาไว้ที่นี่ (หรือใช้ git clone)
```
สร้างหรืออัปโหลดไฟล์ `.env`:
```bash
nano .env
```
*(ใส่ `BYBIT_API_KEY`, `BYBIT_API_SECRET`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` ให้ครบถ้วน แล้วกด `Ctrl+O` แล้ว `Enter` ตามด้วย `Ctrl+X`)*

#### 5. สั่งรันบอทและแดชบอร์ด 24/7 ด้วย Docker Compose
```bash
docker compose up -d --build
```
- ระบบจะดาวน์โหลดและคอมไพล์ container รันทั้ง **Trading Engine** และ **Streamlit Dashboard** ในเบื้องหลัง
- มีระบบ `restart: unless-stopped` หากเครื่องเกิดรีสตาร์ท บอทจะเปิดตัวเองขึ้นมาใหม่อัตโนมัติ!

#### 6. ตรวจสอบการทำงานและ Logs แบบ Real-time
```bash
# ดูสถานะ container
docker compose ps

# ดู log การเทรดสดๆ
docker compose logs -f alpha-bot
```

#### 7. การเปิดพอร์ตเพื่อเข้าดู Web Dashboard ผ่าน Browser
ใน Oracle Cloud Console:
- ไปที่ **Networking > Virtual Cloud Networks > Security Lists > Default Security List**
- เพิ่ม **Ingress Rule**:
  - Source CIDR: `0.0.0.0/0`
  - IP Protocol: `TCP`
  - Destination Port: `8501`
- ในเครื่อง Server สั่งเปิด Firewall ของ Ubuntu:
  ```bash
  sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8501 -j ACCEPT
  sudo netfilter-persistent save
  ```
- ตอนนี้คุณสามารถเปิดดู Dashboard ได้จากทุกอุปกรณ์ผ่าน: `http://<IP_ORACLE>:8501`

---

## 🥈 ตัวเลือกที่ 2: Render.com (ฟรี 24/7 ผ่าน GitHub + UptimeRobot) ⭐⭐⭐⭐

*(หมายเหตุ: Koyeb เดิมได้ปิดรับสมัครและควบรวมกับ Mistral AI จึงแนะนำ Render.com ซึ่งฟรี เสถียร และใช้งานง่ายเหมือนกัน)*

Render มีแพ็กเกจ **Free Web Service** ให้โควต้าฟรี 750 ชั่วโมง/เดือน (เพียงพอสำหรับรันบอท 24 ชั่วโมงตลอดทั้งเดือน):

### ขั้นตอนการติดตั้งบน Render (ใช้เวลา 5 นาที):

#### 1. นำโค้ดขึ้น GitHub (Private Repository)
1. เปิด Terminal / PowerShell ในโฟลเดอร์โปรเจกต์นี้
2. สั่งคำสั่ง Git:
   ```bash
   git init
   git add .
   git commit -m "Deploy AlphaScalp V6.0"
   git branch -M main
   git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPO_NAME>.git
   git push -u origin main
   ```
   *(ไฟล์ `.gitignore` จะช่วยป้องกันไม่ให้ไฟล์ `.env` และข้อมูลส่วนตัวหลุดขึ้น GitHub 100%)*

#### 2. สร้าง Web Service บน Render
1. ไปที่ [dashboard.render.com](https://dashboard.render.com)
2. กดปุ่ม **New +** ที่มุมขวาบน ➔ เลือก **Web Service**
3. เลือก **Build and deploy from a Git repository** ➔ กด **Next**
4. เลือก Repository บอทของคุณที่เพิ่ง Push ขึ้นไป (กด Connect)

#### 3. ตั้งค่าการรัน (Settings)
- **Name:** `alphascalp-bot` (หรือชื่อใดก็ได้)
- **Region:** `Singapore` (ใกล้ Bybit ที่สุด ความเร็วและ Ping จะดีที่สุด)
- **Branch:** `main`
- **Runtime:** `Python 3`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python bot.py`
- **Instance Type:** `Free` ($0/month)

#### 4. ใส่ Environment Variables (สำคัญที่สุด)
เลื่อนลงมาที่หัวข้อ **Environment Variables** แล้วกด **Add Environment Variable** ใส่ค่าตามไฟล์ `.env`:
- `BYBIT_API_KEY`: *(Key ของคุณ)*
- `BYBIT_API_SECRET`: *(Secret ของคุณ)*
- `TELEGRAM_TOKEN`: *(Token ของคุณ)*
- `TELEGRAM_CHAT_ID`: *(Chat ID ของคุณ)*
- `SYMBOL`: `XRPUSDT`
- `LEVERAGE`: `10`
- `DRY_RUN`: `false` (หรือ `true` ถ้าต้องการทดสอบก่อน)

กดปุ่ม **Deploy Web Service** ด้านล่างสุด ➔ Render จะเริ่มติดตั้งและรันบอททันที! เมื่อรันเสร็จสถานะจะขึ้นสีเขียว **Live**

#### 5. ป้องกัน Render หลับ (Keep-Alive 24/7 ด้วย UptimeRobot ฟรี)
เนื่องจาก Render Free Tier จะหยุดทำงาน (Sleep) ถ้าไม่มีคนเปิดหน้าเว็บเกิน 15 นาที จึงต้องตั้งค่าให้ส่ง Ping อัตโนมัติ:
1. คัดลอก URL ของ Web Service บน Render (เช่น `https://alphascalp-bot-xxxx.onrender.com`)
2. ไปที่ [uptimerobot.com](https://uptimerobot.com) สมัครบัญชีฟรี
3. กด **Add New Monitor**:
   - **Monitor Type:** `HTTP(s)`
   - **Friendly Name:** `AlphaScalp Bot`
   - **URL (or IP):** วาง URL ของ Render ลงไป
   - **Monitoring Interval:** ทุกๆ `5 minutes`
4. กด **Create Monitor**
   > 🎉 **เสร็จสิ้น!** UptimeRobot จะยิง Ping ปลุกบอททุกๆ 5 นาที ทำให้บอททำงานต่อเนื่อง 24 ชั่วโมง 7 วันต่อสัปดาห์โดยไม่มีวันหลับ!

---

## 🛡️ คำแนะนำด้านความปลอดภัยขั้นสูงสุด (Security Best Practices)

1. **Bybit API Key Permissions:**
   - ให้เปิดเฉพาะสิทธิ์ **Derivatives Trading (Linear Contract)** เท่านั้น
   - **ห้าม** เปิดสิทธิ์ Withdrawals (ถอนเงิน) เด็ดขาด
2. **IP Whitelisting:**
   - เมื่อได้ Static IP จาก Oracle Cloud ให้นำ IP นั้นไปกรอกในช่อง IP Whitelist ของ Bybit API Key เพื่อให้บอทของคุณสามารถสั่งเทรดได้จาก IP นั้นเพียงเครื่องเดียว ป้องกันแฮกเกอร์ 100%
3. **ควบคุมผ่าน Telegram:**
   - บอทมีระบบ Security Check ป้องกัน Chat ID อื่นๆ มาสั่งงาน
   - สามารถพิมพ์ `/pause`, `/resume`, `/status`, `/vault`, `/closeall` ได้จากมือถือตลอด 24 ชั่วโมง
