# 🩺 Kokoro Doctor – Healthcare Platform

**Kokoro Doctor** is a full-stack serverless platform that allows patients to:

- Book consultations with doctors
- Securely upload and manage medical records
- Subscribe to preferred doctors
- Chat using AI-powered assistants

Built using **FastAPI**, **AWS Lambda**, **DynamoDB**, **S3**, and **API Gateway**, the system is modular, scalable, and secure.

---

## 🌐 Live Domain

**Frontend & API Gateway**: [https://kokoro.doctor](https://kokoro.doctor)

---

## 🧱 Tech Stack

| Layer         | Tools / Services                                 |
| ------------- | ------------------------------------------------ |
| Backend       | FastAPI + AWS Lambda (Python 3.11)               |
| Frontend      | React (deployed on EC2 with Spot instance logic) |
| Infra as Code | AWS SAM (`template.yaml`)                        |
| Database      | DynamoDB                                         |
| Storage       | AWS S3 (file storage, documents)                 |
| API Gateway   | AWS API Gateway (with proxy integrations)        |
| Payments      | Razorpay                                         |

---

## 📦 Modules & APIs

### 1. `auth` – Authentication

- `POST /auth/signupUser`
- `POST /auth/signupDoctor`
- `POST /auth/loginUser`
- `POST /auth/loginDoctor`

---

### 2. `doctorsService` – Doctor Management

- `POST /doctorsService/completeProfile` – Update doctor profile & upload documents
- `POST /doctorsService/fetchDoctors` – Fetch list of all doctors
- `POST /doctorsService/subscribe` – User subscribes to doctor
- `POST /doctorsService/setSlots` – Doctor sets availability for specific days

---

### 3. `doctorBookings` – Appointment Booking

- `POST /doctorBookings/book` – Book a 30-min slot
- `POST /doctorBookings/cancel` – Cancel an existing booking
- `POST /doctorBookings/available` – View available slots for a day
- `POST /doctorBookings/user` – Get bookings made by a user

> ⏱️ Each slot: 30 min | Max 5 users per slot | Bookable: 15 days in advance

---

### 4. `medilocker` – Medical File Locker

- `POST /medilocker/upload` – Upload one or more files
- `POST /medilocker/fetch` – List user’s files with metadata
- `POST /medilocker/download` – Get pre-signed download URL
- `POST /medilocker/delete` – Delete a file

---

### 5. `chat` – AI Chat Assistant

- `POST /chat` – Send a prompt and get response using `OLLAMA_API`
- Logs chat with timestamp in DynamoDB

---

### 6. `payment` – Razorpay Integration

- `POST /process-payment` – Create payment link and save status to DB

---

## 🗃️ DynamoDB Tables

| Table                     | Purpose                                  |
| ------------------------- | ---------------------------------------- |
| `Users`                   | User login data, subscriptions           |
| `Doctors`                 | Doctor data and onboarding status        |
| `DoctorAvailabilityTable` | Stores time slots per doctor per weekday |
| `DoctorBookingsTable`     | Stores user bookings (PK/SK + GSI)       |
| `ChatHistory`             | Stores user chat logs with timestamps    |
| `PaymentsTable`           | Stores Razorpay payment info             |

### 🧠 Bookings Table Schema

| Field     | Format                           |
| --------- | -------------------------------- |
| `PK`      | `doctor_id#YYYY-MM-DD`           |
| `SK`      | `HH:MM#user_id`                  |
| `user_id` | Used for GSI: `GSI_UserBookings` |
| `ttl`     | Epoch timestamp (7-day expiry)   |

---

## ☁️ S3 Buckets

| Bucket Name               | Usage                         |
| ------------------------- | ----------------------------- |
| `kokoro-medilocker`       | User-uploaded medical files   |
| `kokoro-doctor-documents` | Doctor registration documents |

---

## 🚀 Deployment (AWS SAM)

### Build

```bash
sam build
```
