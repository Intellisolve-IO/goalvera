# Open Classroom Recorder & IEP Compliance Engine
## Comprehensive Technical System Specification & Architectural Blueprint

---

## Executive Summary

The **Open Classroom Recorder** is an open-source, edge-to-cloud hardware and software solution designed for special education settings. By combining a low-cost ($50 target BOM) touchscreen recorder with cloud-based automated speech recognition and LLM synthesis (Google Gemini 1.5 Flash), the system eliminates up to 80% of administrative documentation overhead for special education teachers. 

It converts daily ambient classroom recordings or structured teacher dictations into audit-ready **Present Levels of Performance (PLOP)** logs, **Antecedent-Behavior-Consequence (ABC)** incident records, and quarterly **IEP progress reports**, while automatically generating draft weekly summaries for parents.

---

## 1. Bill of Materials (BOM) & Hardware Specifications

All components are standard off-the-shelf parts available via Amazon/online electronics distributors.

### Hardware Component Matrix

| Category | Specific Part / Component | Function & Specs | Amazon / Order Reference | Target Unit Cost |
| :--- | :--- | :--- | :--- | :--- |
| **SoC & Display** | ELECROW ESP32 Display 800x480 4.3" HMI Advanced | ESP32-S3 (16MB Flash, 8MB PSRAM), IPS Capacitive Touch Screen, Onboard MicroSD Slot. | ELECROW 4.3" HMI | ~$24.00 |
| **Audio Capture** | Teyleten Robot ICS-43434 MEMS Microphone | High-precision, low-power I2S digital output, omnidirectional mic. Replaces INMP441 with higher SNR (65 dBA). | Order #114-7282391-9534668 | ~$2.50 |
| **Power System** | FORIOT 18650 Battery Holder V8 Shield (Dual Slot) | Micro-USB/USB-C charging, dual 18650 Li-ion cell support, regulated 5V/3A & 3V/1A outputs with safety cutoffs. | Order #114-7282391-9534668 | ~$8.00 |
| **Storage** | SanDisk / Samsung 32GB MicroSD Card | Class 10 / U1 SD card using SDMMC 4-bit bus mode for local stream buffering. | Off-the-shelf | ~$5.50 |
| **Enclosure** | 3D Printed PETG / ABS Project Box | Custom enclosure housing the 4.3" display, power shield, dual 18650s, and microphone acoustic port. | Open-source STL | ~$4.00 |
| **Total BOM** | | **Complete Hardware Assembly** | **Order Total: $49.67** | **~$44.00–$49.67** |

---

## 2. Hardware Wiring & Edge Security Architecture

### Pinout Mapping (ELECROW ESP32-S3)

* **ICS-43434 I2S Digital Microphone:**
  * `VDD` $\rightarrow$ **3.3V** (Regulated output from V8 Shield or ELECROW header)
  * `GND` $\rightarrow$ **GND**
  * `SCK` (BCLK) $\rightarrow$ **GPIO 12**
  * `WS` (LRCLK) $\rightarrow$ **GPIO 13**
  * `SD` (DATA) $\rightarrow$ **GPIO 14**
  * `L/R` $\rightarrow$ **GND** (Configures Left Channel, 16kHz, 16-bit PCM Mono audio stream)
* **MicroSD Card (SDMMC 4-Bit Mode):**
  * `CLK` $\rightarrow$ GPIO 15 | `CMD` $\rightarrow$ GPIO 7 | `D0` $\rightarrow$ GPIO 4 | `D1` $\rightarrow$ GPIO 5 | `D2` $\rightarrow$ GPIO 6 | `D3` $\rightarrow$ GPIO 22

### Edge Encryption & Zero-Trust At Rest
To ensure data security if a physical device is lost, stolen, or tampered with:
1. **AES-256-CTR Stream Encryption:** The ESP32 Hardware Cryptographic Engine (`mbedtls/aes.h`) encrypts raw PCM audio blocks in real time before writing to the MicroSD card.
2. **Key Provisioning:** AES keys are generated dynamically during device onboarding via Espressif RainMaker/NVS, locked down with ESP32 **Flash Encryption** and **Secure Boot v2**.
3. **Data Uselessness:** The `.enc` files stored on the local SD card cannot be read or decrypted on a standard PC without the corresponding private key stored in GCP KMS.

---

## 3. Firmware Architecture & On-Device UI (ESP32 / LVGL)

### FreeRTOS Multithreaded Execution Model
* **Task 1: `Task_UI_LVGL` (Core 1, Priority 2):** Renders the 800x480 UI at 30 FPS, handles touch inputs, updates clock, battery %, and Wi-Fi signal.
* **Task 2: `Task_Audio_Capture` (Core 0, Priority 5):** Pulls raw 16kHz audio from I2S DMA buffers into a cyclic PSRAM buffer.
* **Task 3: `Task_Crypto_Disk` (Core 0, Priority 4):** Encrypts 4KB PCM audio chunks using AES-256-CTR and flushes them to the SD card.
* **Task 4: `Task_GCS_Uploader` (Core 1, Priority 1):** Manages background HTTP Resumable Uploads to Google Cloud Storage over Wi-Fi.

