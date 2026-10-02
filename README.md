# Auto Spare Parts Management System

One shop system for stock, sales, suppliers, price history, and reports. Use it on this computer, or host it so phones and other computers can open it while this laptop is off.

## What it does

- Dashboard with stock, sales, profit, and low-stock alerts
- Inventory with engine / vehicle and specification (so similar parts stay distinct)
- Selling-price history
- Sales with the actual unit price charged
- Supplier contacts
- Reports by day, week, month, or a date range
- Excel backup and restore
- Clean phone layout with an Install App button (PWA)

## Run on this computer

1. Install [Python 3](https://python.org/downloads/). Tick **Add Python to PATH**.
2. Double-click `start.bat`.
3. Open `http://127.0.0.1:5000`

Optional: copy `.env.example` to `.env` and set `APP_PASSWORD`.

Shop data on this computer is saved in `instance/auto_spares.db`.

## Use from any phone or computer (laptop can be off)

The program must run on a host, not only on GitHub. GitHub stores the code. Render (or similar) keeps the website open.

1. Create a free PostgreSQL database at [Neon](https://neon.tech) and copy the connection string (`DATABASE_URL`).
2. Push this folder to GitHub (GitHub Desktop → publish this repository).
3. On [Render](https://render.com), create a **Web Service**, connect that GitHub repo, and set:
   - `APP_PASSWORD` — the shop sign-in password
   - `DATABASE_URL` — the Neon connection string
   - `PUBLIC_URL` — the `https://....onrender.com` address Render gives you (add this after the first deploy)
4. Open that `https://` address on any phone or computer. From the Dashboard, use **Download / Install App** to add it to the phone home screen. On iPhone, use Safari → Share → Add to Home Screen.

Free Render services sleep after inactivity. The first open after a rest can take about a minute. Data stays in Neon, not on this laptop.

After it is live, download Excel from **Data / SQL** so you always have an offline copy.
