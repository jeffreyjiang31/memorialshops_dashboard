# Deploy Memorial Metrics – Step by Step

Follow these steps to put your dashboard online and share the link (e.g. with your brother).

---

## Step 1: Install dependencies (one time)

Open PowerShell, go to your project folder, and run:

```powershell
cd "C:\Users\jeffr\OneDrive\Documents\RememberUrns\Scripts\Metrics"
pip install -r requirements.txt
```

---

## Step 2: Test locally (optional but recommended)

```powershell
flask --app app run --port 5051
```

Open **http://127.0.0.1:5051** in your browser. If the dashboard loads and shows data, you’re good to deploy.

Press **Ctrl+C** in the terminal to stop the server.

---

## Step 3: Put your code on GitHub

### 3a. Initialize Git (if you haven’t already)

```powershell
cd "C:\Users\jeffr\OneDrive\Documents\RememberUrns\Scripts\Metrics"
git init
```

### 3b. Stage and commit

```powershell
git add .
git status
```

Confirm that **`.env` does NOT appear** in the list (it’s in `.gitignore`). If you see it, do not add it.

```powershell
git commit -m "Memorial Metrics dashboard - ready for deploy"
```

### 3c. Create a repo on GitHub

1. Go to **https://github.com/new**
2. **Repository name:** e.g. `memorial-metrics` (or any name you like)
3. Leave it **Public**, no need to add README or .gitignore (you already have them)
4. Click **Create repository**

### 3d. Connect and push

GitHub will show you commands; use these (replace `YOUR_USERNAME` and `memorial-metrics` if you chose a different repo name):

```powershell
git remote add origin https://github.com/YOUR_USERNAME/memorial-metrics.git
git branch -M main
git push -u origin main
```

If GitHub asks you to sign in, use the browser or a personal access token.

---

## Step 4: Deploy on Render

### 4a. Sign up / log in

1. Go to **https://render.com**
2. Sign up or log in (e.g. **Sign up with GitHub**).

### 4b. New Web Service from repo

1. In the dashboard click **New +** → **Web Service**.
2. Connect GitHub if asked and **authorize Render** to see your repos.
3. Find and select the repo you pushed (e.g. `memorial-metrics`) and click **Connect**.

### 4c. Configure the service

Use these settings:

| Field | Value |
|--------|--------|
| **Name** | `memorial-metrics` (or any name) |
| **Region** | Pick one close to you |
| **Branch** | `main` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app` |

### 4d. Add environment variables

Scroll to **Environment Variables** and click **Add Environment Variable**. Add these **four** (use the same values as in your local `.env`):

| Key | Value (your real values) |
|-----|---------------------------|
| `ODOO_URL` | `https://sinosource.odoo.com` |
| `ODOO_DB` | `odoo-ps-psus-sinosource-production-11139452` |
| `ODOO_USERNAME` | your Odoo email |
| `ODOO_PASSWORD` | your Odoo password / API key |

Mark **ODOO_PASSWORD** as **Secret** if Render offers that option.

### 4e. Create Web Service

Click **Create Web Service**. Render will build and start the app (first time can take a few minutes).

---

## Step 5: Get your link

When the deploy finishes you’ll see a URL at the top, e.g.:

**https://memorial-metrics-xxxx.onrender.com**

Open it in your browser. If the dashboard loads and shows data, deployment worked.

---

## Step 6: Share with your brother

Send him that URL. He can open it in any browser; no install needed.

**Note:** On the free plan, the app may “spin down” after some idle time. The first open after that can take 30–60 seconds; that’s normal.

---

## Updating the app later (e.g. mobile-friendly changes)

To deploy any code changes (including the new mobile-friendly layout):

1. **Commit and push** from your project folder:

```powershell
cd "C:\Users\jeffr\OneDrive\Documents\RememberUrns\Scripts\Metrics"
git add .
git commit -m "Describe your change"
git push
```

Use the branch Render is using (e.g. `master` or `main` — check your Render service **Settings** if unsure). For example, if you use `master`:

```powershell
git push origin master
```

2. **Render redeploys automatically** when it sees the new push. In the Render dashboard, open your service and watch the **Events** or **Logs** tab; when the deploy finishes, the live site will show your updates.

3. **Check on your phone** by opening the same Render URL in your mobile browser to confirm the mobile-friendly layout.