### On-Device User Interface Layout (800x480 Screen)

#### Pre-Session Onboarding Screen
* **Header Bar:** Wi-Fi Signal Strength Indicator, Current Battery %, System Date/Time.
* **Teacher Selection Dropdown:** Populated dynamically from synced local NVS cache:
  ```
  [ Dropdown: Select Teacher Profile ]
  |-- Sarah Jenkins (s.jenkins@school.org)
  |-- Mark Davis (m.davis@school.org)
  ```
* **Primary Action Button:** Large "START RECORDING" Touch Target.

#### Active Session Monitor Screen
```
+-----------------------------------------------------------------------------------+
| [WiFi: 82%]     [Time: 10:14 AM]     [Date: 09/24/2026]     [Battery: 88% |||| ] |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  TEACHER: Sarah Jenkins                                                           |
|  STATUS:  RECORDING IN PROGRESS...                                                |
|                                                                                   |
|  +-------------------------------------+  +------------------------------------+  |
|  |  TIME REMAINING: 05h 22m          |  |  SD CAPACITY: 18.4 GB (58 hrs)     |  |
|  +-------------------------------------+  +------------------------------------+  |
|                                                                                   |
|  +-------------------------------------+  +------------------------------------+  |
|  |  [!] FLAG INCIDENT / NOTE           |  |  [ [] ] STOP & SUBMIT RECORDING    |  |
|  |  (Appends Timestamp Marker)         |  |                                    |  |
|  +-------------------------------------+  +------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 4. Cloud Architecture & Pipeline Specification

```
[ESP32 Device Uploads Encrypted .enc File]
                   │
                   ▼
  ┌─────────────────────────────────┐
  │ GCS Bucket: /raw-audio-incoming │
  └────────────────┬────────────────┘
                   │
                   ▼ (Eventarc Storage Trigger)
  ┌─────────────────────────────────┐
  │ Cloud Run Pipeline Handler API  │
  └────────────────┬────────────────┘
                   │
  ├── 1. Fetch AES Decryption Key from GCP KMS
  ├── 2. Decrypt Stream in Memory (Zero Disk Persistence)
  ├── 3. Submit Audio to Cloud Speech-to-Text v2 API
  │      └── Speaker Diarization, Multi-Speaker Separation
  ├── 4. Pass Transcripts & IEP Goals to Gemini 1.5 Flash Pipeline
  └── 5. Write Structured Outputs to Firestore Database
```

---

## 5. Database Schema (Firebase / Firestore)

### Collection: `/teachers`
```json
{
  "teacher_id": "usr_98123",
  "email": "s.jenkins@school.org",
  "full_name": "Sarah Jenkins",
  "assigned_students": ["stu_101", "stu_102"],
  "pin_hash": "$2b$10$e8..."
}
```

### Collection: `/students`
```json
{
  "student_id": "stu_101",
  "first_name": "Marcus",
  "last_initial": "A.",
  "iep_goals": [
    { "goal_id": "g1", "category": "Behavioral", "description": "De-escalate sensory overload within 5 mins." },
    { "goal_id": "g2", "category": "Academic", "description": "Achieve 80% accuracy in double-digit math." }
  ]
}
```

### Collection: `/recordings`
```json
{
  "recording_id": "rec_20260924_1014",
  "teacher_id": "usr_98123",
  "timestamp_start": "2026-09-24T10:14:00Z",
  "duration_seconds": 2700,
  "incident_flags": [1240, 2100],
  "transcript_path": "gs://school-transcripts-prod/rec_20260924_1014.json",
  "processed_analysis": {
    "iep_metrics": [
      { "goal_id": "g1", "observed_status": "Met", "notes": "Used noise-canceling headphones at 10:34 AM." }
    ],
    "session_summary": "Marcus worked on math worksheets. Required verbal prompts twice.",
    "draft_parent_email": "Hi! Marcus had a great morning working on double-digit addition..."
  }
}
```

---

## 6. Gemini 1.5 Flash AI Pipeline Prompt Specification

```text
SYSTEM INSTRUCTION:
You are an expert Special Education Compliance Analyst and Behavioral Specialist. You will be provided a classroom session transcript containing speaker diarization, active IEP goals for assigned students, and flagged incident timestamps. Extract observable academic and behavioral indicators accurately and objectively without hallucinating data.

INPUT DATA:
Student Name: {STUDENT_FIRST_NAME}
Active IEP Goals: {STUDENT_IEP_GOALS_JSON}
Raw Transcript: {TRANSCRIPT_TEXT}
Incident Timestamps (Seconds): {INCIDENT_FLAGS_ARRAY}

