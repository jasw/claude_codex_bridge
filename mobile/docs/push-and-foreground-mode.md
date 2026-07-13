# Android push and foreground-mode contract

## Decisions

Android push is an opt-in, feature-flagged delivery hint. The app uses the
official FlutterFire `firebase_core` and `firebase_messaging` packages only
when `CCB_MOBILE_PUSH_ENABLED=true`; the default build neither initializes
Firebase nor registers an FCM token. Firebase auto-initialization is disabled
in the Android manifest, so a token is never generated before the paired
profile has opted in.

Firebase configuration is deployment-owned and is intentionally absent from
this repository: no `google-services.json`, generated `firebase_options.dart`,
service-account credential, or sender credential is committed. If private
Android configuration is absent or Firebase initialization fails, push stays
disabled for that process. Pairing, terminal, snapshots, and foreground UI
continue normally.

The client requests notification permission only after it has a paired host
with the existing `notify` scope. A denial is a normal result: it disables
push registration and does not affect foreground notification reconciliation.
Once allowed, the FCM token and every refresh are registered at
`PUT /v1/devices/me/push-token` with the bearer token from that exact paired
host. The gateway derives device identity exclusively from that bearer; it
never accepts a caller-supplied device identity. Re-pairing creates a new
binding; the app does not revoke or mutate an old binding automatically.

The registration protocol is deliberately small:

```json
{"token":"fcm-token"}
```

The gateway stores the FCM token in owner-only private state. Sender data
payloads are exactly `id`, `kind`, `project_id`, `project_short_name`,
`agent`, `completed_at`, and `dedupe_key`; they must not include prompts,
terminal output, paths, errors, credentials, or a device token. For Android
background and terminated delivery, the deployment-owned FCM sender must wrap
those route fields in a real FCM notification+data message; data-only messages
are not accepted as production evidence for background user-visible delivery.
FCM is not an authorization channel: opening a route always uses the stored
paired profile and normal gateway authorization.

On a foreground/background notification click, the app first restores its
stored paired profile, records the push `dedupe_key` in the same seen store
used by the foreground SSE notification stream, and resumes the existing
cursor-based notification catch-up, then loads the requested project and
selects its agent. Invalid, unpaired, stale, or cross-profile routes stop at
the project list. Because the production payload whitelist intentionally omits
host and device identity, identity-free push routes are treated as ambiguous
when the app has more than one stored profile; in that case the app does not
deep-link to a project/agent. Handling a push never submits a prompt, replays
terminal input, retries a mutation, or writes project files. The notification
route is a view-selection request only.

Only the device whose visible target exactly matches the event's project and
agent may be suppressed. Visibility is sent to the gateway as paired-device
presence; it is not a global notification acknowledgement and does not
suppress other devices. Push does not start or retain background SSE/polling.
The current foreground SSE remains a foreground reconciliation aid and is
stopped on backgrounding.

## Threat and dependency review

`firebase_messaging 16.4.1` (Firebase/FlutterFire, BSD-3-Clause) is the
official FCM Flutter client and brings `firebase_core`; `firebase_core
4.11.0` is declared directly so initialization is explicit. Versions are
pinned through `pubspec.lock`. Firebase's Flutter setup requires a
deployment-specific configuration file, while the FCM guide specifies
`getToken`, `onTokenRefresh`, and Android auto-init controls. Those facts are
why configuration and sender credentials stay outside source control and why
the app enables token generation only after paired opt-in.

Dependency audit note: pub.dev listed `firebase_messaging 16.4.2` and
`firebase_core 4.12.0` as current on 2026-07-14, but that pair failed this
project's compile gate because `firebase_messaging` could not resolve
`FirebasePlugin` / `pluginConstants`. The integration therefore keeps the
latest build-passing official FlutterFire pins above until the upstream package
pair is buildable with this Flutter SDK.

The remaining external requirements are: a private Firebase Android app
configuration matching `io.ccb.mobile.ccb_mobile`, a server-side FCM v1 sender
credential, gateway support for the route-only registration endpoint, and a
physical Android device or Google Play emulator test. Until those are supplied,
real delivery is blocked; local tests cover the app protocol and fail-closed
paths only.

## Foreground service decision

No Android foreground service is implemented in this change. The app has no
single native owner for an active terminal or large transfer connection, so a
notification-only service would be misleading and would not make SSE/polling a
valid background delivery mechanism. A future service may be considered only
behind a feature flag after it owns a user-visible active terminal or transfer,
declares an Android-supported service type, has stop/cancel semantics, and is
covered by emulator/device tests. It must not be used for push, idle polling,
or mutation replay.
