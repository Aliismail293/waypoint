# Short URL with Firebase Hosting (V2, Stage 6)

Firebase Hosting sits in front of the existing Cloud Run service `waypoint` (project `waypoint-513003`,
`us-central1`) and gives it a clean `https://SITE_ID.web.app` address. Nothing here has been run: it needs your
Google credentials, so these are the commands for you to run.

**SITE_ID is a placeholder.** `firebase.json` uses `waypoint-planner`. Pick your own (lowercase letters, digits,
hyphens; globally unique; the plan suggested `waypoint-planner`, `waypointplanner`, `waypoint-degree`; avoid "UTM" in
the name) and change the `site` field before step 3.

## One-time setup

```
npm install -g firebase-tools
firebase login
firebase hosting:sites:create waypoint-planner --project waypoint-513003
```

(If Firebase is not yet enabled on the project: `firebase projects:addfirebase waypoint-513003`.)

## Preview first, then go live

```
firebase hosting:channel:deploy preview --project waypoint-513003
```

This prints a temporary `https://waypoint-planner--preview-xxxx.web.app` URL (expires in 7 days). To go live:

```
firebase deploy --only hosting --project waypoint-513003
```

The rewrite (`**` to Cloud Run `waypoint`) sends every path, including `/api/*`, `/privacy` and `/terms`, to the
service, so the browser sees one origin: **no CORS is needed** and the relative links in the app work unchanged.

Hosting always rewrites to the service's live (100% traffic) revision, not a tagged `v2---` preview. To put V2 behind
the short URL, first promote it: `gcloud run services update-traffic waypoint --to-latest` (a user decision; not done).

## Before you share the URL

1. **OAuth origins** (Google Cloud console, APIs & Services, Credentials, the Web client used for Sign-In): add
   `https://waypoint-planner.web.app` and `https://waypoint-planner.firebaseapp.com` under **Authorised JavaScript
   origins**. Without this, Google Sign-In fails on the new address with `origin_mismatch`.
2. **Streaming check** (not tested here; Hosting needs credentials): open the site, sign in, ask a question, and
   confirm the answer streams word by word. Hosting proxies Cloud Run responses with a 60 second limit, and V2 answers
   normally finish well within that. If it buffers or times out, fall back to the Cloud Run URL (it stays valid) and
   set `WAYPOINT_CORS_ORIGINS=https://waypoint-planner.web.app` on the service, then point the front end at the Cloud
   Run URL for `/api`. CORS is off unless that variable is set.
3. Update the README "Live URL" line and the Sign-In consent screen's authorised domain (`web.app`) if prompted.