TASKS:
1. Cross-reference transcript dialogue and events against each IEP goal.
2. Determine progress status: "Demonstrated", "Struggled", or "Not Observed".
3. Extract direct supporting quotes from the transcript for each goal.
4. Analyze tagged Incident Timestamps ({INCIDENT_FLAGS_ARRAY}) using an Antecedent-Behavior-Consequence (ABC) framework:
   - Antecedent: What happened immediately prior to the flag?
   - Behavior: What specific observable behavior occurred?
   - Consequence/Intervention: What action did the educator take, and what was the outcome?
5. Draft a warm, professional, non-jargon weekly parent update email.

OUTPUT FORMAT (JSON ONLY):
{
  "goal_evaluations": [
    {
      "goal_id": "string",
      "status": "Demonstrated | Struggled | Not Observed",
      "evidence_quote": "string",
      "analysis": "string"
    }
  ],
  "incident_analysis": [
    {
      "timestamp_seconds": 1240,
      "antecedent": "string",
      "behavior": "string",
      "consequence_intervention": "string"
    }
  ],
  "general_session_summary": "string",
  "draft_parent_email": "string"
}
```

---

## 7. Web Portal & UI Specification

### Teacher Portal Navigation Layout

#### 1. Identity Selection Login
Passwordless Firebase Authentication (Email Link / Magic Link) or quick PIN verification selecting from registered teacher tiles.

#### 2. Weekly Student Matrix View
```
+-----------------------------------------------------------------------------------+
| TEACHER: Sarah Jenkins  |  WEEK OF: Sept 21 - Sept 25, 2026                          |
+-----------------------------------------------------------------------------------+
| Student      | MON (9/21)  | TUE (9/22)  | WED (9/23)  | THU (9/24)  | FRI (9/25)  |
+--------------+-------------+-------------+-------------+-------------+-------------+
| Marcus A.    | [x] Rec (45m)| [x] Rec (50m)| [x] Rec (30m)| [!] Flagged | [ ] Pending |
|              | IEP: 2/2 Met| IEP: 1/2 Met| IEP: 2/2 Met| IEP: Review |             |
+--------------+-------------+-------------+-------------+-------------+-------------+
| Sophia L.    | [x] Rec (40m)| [x] Rec (45m)| [x] Rec (45m)| [x] Rec (40m)| [ ] Pending |
|              | IEP: 1/1 Met| IEP: 1/1 Met| IEP: 1/1 Met| IEP: 1/1 Met|             |
+-----------------------------------------------------------------------------------+
```

#### 3. Detail & Email Generation Modal
Allows teachers to click into any session tile to view extracted ABC logs, audit goal progress, manually tweak generated summaries, and click **"Send Parent Email"** via SendGrid/Gmail integration.

---

## 8. Legal, Compliance & Consent Framework

### Regulatory Matrix

| Regulatory Requirement | Primary Legal Risk | Engineering & Operational Mitigation |
| :--- | :--- | :--- |
| **FERPA** | Unauthorized storage of minor PII / educational records. | Hardware AES-256 encryption, Zero-Cloud Audio Retention (auto-purge raw audio after transcription), GCP Data Privacy Agreement (DPA). |
| **COPPA** | Automated processing of data/audio of minors under 13. | Parent Consent Form required prior to onboarding; no commercial data monetization or model training on school data. |
| **Wiretapping Laws** | Multi-party audio recording without explicit consent. | Active LED indicator light on device during recording; mandatory signed Parent Opt-In forms on file. |

### Required Contractual Sign-Off Checklist
* [ ] **Parental Consent Form:** Signed copy authorizing audio processing and AI transcription for IEP tracking.
* [ ] **District IT Security Clearance:** GCP Tenant deployment approval with KMS key ownership.
* [ ] **Vendor DPA Execution:** GCP / Firebase zero-data-retention agreement signed by school administration.
* [ ] **Automatic Data Retention Rules:** GCS Lifecycle Policy set to auto-delete raw `.enc` audio files after 7 days.

---

## 9. Open Source Repository Structure

```
/open-classroom-recorder
├── /firmware                  # Codey / ESP-IDF C++ Source Code
│   ├── /main                  # I2S (ICS-43434), AES-CTR Crypto, SDMMC Drivers
│   ├── /ui                    # LVGL Screen Layouts (800x480 Resolution)
│   └── sdkconfig.defaults     # ESP32 Flash Encryption & Secure Boot Configuration
├── /cloud-terraform           # One-Click Infrastructure as Code (GCP)
│   ├── main.tf                # Buckets, Cloud Run Services, KMS, Firestore DB
│   └── iam.tf                 # Least-Privilege Role Policies
├── /backend-pipeline          # Node.js / Python Cloud Run Microservices
│   ├── /decrypter             # KMS Key Fetcher & AES Stream Decryptor
│   ├── /speech_transcriber    # Speech-to-Text v2 Integration
│   └── /gemini_analyzer       # Gemini 1.5 IEP Metrics & Email Generation Prompts
├── /frontend-web              # React / Angular Web App
│   ├── /src/app/teachers      # Teacher Matrix & Weekly View Components
│   └── /src/app/parent-email  # Dynamic Email Generation & Editor UI
└── README.md                  # Assembly Guide, Hardware BOM, and Quickstart Manual
```