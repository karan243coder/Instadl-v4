# 🌌 SaveGr Ultra Premium - Advanced Instagram Downloader Web Suite & Telegram Bot (v3.0)

[![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)](https://python.org)
[![Netlify](https://img.shields.io/badge/Netlify-Serverless-00C7B7?logo=netlify&logoColor=white)](https://netlify.com)
[![Koyeb](https://img.shields.io/badge/Koyeb-Hosted-000000?logo=koyeb&logoColor=white)](https://koyeb.com)
[![MongoDB](https://img.shields.io/badge/MongoDB-Dynamic-47A248?logo=mongodb&logoColor=white)](https://mongodb.com)

Welcome to **SaveGr Ultra Premium (v3.0)**, the ultimate, pixel-perfect clone of `SaveGr.com`. This enterprise-grade repository includes a super-responsive, fluid-aspect-ratio browser frontend, a high-performance, load-balanced serverless API architecture, and a dynamic, rate-limit proof Telegram Bot backed by MongoDB!

---

## 🌟 Major Upgrades in v3.0 (What's New)

### 1. 📡 Multi-API Dynamic Round-Robin Load Balancer (No Overloads)
* **What it does:** Both the website and the bot can automatically shuffle, load-balance, and ping multiple Netlify API scraper nodes randomly.
* **Failover Protocol:** If any node fails or returns fallback stock files due to rate limits, the load balancer sequentially traverses subsequent nodes instantly to retrieve the actual file seamlessly.

### 2. 🚫 100% Mock-Free Smart Metadata System
* **What it does:** No more fake or placeholder values (like `@Unknown` or `N/A`) if scraping fails. 
* **Dynamic Filters:** The bot extracts actual usernames and IDs directly from URL parameters. If no real metadata can be resolved, fake placeholders are completely hidden, keeping delivery captions incredibly clean and elegant!

### 3. 🛡️ Interactive VIP Admin Dashboard (Telegram Inline Keyboards)
* **What it does:** Running `/admin` displays a premium inline control room right in Telegram.
* **Features:**
  * **System Stats:** Monitor registered users, logged downloads, and active scraper nodes.
  * **Server Health:** Live pings each Netlify scraper node to check active latency in milliseconds (`ms`).
  * **Scraper Node Manager:** Dynamically Add or Delete Netlify scraper endpoints directly from Telegram with persistent database sync.
  * **Admins List Manager:** Add or delete administrators dynamically.
  * **Bans Manager:** Block/Unblock users from using the bot using interactive inline prompts.
  * **Claim Admin Bypass:** If no admin is configured, the first user to run `/admin` is automatically registered as Master Admin.

4. 😴 **Automatic Rate-Limit Shield (429 Retries)**:
   * Handles Telegram's strict upload speed limiters. If the bot receives a `429 Too Many Requests` error, it automatically pauses execution for the exact seconds requested, sleeps, and retries the upload without any data drop or error.

5. 🌌 **Aesthetic Neon Glow UI (Website)**:
   * Custom double-rotating neon spinner rings, glowing pulsing headers, vertical laser sweeps, and live mock system log terminals for an incredibly premium user experience!

---

## 📂 Project Structure

```text
instadl-project/
├── index.html                  # Main All-in-One Downloader (Neon Glow Custom)
├── video.html                  # Video Downloader Page
├── reels.html                  # Reels Downloader Page
├── photo.html                  # Photo Downloader Page
├── story.html                  # Story Downloader Page
├── igtv.html                   # IGTV Downloader Page
├── carousel.html               # Carousel / Album Downloader Page
├── highlights.html            # Highlights Downloader Page (Math-bypass)
├── profile.html                # Profile DP Downloader Page
├── faq.html                    # FAQ Page
├── api_docs.html               # Developer Interactive API documentation
├── netlify.toml                # Netlify Serverless redirection mappings
├── package.json                # Serverless dependencies for Netlify
├── main.py                     # Local FastAPI Server
├── requirements.txt            # Python Dependencies
├── bot.py                      # Telegram Bot Integration Script 🤖
├── js/
│   └── download-actions.js     # Load Balancer & CORS unblock coordinator
└── netlify/
    └── functions/
        ├── download.js         # Core Netlify scraper endpoint
        └── file.js             # Referrer bypass image proxy
```

---

## ⚙️ Environment Variables (Koyeb/Hosting Dashboard)

To activate the premium database features, set these variables in your Koyeb container or host dashboard:

| Variable Name | Description | Example Value |
| :--- | :--- | :--- |
| `BOT_TOKEN` | Secure HTTP token from Telegram's @BotFather | `123456:ABC-DEF1234ghIkl-zyx` |
| `MONGO_URL` | MongoDB connection string (Atlas or Local) | `mongodb+srv://user:pass@cluster.mongodb.net/` |
| `ADMIN_ID` | Numerical User ID of the primary master admin | `123456789` |
| `WEBSITE_API_URLS` | Comma-separated list of active Netlify clones | `https://url1.com,https://url2.com` |
| `LOG_CHANNEL_ID` | Channel ID to log downloads natively | `-1001234567890` |

---

## 🚀 Deployment Guides

### 🌐 Step 1: Deploy Website on Netlify Drop (1-Click)
1. Prepare a folder containing the files in `instadl-project.zip`.
2. Go to [Netlify Drop](https://app.netlify.com/drop).
3. Drag and drop your folder.
4. Go to **Site Settings** and change your domain to get your `.netlify.app` URL.

### 🤖 Step 2: Deploy Python Bot on Koyeb (Free Tier)
1. Create a GitHub Repository and upload the files in `instadl-bot.zip`.
2. Connect your GitHub Account to [Koyeb Dashboard](https://app.koyeb.com/).
3. Create a new service selecting your repository as a **Web App** (so that health check monitors work on port `8000`).
4. Set the **Environment Variables** listed above.
5. Set the build/run command as:
   * Run Command: `python bot.py`
6. Click **Deploy**! Koyeb will automatically start the background serverless health-checking port `8000` and start polling your bot.

---

## 🔌 API Developer System Integration

The serverless backends expose a powerful REST API for programmatic downloads:

### 📥 1. Media Extractor (`POST /api/download`)
* **Endpoint URL:** `https://your-netlify-url.netlify.app/api/download`
* **Payload Format (JSON):**
```json
{
  "url": "https://www.instagram.com/reel/C9eI1qNyfhj/"
}
```
* **Success JSON Response:**
```json
{
  "ok": true,
  "contentType": "video",
  "items": [
    {
      "url": "https://scontent.cdninstagram.com/v/...",
      "type": "video",
      "thumbnail": "https://scontent.cdninstagram.com/v/...",
      "filename": "savegr-download-1.mp4"
    }
  ]
}
```

### 🛰️ 2. Secure Image CDN Proxy (`GET /api/file`)
Bypasses CORS restrictions and Referrer leaks natively in mobile and desktop browsers:
* **Usage Format:** `https://your-netlify-url.netlify.app/api/file?url=IMAGE_CDN_URL&inline=true`

---

### ⭐ License & Author
Developed and customized dynamically for premium, watermark-free delivery. Designed as an independent downloader suite with zero dependency on official Meta APIs.

**SaveGr™ Ultra Premium Downloader** - Built with ❤️ in Hindi-English!
