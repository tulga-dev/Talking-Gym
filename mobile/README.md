# Talking Gym — iOS app (Capacitor shell)

The iOS app is a native Capacitor shell that loads the live PWA
(`https://talking-gym-mn.fly.dev/app`). Every web deploy updates the app
instantly — no App Store review needed for content changes during testing.

Built entirely in CI (GitHub Actions macOS runner) — no Mac required.

## One-time setup (only the account owner can do these)

1. **Enroll in the Apple Developer Program** — $99/year:
   https://developer.apple.com/programs/enroll/
   (Takes 1–2 days for approval.)

2. **Create the App ID + App Store Connect record** (after approval):
   - https://developer.apple.com/account → Identifiers → new App ID:
     `mn.talkinggym.app`
   - https://appstoreconnect.apple.com → My Apps → "+" → New App:
     platform iOS, bundle ID `mn.talkinggym.app`, name "Talking Gym".

3. **Create an App Store Connect API key**:
   App Store Connect → Users and Access → Integrations → App Store Connect API
   → "+" (role: App Manager). Download the `.p8` file (one chance only!).

4. **Add GitHub secrets** (repo → Settings → Secrets → Actions):
   - `ASC_KEY_ID`      — the key's ID (e.g. `2X9R4HXF34`)
   - `ASC_ISSUER_ID`   — the Issuer ID shown on the same page
   - `ASC_KEY_P8`      — base64 of the .p8 file
     (PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("AuthKey_XXX.p8"))`)
   - `APPLE_TEAM_ID`   — Membership page → Team ID (10 chars)

5. **Run the build**: repo → Actions → "iOS TestFlight" → Run workflow.
   ~15 minutes later the build appears in App Store Connect → TestFlight,
   where you add testers by email.

## Local dev notes

- `npm install && npx cap sync ios` regenerates native deps after config changes.
- The shell loads the production URL (see `capacitor.config.json`);
  point `server.url` at a staging URL to test against staging.
- Mic + camera permission strings live in `ios/App/App/Info.plist`.
